"""Operations that are one workflow with a choice in the middle.

ICICI's monthly and annual statements share four steps to reach the download
page, then diverge: annual clicks a radio and picks a financial year, monthly
does not. The compiler emits them as two operations, each replaying the whole
journey from the landing page — which is why every operation needs the browser
returned to the entry page before it runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.compile.browser_skill import fold_candidates  # noqa: E402


def _s(t):
    return {"command": "click_element", "params": {"selector": t}}


PREFIX = [_s("#nav"), _s("#cards"), _s("#past"), _s("#stmt")]
TAIL = [{"command": "list_downloads", "params": {}}]


def _op(name, mid, kind="download"):
    return {"name": name, "kind": kind, "steps": PREFIX + mid + TAIL}


def test_a_shared_prefix_and_ending_is_foldable():
    ops = [_op("monthly", [_s("#HDisplay23")]), _op("annual", [_s("#Annual")])]
    got = fold_candidates(ops)
    assert len(got) == 1
    assert got[0]["operations"] == ["monthly", "annual"]
    assert got[0]["prefix"] == 4
    assert got[0]["suffix"] == 1
    assert [b[0]["params"]["selector"] for b in got[0]["branches"]] == ["#HDisplay23", "#Annual"]


def test_a_short_overlap_is_not_a_fold():
    # Any two operations on one site open with the same nav click. Folding on
    # that would merge workflows with nothing to do with each other.
    a = {"name": "a", "kind": "download", "steps": [_s("#nav"), _s("#x")] + TAIL}
    b = {"name": "b", "kind": "download", "steps": [_s("#nav"), _s("#y")] + TAIL}
    assert fold_candidates([a, b]) == []


def test_operations_of_different_kinds_are_never_folded():
    ops = [_op("read_it", [_s("#a")], kind="read"), _op("download_it", [_s("#b")])]
    assert fold_candidates(ops) == []


def test_no_shared_ending_means_they_do_different_things():
    a = {"name": "a", "kind": "download", "steps": PREFIX + [_s("#x")]}
    b = {"name": "b", "kind": "download", "steps": PREFIX + [_s("#y")]}
    assert fold_candidates([a, b]) == []


def test_identical_operations_are_not_a_fold():
    ops = [_op("a", [_s("#same")]), _op("b", [_s("#same")])]
    assert fold_candidates(ops) == []


def test_the_suffix_never_eats_into_the_prefix():
    # Two operations that are prefix + tail, differing only in length, must not
    # report overlapping regions — the branches would come out negative-length.
    a = _op("a", [])
    b = _op("b", [_s("#extra")])
    got = fold_candidates([a, b])
    assert len(got) == 1
    f = got[0]
    assert f["prefix"] + f["suffix"] <= min(len(a["steps"]), len(b["steps"]))
    assert f["branches"][0] == []
    assert [s["params"]["selector"] for s in f["branches"][1]] == ["#extra"]


# --- applying a fold the member has named --------------------------------------
#
# The names come from a person. "corp_finacle" is the page the annual statement
# happened to be served from; a caller choosing between monthly and annual can
# make nothing of it.

from noui_core.compile.browser_skill import apply_fold  # noqa: E402
from noui_core.verify.replay import substitute_parameters  # noqa: E402


def _folded():
    ops = [_op("monthly", [_s("#HDisplay23")]), _op("annual", [_s("#Annual")])]
    fold = fold_candidates(ops)[0]
    return apply_fold(
        ops, fold, name="download_statement", param="timeframe",
        values=["monthly", "annual"],
    )


def test_the_pair_becomes_one_named_operation():
    out = _folded()
    assert [o["name"] for o in out] == ["download_statement"]
    assert out[0]["parameters"][-1] == {
        "name": "timeframe",
        "default": "monthly",
        "allowed": ["monthly", "annual"],
        "description": "Which variant to run: monthly, annual.",
    }


def test_each_variant_replays_its_own_steps():
    op = _folded()[0]
    def targets(values):
        return [
            (s.get("params") or {}).get("selector")
            for s in substitute_parameters(op, values)
        ]
    # The shared journey runs for both; only the middle differs.
    assert targets({"timeframe": "monthly"}) == ["#nav", "#cards", "#past", "#stmt", "#HDisplay23", None]
    assert targets({"timeframe": "annual"}) == ["#nav", "#cards", "#past", "#stmt", "#Annual", None]


def test_the_default_variant_is_the_first_named():
    op = _folded()[0]
    sels = [(s.get("params") or {}).get("selector") for s in substitute_parameters(op)]
    assert "#HDisplay23" in sels and "#Annual" not in sels


def test_only_the_divergent_steps_carry_a_condition():
    op = _folded()[0]
    conditioned = [s for s in op["steps"] if s.get("when")]
    assert len(conditioned) == 2, "the shared prefix and ending must run for every variant"


def test_a_fold_naming_missing_operations_changes_nothing():
    ops = [_op("monthly", [_s("#a")]), _op("annual", [_s("#b")])]
    bad = {"operations": ["monthly", "gone"], "prefix": 4, "suffix": 1, "branches": [[], []]}
    assert apply_fold(ops, bad, name="x", param="p", values=["a", "b"]) == ops


# --- segments: the hop, not the whole journey ----------------------------------
#
# `steps` repeats the full chain from the landing page into every operation, so
# replaying operation 2 demands being back somewhere the recording left once and
# never returned to. `segment_steps` is only the hop from `starts_from`.


def test_the_compiler_emits_the_hop_alongside_the_journey():
    from noui_core.compile import browser_skill as bs

    pages = bs._resolve_nav_chains([
        {"name": "read_overview", "url": "https://x.test/overview",
         "nav": None, "_from_key": None},
        {"name": "read_cc", "url": "https://x.test/credit-card",
         "nav": [{"text": "Cards"}], "_from_key": bs._page_key("https://x.test/overview")},
        {"name": "read_stmt", "url": "https://x.test/stmt",
         "nav": [{"text": "Past"}], "_from_key": bs._page_key("https://x.test/credit-card")},
    ])
    stmt = [p for p in pages if p["name"] == "read_stmt"][0]
    assert [c["text"] for c in stmt["nav"]] == ["Cards", "Past"]   # the journey
    assert [c["text"] for c in stmt["segment"]] == ["Past"]        # the hop
    assert stmt["starts_from"] == "https://x.test/credit-card"


def test_the_landing_page_starts_from_nothing():
    # A fresh session is already there, so there is no hop to record.
    from noui_core.compile import browser_skill as bs

    pages = bs._resolve_nav_chains([
        {"name": "read_overview", "url": "https://x.test/overview",
         "nav": None, "_from_key": None},
    ])
    assert pages[0]["starts_from"] is None


def test_a_terminal_operation_drops_the_journey_from_its_segment():
    # A download repeats four clicks the previous operation already made. Its
    # segment is what happens ON the statement page: the lead-in, the fills, and
    # the download itself.
    from noui_core.compile import browser_skill as bs

    op = {
        "name": "download_statement",
        "kind": "download",
        "nav": [
            {"text": "Cards", "outcome": {"to_url": "https://x.test/credit-card"}},
            {"text": "Past", "outcome": {"to_url": "https://x.test/stmt"}},
        ],
        "lead": [{"command": "click_by_text", "params": {"text": "Annual"}}],
        "parameters": [],
        "terminal": {"command": "click_element", "params": {"selector": "#DL"}},
    }
    full = bs._steps_for_terminal(op)
    seg = bs._terminal_segment(op)
    # The segment is the full recipe minus exactly the two journey clicks --
    # and it keeps the tail (list_downloads) that confirms the download landed,
    # which a hand-mirrored version dropped.
    assert len(full) == len(seg) + 2, "the journey is what the segment drops"
    assert full[2:] == seg
    assert seg[0]["params"]["text"] == "Annual"
    assert seg[-1]["command"] == "list_downloads"
    assert bs._terminal_starts_from(dict(op, url="https://x.test/stmt")) == "https://x.test/stmt"


def test_a_terminal_with_no_journey_starts_from_the_landing_page():
    from noui_core.compile import browser_skill as bs

    op = {"nav": [], "lead": [], "parameters": [],
          "terminal": {"command": "click_element", "params": {"selector": "#DL"}}}
    assert bs._terminal_starts_from(op) is None
    assert bs._terminal_segment(op) == bs._steps_for_terminal(op)


def test_a_terminal_starts_from_its_own_page_not_the_last_stamped_hop():
    """download_statement came out starting from /credit-card while it runs on
    the statement portal, so its starts_from guard refused it every time.

    A cross-origin hop completes after the click returns, so it is never stamped
    as that click's outcome — and walking the nav chain for one fell back to the
    last hop that WAS stamped. The operation already carries the page it acts on.
    """
    from noui_core.compile import browser_skill as bs

    op = {
        "url": "https://infinity.icici.bank.in/corp/AuthenticationController",
        "nav": [
            {"text": "Cards", "outcome": {"to_url": "https://retailnetbanking.icici.bank.in/credit-card"}},
            {"text": "Past", "outcome": {"navigated": False, "to_url": None}},
        ],
        "lead": [], "parameters": [],
        "terminal": {"command": "click_element", "params": {"selector": "#DL"}},
    }
    assert bs._terminal_starts_from(op) == "https://infinity.icici.bank.in/corp/AuthenticationController"


def test_the_segment_is_folded_too_not_just_the_full_steps():
    """A segmented replay runs segment_steps. Leaving it as operation A's copy
    meant `timeframe=annual` ran the monthly branch — the skill offered a choice
    it could not honour, which is worse than not folding at all.
    """
    from noui_core.compile.browser_skill import apply_fold, fold_candidates
    from noui_core.verify.replay import substitute_parameters

    ops = [
        {**_op("monthly", [_s("#HDisplay23")]),
         "segment_steps": [_s("#HDisplay23"), _s("#DL")], "starts_from": "https://x/p"},
        {**_op("annual", [_s("#Annual"), _s("#GO")]),
         "segment_steps": [_s("#Annual"), _s("#GO"), _s("#DL")], "starts_from": "https://x/p"},
    ]
    fold = fold_candidates(ops)[0]
    out = apply_fold(ops, fold, name="download_statement", param="timeframe",
                     values=["monthly", "annual"])[0]

    def seg(tf):
        return [
            (s.get("params") or {}).get("selector")
            for s in substitute_parameters({**out, "steps": out["segment_steps"]},
                                           {"timeframe": tf})
        ]

    assert seg("monthly") == ["#HDisplay23", "#DL"]
    assert seg("annual") == ["#Annual", "#GO", "#DL"]
    assert "#Annual" not in seg("monthly"), "branches must not leak into each other"


def test_the_terminal_asserts_the_page_it_was_recorded_on():
    """ICICI's annual and monthly statement pages are identically designed.

    #PDF_Download and #DOWNLOAD_ESTATEMENT_PDF exist on both, so the monthly
    steps ran on the annual page and returned a MONTHLY statement with every
    check green — selector checks cannot tell the pages apart. Their URLs can.
    """
    from noui_core.compile import browser_skill as bs

    op = {
        "url": "https://infinity.icici.bank.in/corp/Finacle",
        "work_url": "https://infinity.icici.bank.in/corp/Finacle",
        "nav": [], "lead": [], "parameters": [],
        "terminal": {"command": "click_element", "params": {"selector": "#DL"}},
    }
    steps = bs._steps_for_terminal(op)
    dl = [s for s in steps if (s.get("params") or {}).get("selector") == "#DL"][0]
    assert dl["expect"]["url"] == "https://infinity.icici.bank.in/corp/Finacle"


def test_a_recorded_expectation_is_not_overwritten():
    from noui_core.compile import browser_skill as bs

    op = {
        "url": "https://x/page", "nav": [], "lead": [], "parameters": [],
        "terminal": {"command": "click_element", "params": {"selector": "#DL"},
                     "expect": {"url": "https://x/observed", "download": True}},
    }
    dl = [s for s in bs._steps_for_terminal(op)
          if (s.get("params") or {}).get("selector") == "#DL"][0]
    assert dl["expect"]["url"] == "https://x/observed", "what was observed wins"
    assert dl["expect"]["download"] is True
