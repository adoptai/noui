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


def test_a_branch_keeps_the_journey_only_it_takes():
    """A step can both navigate and belong to one branch.

    ICICI's annual statement is selected by a radio and a GO press — and that
    submit navigates, so both were classified as journey and stripped from the
    segment. Nothing else performed them: the monthly branch diverges before,
    and no operation sits between. So timeframe=annual ran the MONTHLY steps on
    the monthly page and returned a monthly statement, with every check green.
    """
    from noui_core.compile.browser_skill import apply_fold, fold_candidates
    from noui_core.verify.replay import substitute_parameters

    to_fork = [_s("#nav"), _s("#past"), _s("#stmt")]
    monthly = {**_op("monthly", [_s("#DL")]),
               "steps": to_fork + [_s("#DL")] + TAIL,
               "segment_steps": [_s("#DL")] + TAIL, "starts_from": "https://x/fork"}
    annual = {**_op("annual", [_s("#DL")]),
              # its journey continues past the fork: radio, then GO (navigates)
              "steps": to_fork + [_s("#Annual"), _s("#GO")] + [_s("#year"), _s("#DL")] + TAIL,
              "segment_steps": [_s("#year"), _s("#DL")] + TAIL,
              "starts_from": "https://x/after-go"}

    fold = fold_candidates([monthly, annual])[0]
    out = apply_fold([monthly, annual], fold, name="download_statement",
                     param="timeframe", values=["monthly", "annual"])[0]

    def seg(tf):
        return [
            (s.get("params") or {}).get("selector")
            for s in substitute_parameters({**out, "steps": out["segment_steps"]},
                                           {"timeframe": tf})
        ]

    assert "#Annual" in seg("annual"), "the branch must select itself"
    assert "#GO" in seg("annual")
    assert "#Annual" not in seg("monthly")
    # The way to the fork stays out of both — the operation before walks it.
    assert "#past" not in seg("annual") and "#past" not in seg("monthly")


def test_a_radio_with_no_unique_selector_is_set_by_role_and_name():
    """ICICI's Monthly and Annual radios share an id AND a name.

    No selector picks one of them, so the step fell back to clicking the label
    text: it reported success while the form stayed on Monthly, and the replay
    downloaded a monthly statement while asking for the annual one. The only
    unique handle the recorder captured was `role_name: radio|Annual`.
    """
    from noui_core.compile.browser_skill import _step_for_click

    step = _step_for_click({
        "element": {"role": "radio"},
        "candidates": [
            {"kind": "id", "value": "#PERIOD_TYPE", "match_count": 2},
            {"kind": "name", "value": 'input[name="PERIOD_TYPE"]', "match_count": 2},
            {"kind": "role_name", "value": "radio|Annual", "match_count": 1},
        ],
    })
    assert step["command"] == "set_checked"
    assert step["params"] == {"role": "radio", "name": "Annual", "checked": True}


def test_a_unique_selector_is_still_preferred():
    from noui_core.compile.browser_skill import _step_for_click

    step = _step_for_click({
        "element": {"role": "radio"},
        "candidates": [
            {"kind": "id", "value": "#annual", "match_count": 1},
            {"kind": "role_name", "value": "radio|Annual", "match_count": 1},
        ],
    })
    assert step["params"]["selector"] == "#annual"


def test_a_completed_download_ends_the_previous_variant():
    """The human downloaded the MONTHLY statement, then switched to Annual and
    downloaded again.

    The clicks between the fork and the annual terminal therefore include the
    monthly download that ENDED the previous variant. Handed to the annual
    branch it fired a monthly download first, left the form busy — the bank
    answered "the system is considering your first request" — and the annual
    steps had nothing to act on.
    """
    from noui_core.compile.browser_skill import _after_last_terminal

    steps = [
        {"command": "click_element", "params": {"selector": "#PDF_Download"},
         "expect": {"download": True}},
        {"command": "set_checked", "params": {"role": "radio", "name": "Annual"}},
        {"command": "click_element", "params": {"selector": "#GO"}},
    ]
    kept = [(s.get("params") or {}).get("selector") or (s.get("params") or {}).get("name")
            for s in _after_last_terminal(steps)]
    assert kept == ["Annual", "#GO"]


def test_steps_with_no_terminal_are_left_alone():
    from noui_core.compile.browser_skill import _after_last_terminal

    steps = [{"command": "click_by_text", "params": {"text": "Past"}},
             {"command": "click_element", "params": {"selector": "#x"}}]
    assert _after_last_terminal(steps) == steps


def test_only_the_last_terminal_bounds_it():
    # Two completed downloads before the fork: everything up to the later one
    # belongs to variants already finished.
    from noui_core.compile.browser_skill import _after_last_terminal

    steps = [
        {"command": "click_element", "params": {"selector": "#a"}, "expect": {"download": True}},
        {"command": "click_element", "params": {"selector": "#b"}},
        {"command": "list_downloads", "params": {}},
        {"command": "click_element", "params": {"selector": "#keep"}},
    ]
    assert [(s.get("params") or {}).get("selector") for s in _after_last_terminal(steps)] == ["#keep"]


def test_a_terminal_the_steps_cannot_show_still_bounds_the_branch():
    """The real ICICI boundary is invisible in the steps.

    The monthly download was reported against the `submit` that the download
    click triggered, and a submit is not a replayable step — so no step in the
    annual journey carries `expect.download`, and comparing the steps finds
    nothing. Recorded order is the only witness: `_seq` says when each step
    happened, the monthly operation says when it finished, and everything at or
    before that moment was its work.
    """
    from noui_core.compile.browser_skill import _after_last_terminal

    steps = [
        {"command": "click_element", "params": {"selector": "#PDF_Download"}, "_seq": 20},
        {"command": "set_checked", "params": {"role": "radio", "name": "Annual"}, "_seq": 23},
        {"command": "click_element", "params": {"selector": "#DUMMY1"}, "_seq": 24},
    ]
    kept = [(s.get("params") or {}).get("selector") or (s.get("params") or {}).get("name")
            for s in _after_last_terminal(steps, boundary_seq=22)]
    assert kept == ["Annual", "#DUMMY1"]


def test_a_terminal_that_has_not_happened_yet_bounds_nothing():
    """Applied backwards this rule deletes the earlier branch entirely.

    The monthly branch must not be trimmed by the annual download that comes
    after it: every one of its steps precedes that moment, so the whole branch
    would vanish and the operation would report success having done nothing.
    """
    from noui_core.compile.browser_skill import _preceding_terminal

    assert _preceding_terminal(22, 40) is None      # monthly bounded by annual: no
    assert _preceding_terminal(40, 22) == 22        # annual bounded by monthly: yes
    assert _preceding_terminal(22, 22) is None      # one operation is not its own boundary
    assert _preceding_terminal(None, 22) is None
    assert _preceding_terminal(40, None) is None


def test_provenance_never_makes_two_identical_actions_differ():
    """`_seq` records where a step came from, not what it does.

    Monthly and annual both end by clicking Download, from different clicks. If
    the recorded moment counted as part of the step, that shared ending would
    stop being recognised as shared and be duplicated into both branches.
    """
    from noui_core.compile.browser_skill import _common_prefix_len, _common_suffix_len

    a = [{"command": "click_element", "params": {"selector": "#D"}, "_seq": 21}]
    b = [{"command": "click_element", "params": {"selector": "#D"}, "_seq": 39}]
    assert _common_prefix_len(a, b) == 1
    assert _common_suffix_len(a, b, 0) == 1


def _pair():
    """The monthly/annual pair, shaped like the real fold."""
    to_fork = [_s("#nav"), _s("#past"), _s("#stmt")]
    monthly = {**_op("monthly", [_s("#DL")]),
               "steps": to_fork + [_s("#DL")] + TAIL,
               "segment_steps": [_s("#DL")] + TAIL}
    annual = {**_op("annual", [_s("#DL")]),
              "steps": to_fork + [_s("#Annual"), _s("#GO")] + [_s("#year"), _s("#DL")] + TAIL,
              "segment_steps": [_s("#year"), _s("#DL")] + TAIL}
    return monthly, annual

def test_each_variant_starts_where_its_own_work_happens():
    """A folded operation's variants need not share a page.

    ICICI produces the monthly statement on /corp/AuthenticationController;
    choosing Annual navigates to /corp/Finacle. The folded operation inherited
    the monthly page as its single precondition, so the annual variant's guard
    refused it while standing on exactly the page it was supposed to run on.
    """
    from noui_core.compile.browser_skill import apply_fold, fold_candidates

    monthly, annual = _pair()
    monthly["starts_from"] = "https://p.test/corp/AuthenticationController"
    annual["starts_from"] = "https://p.test/corp/Finacle"

    fold = fold_candidates([monthly, annual])[0]
    out = apply_fold([monthly, annual], fold, name="download_statement",
                     param="timeframe", values=["monthly", "annual"])
    op = [o for o in out if o["name"] == "download_statement"][0]

    assert op["starts_from_by"] == {
        "param": "timeframe",
        "by": {
            "monthly": "https://p.test/corp/AuthenticationController",
            "annual": "https://p.test/corp/Finacle",
        },
    }


def test_variants_that_share_a_page_carry_no_override():
    """Nothing changes for a fold whose variants begin in the same place."""
    from noui_core.compile.browser_skill import apply_fold, fold_candidates

    monthly, annual = _pair()
    monthly["starts_from"] = annual["starts_from"] = "https://p.test/same"

    fold = fold_candidates([monthly, annual])[0]
    out = apply_fold([monthly, annual], fold, name="d", param="timeframe",
                     values=["monthly", "annual"])
    op = [o for o in out if o["name"] == "d"][0]
    assert "starts_from_by" not in op
