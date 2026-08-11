"""A segmented replay runs the recorded journey once, in order.

`steps` repeats the whole chain from the entry page into every operation, so
replaying operation 2 demanded being back somewhere the recording left once and
never returned to — and the reset had to invent a route there. Watched failing
on live ICICI: operations 2-5 reported "could not return to the start of the
journey" while the browser sat on /credit-card with the next recorded click
visible on screen.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.verify import session as session_mod  # noqa: E402
from noui_core.verify.session import run_replay  # noqa: E402

# The wrong-page case waits for a hop that never lands; a real wait would add
# eight seconds to the suite for no signal.
session_mod._STARTS_FROM_WAIT_S = 0

OVERVIEW = "https://x.test/overview"
CC = "https://x.test/credit-card"


def _op(name, starts_from, seg, full):
    return {
        "name": name,
        "tool": "call_web_browser",
        "starts_from": starts_from,
        "segment_steps": seg,
        "steps": full,
    }


OPS = [
    _op("read_cc", None,
        [{"command": "click_by_text", "params": {"text": "Cards"}}],
        [{"command": "click_by_text", "params": {"text": "Cards"}}]),
    _op("download", CC,
        [{"command": "click_element", "params": {"selector": "#DL"}}],
        [{"command": "click_by_text", "params": {"text": "Cards"}},
         {"command": "click_element", "params": {"selector": "#DL"}}]),
]


class _Page:
    def __init__(self, at, after_first=None):
        self.at = at
        self.after_first = after_first
        self.calls: list[str] = []

    def __call__(self, command, params):
        self.calls.append(command)
        if command == "get_page_info":
            return {"data": {"url": self.at}}
        if command == "click_by_text" and self.after_first:
            self.at = self.after_first
        return {"data": {}}


def _run(page, **kw):
    return run_replay(OPS, profile_slug="p", token="t", entry_url=OVERVIEW, **kw)


def test_the_second_operation_does_not_re_walk_the_journey(monkeypatch):
    page = _Page(OVERVIEW, after_first=CC)
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)
    _run(page)
    # "Cards" is clicked ONCE — by the operation that owns it, not again by the
    # download that follows it.
    assert page.calls.count("click_by_text") == 1
    assert page.calls.count("click_element") == 1


def test_a_predecessor_that_did_not_arrive_is_reported_not_replayed(monkeypatch):
    # The first operation leaves the browser somewhere unexpected, so the second
    # must refuse rather than act on the wrong page.
    page = _Page(OVERVIEW, after_first="https://x.test/somewhere-else")
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)
    report = _run(page)
    dl = [o for o in report["operations"] if o["name"] == "download"][0]
    assert dl["steps"][0]["status"] == "blocked"
    assert "did not arrive" in dl["steps"][0]["error"]
    assert page.calls.count("click_element") == 0, "must not act on the wrong page"


def test_a_skill_without_segments_runs_as_it_always_did(monkeypatch):
    page = _Page(OVERVIEW, after_first=CC)
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)
    legacy = [{k: v for k, v in o.items() if k not in ("segment_steps", "starts_from")}
              for o in OPS]
    run_replay(legacy, profile_slug="p", token="t", entry_url=OVERVIEW)
    # The full chain runs, so "Cards" is clicked by both operations.
    assert page.calls.count("click_by_text") == 2


def test_an_arrival_is_given_time_to_paint(monkeypatch):
    """The URL matches before the DOM is up.

    ICICI's statement portal is reached by a cross-origin hop that completes
    after the click returns. The next operation asked for #PDF_Download while
    the form was still building; probed by hand a minute later it was there.
    """
    waited: list[float] = []
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: waited.append(s))
    page = _Page(OVERVIEW, after_first=CC)
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)

    nav = {"command": "click_by_text", "params": {"text": "Cards"},
           "expect": {"settle_ms": 4000}}
    dl = {"command": "click_element", "params": {"selector": "#DL"}}
    ops = [
        _op("read_cc", None, [nav], [nav]),
        _op("download", CC, [dl], [nav, dl]),
    ]
    run_replay(ops, profile_slug="p", token="t", entry_url=OVERVIEW)
    # The second operation arrived at its starts_from and waited before acting.
    assert 4.0 in waited


def test_no_wait_when_the_predecessor_did_not_arrive(monkeypatch):
    # A blocked operation must not also pay the settle.
    waited: list[float] = []
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: waited.append(s))
    page = _Page(OVERVIEW, after_first="https://x.test/elsewhere")
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)
    run_replay(OPS, profile_slug="p", token="t", entry_url=OVERVIEW)
    assert waited == []
