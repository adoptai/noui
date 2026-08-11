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
