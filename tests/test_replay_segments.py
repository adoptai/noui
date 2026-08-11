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


def test_an_arrival_waits_for_its_first_control(monkeypatch):
    """Poll for the thing we are about to click, not a fixed time.

    A fixed sleep was wrong twice: 3s was too short for ICICI's statement portal
    (the control was there when probed by hand a minute later) and dead weight on
    every fast page. The recording says the human's own gap after that hop was
    17-29s — an upper bound, since some of it was reading.
    """
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: None)
    asked: list[str] = []

    class _Waits(_Page):
        def __call__(self, command, params):
            if command == "wait_for_selector":
                asked.append(params["selector"])
                return {"data": {}}
            return super().__call__(command, params)

    page = _Waits(OVERVIEW, after_first=CC)
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)
    run_replay(OPS, profile_slug="p", token="t", entry_url=OVERVIEW)
    # It waited for the download's own target before clicking it.
    assert asked == ["#DL"]


def test_a_slow_control_is_polled_until_the_budget_runs_out(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: slept.append(s))
    ticks = iter([0.0, 1.0, 2.0, 99.0])
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(ticks, 99.0))

    def never_there(command, params):
        if command == "wait_for_selector":
            raise RuntimeError("nothing matches")
        return {"data": {"url": CC}}

    session_mod._await_first_control(
        never_there, [{"params": {"selector": "#DL"}}], 5.0
    )
    # Polled, then gave up rather than hanging — the step itself reports next.
    assert slept and all(s == 1.0 for s in slept)


def test_a_step_with_no_selector_cannot_be_polled_for(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: slept.append(s))

    def boom(command, params):
        raise AssertionError("must not probe without a selector")

    session_mod._await_first_control(boom, [{"params": {"text": "Annual"}}], 5.0)
    assert slept, "falls back to a short settle"


def test_no_wait_when_the_predecessor_did_not_arrive(monkeypatch):
    # A blocked operation must not also pay the settle.
    #
    # Held apart from the live-page stabiliser, which is a different mechanism
    # with its own test: that one polls after a step that RAN, and a blocked
    # operation never runs one.
    waited: list[float] = []
    monkeypatch.setattr(session_mod, "_wait_until_the_page_stops_moving", lambda execute: None)
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: waited.append(s))
    page = _Page(OVERVIEW, after_first="https://x.test/elsewhere")
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)
    run_replay(OPS, profile_slug="p", token="t", entry_url=OVERVIEW)
    assert waited == []


def test_the_arrival_budget_comes_from_the_recording(monkeypatch):
    """The human's own gap, buffered — not a constant pretending to be one.

    On ICICI the gap after the hop to the statement portal was 17.2s; a 3s
    constant gave up long before the form appeared, and a flat 20s was under the
    29s the human took elsewhere.
    """
    seen: list[float] = []
    monkeypatch.setattr(
        session_mod, "_await_first_control",
        lambda ex, steps, budget: seen.append(budget),
    )
    page = _Page(OVERVIEW, after_first=CC)
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)

    ops = [dict(OPS[0], causes_arrival_budget_ms=25819), OPS[1]]
    run_replay(ops, profile_slug="p", token="t", entry_url=OVERVIEW)
    assert seen == [25.819]


def test_a_skill_without_a_recorded_budget_uses_the_constant(monkeypatch):
    seen: list[float] = []
    monkeypatch.setattr(
        session_mod, "_await_first_control",
        lambda ex, steps, budget: seen.append(budget),
    )
    page = _Page(OVERVIEW, after_first=CC)
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)
    run_replay(OPS, profile_slug="p", token="t", entry_url=OVERVIEW)
    assert seen == [session_mod._ARRIVAL_BUDGET_S]


def test_the_nav_projection_carries_seq_for_the_budget():
    """Measured through the REAL path, not a hand-built fixture.

    The unit test for the budget fed causes_arrival_budget_ms in directly and
    passed while the compile path produced None for every operation --
    _nav_clicks_for's projection was dropping `seq`, the fourth field it has
    been caught losing after candidates, is_opener and event_type.
    """
    import json as _json
    from pathlib import Path as _Path

    from noui_core.compile import browser_skill as bs

    bundle = _Path(__file__).resolve().parents[1] / "skills/noui/workbench/bundles"
    found = sorted(bundle.glob("icici-cc-hover-*.json"))
    if not found:
        return  # bundle not present in this checkout
    b = _json.loads(found[0].read_text())
    nav_ev = [e for e in b["url_events"] if "credit-card" in (e.get("to_url") or "")][0]
    nav = bs._nav_clicks_for(nav_ev["from_url"], nav_ev, b["click_events"])
    assert all(n.get("seq") is not None for n in nav), "seq must survive the projection"
    assert bs.arrival_budget_ms(b["click_events"], nav[-1]["seq"]) > 0


def test_an_instant_human_does_not_become_an_instant_budget():
    """4ms between two clicks became a 6ms budget, and the next operation
    failed its starts_from with no time to arrive.

    A human clicking straight through says nothing about how fast the page was:
    they may have known where to aim, or the control was already there.
    """
    from noui_core.compile import browser_skill as bs

    events = [
        {"seq": 1, "timestamp": "2026-08-10T18:00:00.000Z"},
        {"seq": 2, "timestamp": "2026-08-10T18:00:00.004Z"},
    ]
    assert bs.arrival_budget_ms(events, 1) == bs._ARRIVAL_FLOOR_MS


def test_a_long_pause_is_capped():
    from noui_core.compile import browser_skill as bs

    events = [
        {"seq": 1, "timestamp": "2026-08-10T18:00:00.000Z"},
        {"seq": 2, "timestamp": "2026-08-10T18:05:00.000Z"},
    ]
    assert bs.arrival_budget_ms(events, 1) == bs._ARRIVAL_CAP_MS


def test_operation_zero_resets_to_the_journey_entry_not_the_host_entry(monkeypatch):
    """A replay that ended on the statement portal must still be repeatable.

    Per-origin is right for an operation that starts mid-journey. For the first
    one it meant "home" resolved to the portal's own entry, the reset did
    nothing, and the net-banking steps ran against the wrong host — which is why
    runs alternated pass/fail with no change between them.
    """
    PORTAL = "https://infinity.icici.bank.in/corp/AuthenticationController"
    aimed: list[str] = []

    def fake_reset(execute, entry, known=None, by_origin=None, ops=None, notes=None):
        aimed.append(entry)
        assert by_origin is None, "operation 0 must not be redirected per host"
        return None

    monkeypatch.setattr(session_mod, "_return_to_entry", fake_reset)
    page = _Page(PORTAL, after_first=CC)
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)

    run_replay(
        OPS, profile_slug="p", token="t", entry_url=OVERVIEW,
        entry_by_origin={"https://infinity.icici.bank.in": PORTAL,
                         "https://retailnetbanking.icici.bank.in": OVERVIEW},
    )
    assert aimed == [OVERVIEW]


def test_a_mid_journey_operation_run_alone_is_not_sent_to_the_entry(monkeypatch):
    """--only made download_statement index 0, and the index-based rule sent it
    back to /overview from the statement portal it had just reached.

    Beginning the journey is a property of the operation — no starts_from —
    not of its position in the list.
    """
    reset_called: list[str] = []
    monkeypatch.setattr(
        session_mod, "_return_to_entry",
        lambda *a, **k: reset_called.append(a[1]) or None,
    )
    page = _Page(CC)
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)

    only = [OPS[1], dict(OPS[1], name="second")]  # both carry starts_from=CC
    run_replay(only, profile_slug="p", token="t", entry_url=OVERVIEW)
    assert reset_called == [], "a continuing operation must never reset"


def test_the_journey_opener_still_resets_wherever_it_sits(monkeypatch):
    reset_called: list[str] = []
    monkeypatch.setattr(
        session_mod, "_return_to_entry",
        lambda *a, **k: reset_called.append(a[1]) or None,
    )
    page = _Page(OVERVIEW, after_first=CC)
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)
    run_replay(OPS, profile_slug="p", token="t", entry_url=OVERVIEW)
    assert reset_called == [OVERVIEW]


def test_a_single_operation_run_still_uses_segments(monkeypatch):
    # `len(ops) > 1` silently undid segments the moment --only narrowed a run.
    reset_called: list = []
    monkeypatch.setattr(
        session_mod, "_return_to_entry",
        lambda *a, **k: reset_called.append(a[1]) or None,
    )
    page = _Page(CC)
    monkeypatch.setattr("noui_core.verify.session._executor", lambda *a, **k: page)
    run_replay([OPS[1]], profile_slug="p", token="t", entry_url=OVERVIEW)
    assert reset_called == [], "one continuing operation must not be reset"


def test_a_jsessionid_does_not_make_it_a_different_page():
    """ICICI's portal carries its session as ;jsessionid=… in the path.

    The guard stripped only `?`, so the page the browser was on and the page the
    recording named compared as different — blocking the one operation being
    tested, on the page it had correctly reached.
    """
    portal = "https://infinity.icici.bank.in/corp/AuthenticationController"
    assert session_mod._same_page(portal + ";jsessionid=0000HU:abc", portal)
    assert session_mod._same_page(portal + ";jsessionid=x?bwayparam=y", portal)
    assert session_mod._same_page(portal + "/", portal)


def test_different_pages_are_still_different():
    a = "https://retailnetbanking.icici.bank.in/overview"
    b = "https://retailnetbanking.icici.bank.in/credit-card"
    assert not session_mod._same_page(a, b)
    assert not session_mod._same_page(a + ";jsessionid=1", b + ";jsessionid=1")


def test_a_step_is_given_time_to_be_processed_before_the_next(monkeypatch):
    """ICICI answered: "You clicked on a link or a button when your previous
    click was still under process. The system is considering your first
    request." — and kept the FIRST request.

    The replay had set the Annual radio, pressed GO, and clicked on immediately;
    the bank discarded the second action and stayed on Monthly. The recorder's
    settle for a step is equally the answer to how long it took to be processed,
    because the human did not act again until it was.
    """
    slept: list[float] = []
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: slept.append(s))

    session_mod._let_the_step_land({"expect": {"settle_ms": 4000}})
    assert slept == [4.0]

    slept.clear()
    session_mod._let_the_step_land({"expect": {"settle_ms": 90_000}})
    assert slept == [session_mod._INTER_STEP_CAP_S], "a human reading is not a page working"

    slept.clear()
    session_mod._let_the_step_land({"command": "click_element"})
    assert slept == [], "nothing measured, nothing waited"


def test_a_step_with_no_recorded_settle_waits_for_the_page_to_stop_moving(monkeypatch):
    """ICICI's portal reports nothing, so the wait has to be observed.

    Every click on the statement portal records `navigated: false, to_url: null,
    settled_ms: null` -- the hop is cross-origin and completes after the click
    returns. So no step there carries a settle, the replay fired the next one
    into a page mid-load, and the portal treats that as a double click and
    expires the session. Two readings that agree is the cheapest honest proof
    that the click has been processed.
    """
    urls = iter(["https://p.test/a", "https://p.test/b", "https://p.test/b"])
    seen: list[str] = []

    def execute(command, params=None):
        assert command == "get_page_info"
        url = next(urls)
        seen.append(url)
        return {"data": {"url": url}}

    monkeypatch.setattr(session_mod.time, "sleep", lambda s: None)
    session_mod._wait_until_the_page_stops_moving(execute)

    # Stopped as soon as two readings agreed, and not before.
    assert seen == ["https://p.test/a", "https://p.test/b", "https://p.test/b"]


def test_the_stabiliser_gives_up_rather_than_hanging(monkeypatch):
    """A page that never settles must not hold the whole replay.

    The step itself waits for its own control and reports what it finds, which
    is a better error than a timeout with no context.
    """
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def always_moving(command, params=None):
        calls["n"] += 1
        return {"data": {"url": f"https://p.test/{calls['n']}"}}

    session_mod._wait_until_the_page_stops_moving(always_moving)
    assert calls["n"] > 1  # it did try


def test_operations_are_emitted_in_the_order_they_were_recorded():
    """These operations are one journey cut into pieces, so list order IS replay order.

    ICICI's portal sign-in was recorded at 18 and the statement-page read at
    20-24, but every page operation was emitted before every terminal one — so
    the sign-in landed after a read that had already navigated past the page the
    sign-in runs on, and could only ever fail its own starts_from guard.
    """
    from noui_core.compile.browser_skill import _recorded_position

    # segment_steps is the operation's OWN work; `steps` prefixes the shared
    # journey, whose first click is identical for every operation.
    journey = [{"_seq": 9}]
    read_cards = {"name": "read_credit_card", "steps": journey,
                  "segment_steps": [{"_seq": 9}]}
    read_portal = {"name": "read_corp_auth", "steps": journey + [{"_seq": 11}],
                   "segment_steps": [{"_seq": 11}, {"_seq": 12}]}
    read_finacle = {"name": "read_corp_finacle", "steps": journey + [{"_seq": 20}],
                    "segment_steps": [{"_seq": 20}, {"_seq": 23}]}
    submit = {"name": "submit_portal_login", "_terminal_seq": 18,
              "steps": journey, "segment_steps": [{}]}
    download = {"name": "download_statement", "_terminal_seq": 22,
                "steps": journey, "segment_steps": [{"_seq": 23}]}

    ops = [read_cards, read_portal, read_finacle, submit, download]
    ops.sort(key=_recorded_position)

    assert [o["name"] for o in ops] == [
        "read_credit_card",
        "read_corp_auth",
        "submit_portal_login",     # 18 — before the read that navigates past it
        "read_corp_finacle",       # 20
        "download_statement",      # 22
    ]


def test_an_operation_the_recording_cannot_place_keeps_its_position():
    """A stable sort leaves it alone rather than moving it somewhere arbitrary."""
    from noui_core.compile.browser_skill import _recorded_position

    placed = {"name": "placed", "segment_steps": [{"_seq": 5}]}
    unplaced = {"name": "unplaced", "segment_steps": [{}]}
    ops = [placed, unplaced]
    ops.sort(key=_recorded_position)
    assert [o["name"] for o in ops] == ["placed", "unplaced"]
    assert _recorded_position(unplaced) == float("inf")


def test_a_terminals_position_is_the_earliest_of_what_it_touches():
    """Its lead-in steps carry no position; its terminal does."""
    from noui_core.compile.browser_skill import _recorded_position

    assert _recorded_position(
        {"_terminal_seq": 22, "segment_steps": [{"_seq": 23}, {"_seq": 24}]}) == 22
    assert _recorded_position(
        {"_terminal_seq": 40, "segment_steps": [{"_seq": 23}]}) == 23


def test_a_page_that_has_not_finished_loading_is_not_somewhere_to_click(monkeypatch):
    """Clicking a page mid-load is not a faster click, it is a lost one.

    Watching the URL alone is too weak for a form postback: ICICI's statement
    portal re-renders in place, so two readings agree immediately and the wait
    collapsed to nothing. Seen on screen — the replay chose FY2025-26, the portal
    was still processing the previous click, and the page came back showing
    FY2024-25 with "we are unable to process your request". The step reported ok.
    """
    states = iter([("u", "loading"), ("u", "loading"), ("u", "complete")])
    seen = []

    def execute(command, params=None):
        url, ready = next(states)
        seen.append(ready)
        return {"data": {"url": url, "ready_state": ready}}

    monkeypatch.setattr(session_mod.time, "sleep", lambda s: None)
    session_mod._wait_until_the_page_stops_moving(execute)

    # It kept looking while the page said "loading", and stopped once complete.
    assert seen == ["loading", "loading", "complete"]


def test_a_worker_that_reports_no_readiness_behaves_as_before(monkeypatch):
    """Older workers return {url, title} only; the URL is then all there is."""
    urls = iter(["a", "b", "b"])

    def execute(command, params=None):
        return {"data": {"url": next(urls)}}

    monkeypatch.setattr(session_mod.time, "sleep", lambda s: None)
    session_mod._wait_until_the_page_stops_moving(execute)   # returns, does not hang
