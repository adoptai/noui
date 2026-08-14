"""A replay restarts the recorded journey; it does not resume it.

Observed on ICICI (run 050970d3): the first replay died mid-journey, an
amendment fixed the step it died on, and the re-run began on
`/credit-card/add-card` -- where the browser had been abandoned. The early steps
that had already passed then matched nothing, so the amendment looked no better
than the selector it replaced. The session is deliberately kept warm between
replays, which is what leaves the browser somewhere.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.verify import session as session_mod  # noqa: E402
from noui_core.verify.replay import SessionNotReadyError  # noqa: E402

# Tests drive fakes: a real settle would add seconds per case for no signal.
session_mod._RESET_SETTLE_MS = 0

ENTRY = "https://retailnetbanking.icici.bank.in/overview"
DRIFTED = "https://retailnetbanking.icici.bank.in/credit-card/add-card"


class _Browser:
    """Records what replay asked of the page, and where the page says it is."""

    def __init__(self, at: str, *, navigate_fails: bool = False):
        self.at = at
        self.navigate_fails = navigate_fails
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, command: str, params: dict) -> dict:
        self.calls.append((command, params))
        if command == "get_page_info":
            return {"data": {"url": self.at}}
        if command == "navigate":
            if self.navigate_fails:
                raise RuntimeError("net::ERR_ABORTED")
            self.at = params["url"]
            return {"data": {}}
        return {"data": {}}

    @property
    def commands(self) -> list[str]:
        return [c for c, _ in self.calls]


def test_drifted_browser_is_returned_to_the_entry_point():
    b = _Browser(DRIFTED)
    assert session_mod._return_to_entry(b, ENTRY) is None
    # History is tried first — same tab, same cookies — then navigate.
    assert b.commands == ["get_page_info", "navigate"]
    assert b.at == ENTRY


def test_already_at_the_entry_point_costs_no_page_load():
    # A fresh session lands here. Reloading would only spend time and risk
    # re-running whatever the landing page does on load.
    b = _Browser(ENTRY)
    assert session_mod._return_to_entry(b, ENTRY) is None
    assert b.commands == ["get_page_info"]


def test_trailing_slash_is_not_a_different_page():
    b = _Browser(ENTRY + "/")
    assert session_mod._return_to_entry(b, ENTRY) is None
    assert b.commands == ["get_page_info"]


def test_unreadable_url_navigates_rather_than_guessing():
    class _Blind(_Browser):
        def __call__(self, command, params):
            if command == "get_page_info":
                self.calls.append((command, params))
                raise RuntimeError("page closed")
            return super().__call__(command, params)

    b = _Blind(DRIFTED)
    assert session_mod._return_to_entry(b, ENTRY) is None
    # History is tried first — same tab, same cookies — then navigate.
    assert b.commands == ["get_page_info", "navigate"]


def test_failed_reset_is_reported_as_the_reset_not_as_step_zero():
    b = _Browser(DRIFTED, navigate_fails=True)
    failed = session_mod._return_to_entry(b, ENTRY)
    assert failed is not None
    assert failed["command"] == "return_to_entry"
    assert failed["status"] == "blocked"
    # The report must say where the browser actually was — that is the fact that
    # explains every step failure underneath it.
    assert DRIFTED in failed["error"]
    assert ENTRY in failed["error"]


def test_no_session_during_reset_stays_a_login_prompt():
    # Not a blocked step: there is nothing to blame the plan for.
    def execute(command, params):
        raise SessionNotReadyError("HTTP 409 from POST /execute/browser")

    try:
        session_mod._return_to_entry(execute, ENTRY)
    except SessionNotReadyError:
        return
    raise AssertionError("expected SessionNotReadyError to propagate")


LOGIN = "https://retailnetbanking.icici.bank.in/login-page"
CC = "https://retailnetbanking.icici.bank.in/credit-card"

# The real transitions from bundle icici-cc-stmt-local-8f23a0f8.
URL_EVENTS = [
    {"seq": 1, "from_url": "about:blank", "to_url": LOGIN},
    {"seq": 3, "from_url": LOGIN, "to_url": ENTRY},
    {"seq": 4, "from_url": ENTRY, "to_url": CC},
]


def test_the_entry_is_the_page_the_first_step_acts_on_not_the_first_page():
    """The bug this file's fix shipped with.

    This recording's split kept no /overview page, so pages[0] is /credit-card
    -- a DESTINATION. Every operation's step 0 is `click_by_text "Credit Cards"`,
    a click you make FROM /overview. Stamping /credit-card would reset the
    browser to the page the first click is supposed to reach.
    """
    from noui_core.compile import browser_skill

    pages = [{"name": "read_credit_card", "url": CC, "nav": [{"text": "Credit Cards"}]}]
    assert browser_skill.entry_url_for(URL_EVENTS, pages) == ENTRY


def test_a_landing_page_that_survived_compilation_is_the_entry():
    from noui_core.compile import browser_skill

    pages = [
        {"name": "read_overview", "url": ENTRY, "nav": None},
        {"name": "read_credit_card", "url": CC, "nav": [{"text": "Credit Cards"}]},
    ]
    assert browser_skill.entry_url_for(URL_EVENTS, pages) == ENTRY


def test_an_unreachable_page_does_not_pass_as_the_landing_page():
    # nav == [] means the driving click could not be recovered — NOT the landing
    # page. Collapsing the two is the mistake this compiler already made once.
    from noui_core.compile import browser_skill

    pages = [{"name": "read_credit_card", "url": CC, "nav": []}]
    assert browser_skill.entry_url_for(URL_EVENTS, pages) == ENTRY


def test_the_compiler_stamps_what_entry_url_for_returns():
    from noui_core.compile import browser_skill

    doc = json.loads(
        browser_skill.render_browser_operations_json(
            [{"name": "read_credit_card", "url": CC, "steps": []}],
            profile_slug="icici-credit-card-statement",
            terminal_ops=[],
            entry_url=ENTRY,
        )
    )
    assert doc["entry_url"] == ENTRY


# --- navigate is not always available -----------------------------------------
#
# First live run of this reset: it used `navigate`, which the worker refuses on
# an app that carries its session in the URL -- "a full-page load destroys its
# session". The compiled SKILL.md says the same thing to the agent. The reset
# was built without checking that the command it chose was one the app allows.

_DISABLED = RuntimeError(
    "execute/browser 'navigate' failed: navigate is disabled for this app: "
    "a full-page load destroys its session"
)


class _NoNavigate(_Browser):
    """An ICICI-shaped app: navigate refused, a home link in the summary."""

    def __init__(self, at, summary):
        super().__init__(at)
        self.summary = summary

    def __call__(self, command, params):
        if command == "navigate":
            self.calls.append((command, params))
            raise _DISABLED
        if command == "get_page_summary":
            self.calls.append((command, params))
            return {"data": self.summary}
        if command == "click_element":
            self.calls.append((command, params))
            self.at = ENTRY  # the app's own route change
            return {"data": {}}
        return super().__call__(command, params)


HOME_LINK = {"links": [{"text": "", "href": "/overview", "selector": "a.logo"}]}


def test_when_navigate_is_refused_the_reset_clicks_the_way_back():
    b = _NoNavigate(DRIFTED, HOME_LINK)
    assert session_mod._return_to_entry(b, ENTRY) is None
    assert "click_element" in b.commands
    assert b.at == ENTRY


def test_the_home_control_is_found_by_where_it_points_not_what_it_says():
    # It is usually a logo with no text at all. Matching words would need a list
    # per language and per bank.
    got = session_mod._home_control(HOME_LINK, ENTRY)
    assert got == {"command": "click_element", "params": {"selector": "a.logo"}}


def test_a_link_to_somewhere_else_is_not_the_way_back():
    other = {"links": [{"text": "Cards", "href": "/credit-card", "selector": "a.cc"}]}
    assert session_mod._home_control(other, ENTRY) is None


def test_no_way_back_is_reported_with_the_reason():
    b = _NoNavigate(DRIFTED, {"links": [], "buttons": []})
    failed = session_mod._return_to_entry(b, ENTRY)
    assert failed is not None
    assert "does not allow navigate" in failed["error"]
    assert DRIFTED in failed["error"]


def test_a_click_that_does_not_land_at_the_start_is_not_treated_as_success():
    class _Wanders(_NoNavigate):
        def __call__(self, command, params):
            if command == "click_element":
                self.calls.append((command, params))
                self.at = "https://retailnetbanking.icici.bank.in/somewhere-else"
                return {"data": {}}
            return super().__call__(command, params)

    b = _Wanders(DRIFTED, HOME_LINK)
    failed = session_mod._return_to_entry(b, ENTRY)
    assert failed is not None
    assert "did not land on the start" in failed["error"]


def test_a_real_navigate_failure_is_not_mistaken_for_the_refusal():
    # Only the "navigate is disabled" refusal earns the click fallback; anything
    # else is a genuine failure and must be reported, not worked around.
    b = _Browser(DRIFTED, navigate_fails=True)
    failed = session_mod._return_to_entry(b, ENTRY)
    assert failed is not None
    assert "get_page_summary" not in b.commands


# --- a sign-in page is a WAIT, not a plan failure -----------------------------
#
# Live run: the browser sat on ICICI's AuthenticationController with LOGIN_FLAG=1
# and the reset reported "no link back to the start was found on the page" —
# true, and useless. A login screen has no link to the landing page. The member
# needs a sign-in card, not a report that their skill is broken.

LOGIN_PAGE = (
    "https://infinity.icici.bank.in/corp/AuthenticationController"
    "?FORMSGROUP_ID__=AuthenticationFG&LOGIN_FLAG=1"
)


def test_a_sign_in_page_becomes_login_required_not_a_blocked_step():
    b = _Browser(LOGIN_PAGE)
    try:
        session_mod._return_to_entry(b, ENTRY)
    except SessionNotReadyError as exc:
        assert "sign-in flow" in str(exc)
        # Nothing was attempted against a session that cannot answer.
        assert b.commands == ["get_page_info"]
        return
    raise AssertionError("expected SessionNotReadyError")


def test_the_ordinary_login_page_shape_is_caught_too():
    for url in (
        "https://retailnetbanking.icici.bank.in/login-page",
        "https://example.test/auth/signin",
        "https://example.test/session/verify",
    ):
        b = _Browser(url)
        try:
            session_mod._return_to_entry(b, ENTRY)
        except SessionNotReadyError:
            continue
        raise AssertionError(f"not detected: {url}")


def test_an_ordinary_app_page_is_still_a_reset_not_a_sign_in():
    # The detector must not swallow a real drift: /credit-card/add-card is a
    # deep app page, and the answer there is to go back to the start.
    b = _Browser(DRIFTED)
    assert session_mod._return_to_entry(b, ENTRY) is None
    assert "navigate" in b.commands


# --- walking history home ------------------------------------------------------
#
# A recorded journey can cross origins: ICICI's statement portal is a different
# host from the net-banking SPA. From there nothing links back to the landing
# page and navigate is refused, so every operation after the first had no way
# home and the reset failed six times in a row.


class _History(_Browser):
    """A back stack. goBack pops it; navigate is refused, as on ICICI."""

    def __init__(self, stack):
        super().__init__(stack[-1])
        self.stack = list(stack)

    def __call__(self, command, params):
        if command == "go_back":
            self.calls.append((command, params))
            moved = len(self.stack) > 1
            if moved:
                self.stack.pop()
                self.at = self.stack[-1]
            return {"data": {"url": self.at, "moved": moved}}
        if command == "navigate":
            self.calls.append((command, params))
            raise _DISABLED
        return super().__call__(command, params)


# --- history does not stop at the landing page ---------------------------------
#
# Measured on ICICI: from /overview one back press lands on /login-page, another
# on about:blank. A signed-in session walked to its own sign-in page looks
# exactly like being logged out — and pressing on abandons the app entirely.

LOGIN_PG = "https://retailnetbanking.icici.bank.in/login-page"


def test_leaving_the_app_is_detected():
    known = {ENTRY}
    assert session_mod._left_the_app("about:blank", ENTRY, known) is True
    assert session_mod._left_the_app(LOGIN_PG, ENTRY, known) is True
    assert session_mod._left_the_app("https://example.test/x", ENTRY, known) is True


def test_a_recorded_page_on_another_origin_is_not_leaving():
    # The statement portal IS part of the journey, on a different host. Treating
    # a cross-origin recorded page as "left the app" would stop the walk at the
    # very page it needs to walk back from.
    portal = "https://infinity.icici.bank.in/corp/AuthenticationController"
    assert session_mod._left_the_app(portal + ";jsessionid=abc", ENTRY, {portal}) is False


def test_an_ordinary_deeper_page_is_not_leaving():
    assert session_mod._left_the_app(DRIFTED, ENTRY, {ENTRY}) is False


# --- a reset never changes hosts -----------------------------------------------
#
# The recording went forward only — /overview -> /credit-card -> the statement
# portal — and never walked back, so no route home across hosts was ever
# observed. Whatever host the browser is on, the reset aims at the first page
# the recording reached THERE.

PORTAL = "https://infinity.icici.bank.in/corp/AuthenticationController"
BY_ORIGIN = {
    "https://retailnetbanking.icici.bank.in": ENTRY,
    "https://infinity.icici.bank.in": PORTAL,
}


def test_on_the_portal_the_reset_aims_at_the_portal_entry():
    here = PORTAL + ";jsessionid=abc"
    assert session_mod._entry_for_origin(here, ENTRY, BY_ORIGIN) == PORTAL


def test_on_the_netbanking_host_it_aims_at_overview():
    assert session_mod._entry_for_origin(DRIFTED, ENTRY, BY_ORIGIN) == ENTRY


def test_an_unrecorded_host_falls_back_rather_than_guessing():
    assert session_mod._entry_for_origin("https://elsewhere.test/x", ENTRY, BY_ORIGIN) == ENTRY


def test_without_a_map_behaviour_is_unchanged():
    assert session_mod._entry_for_origin(PORTAL, ENTRY, None) == ENTRY


def test_the_compiler_emits_one_entry_per_host():
    from noui_core.compile import browser_skill

    got = browser_skill.entry_urls_by_origin(
        URL_EVENTS
        + [
            {"seq": 13, "from_url": CC, "to_url": PORTAL},
            {"seq": 19, "from_url": PORTAL, "to_url": PORTAL + ";jsessionid=x"},
        ]
    )
    assert got["https://retailnetbanking.icici.bank.in"] == ENTRY  # not /login-page
    assert got["https://infinity.icici.bank.in"] == PORTAL  # the FIRST one


# --- repositioning by click, and being honest about which click ----------------
#
# On a browser-driven app navigate is banned — a full load destroys the session —
# so a click is how you reposition. WHICH click matters: one the recording
# watched arrive at this page is evidence; one hunted from the live page is a
# guess, and must not pass as the former.

RECORDED_OPS = [
    {
        "name": "read_overview",
        "steps": [
            {"command": "click_by_text", "params": {"text": "Home"}, "expect": {"url": ENTRY}},
            {"command": "get_page_summary", "params": {}},
        ],
    }
]


def test_a_recorded_control_is_preferred_over_hunting_the_page():
    class _B(_NoNavigate):
        def __call__(self, command, params):
            if command == "click_by_text" and params.get("text") == "Home":
                self.calls.append((command, params))
                self.at = ENTRY
                return {"data": {}}
            return super().__call__(command, params)

    b = _B(DRIFTED, HOME_LINK)
    notes: list = []
    assert session_mod._return_to_entry(b, ENTRY, {ENTRY}, None, RECORDED_OPS, notes) is None
    assert ("click_by_text", {"text": "Home"}) in b.calls
    assert "get_page_summary" not in b.commands, "should not hunt when evidence exists"
    assert notes == [], "a recorded control is not an unobserved reset"


def test_the_live_page_fallback_is_recorded_as_unobserved():
    b = _NoNavigate(DRIFTED, HOME_LINK)
    notes: list = []
    assert session_mod._return_to_entry(b, ENTRY, {ENTRY}, None, [], notes) is None
    assert len(notes) == 1
    assert notes[0]["kind"] == "unobserved_reset"
    assert notes[0]["entry_url"] == ENTRY
    assert "a.logo" in str(notes[0]["control"])


def test_a_recorded_control_for_another_page_is_not_used():
    ops = [
        {
            "name": "x",
            "steps": [
                {
                    "command": "click_by_text",
                    "params": {"text": "Cards"},
                    "expect": {"url": DRIFTED},
                }
            ],
        }
    ]
    assert session_mod._recorded_way_back(ops, ENTRY) is None


def test_the_query_string_does_not_hide_a_match():
    ops = [
        {
            "name": "x",
            "steps": [
                {
                    "command": "click_element",
                    "params": {"selector": "#home"},
                    "expect": {"url": ENTRY + "?ref=nav"},
                }
            ],
        }
    ]
    got = session_mod._recorded_way_back(ops, ENTRY)
    assert got == {"command": "click_element", "params": {"selector": "#home"}}


def test_the_origin_map_uses_the_page_before_the_first_hop():
    # The compiler gets the WORKFLOW slice, whose first transition is already
    # /overview -> /credit-card. Reading destinations alone made the
    # net-banking entry /credit-card — the page the first step is trying to
    # reach, not the one it acts on.
    from noui_core.compile import browser_skill

    got = browser_skill.entry_urls_by_origin(
        [
            {"seq": 4, "from_url": ENTRY, "to_url": CC},
            {"seq": 13, "from_url": CC, "to_url": PORTAL},
        ]
    )
    assert got["https://retailnetbanking.icici.bank.in"] == ENTRY
    assert got["https://infinity.icici.bank.in"] == PORTAL


# --- letting the entry page paint after a reset --------------------------------
#
# A history restore does not render instantly. The reset landed on /overview,
# returned success, and the very next step asked whether the sidebar control was
# visible — on a page still coming up. It resolved and was not yet visible, so
# every operation after the first failed at step 0 while the reset itself had
# worked perfectly.


def test_the_settle_prefers_what_the_recording_measured():
    ops = [{"steps": [{"command": "hover", "expect": {"settle_ms": 4322}}]}]
    assert session_mod._settle_after_reset(ops) == 4.322


def test_the_constant_is_only_a_floor():
    ops = [{"steps": [{"command": "hover", "expect": {"settle_ms": 200}}]}]
    session_mod._RESET_SETTLE_MS = 3000
    try:
        assert session_mod._settle_after_reset(ops) == 3.0
    finally:
        session_mod._RESET_SETTLE_MS = 0


def test_a_wild_recorded_settle_is_capped():
    ops = [{"steps": [{"command": "hover", "expect": {"settle_ms": 90_000}}]}]
    assert session_mod._settle_after_reset(ops) == 10.0


def test_only_the_first_step_of_each_operation_counts():
    # A slow step deep in a flow says nothing about how long the ENTRY page
    # takes to paint.
    ops = [
        {
            "steps": [
                {"command": "hover", "expect": {"settle_ms": 100}},
                {"command": "click_element", "expect": {"settle_ms": 9000}},
            ]
        }
    ]
    session_mod._RESET_SETTLE_MS = 0
    assert session_mod._settle_after_reset(ops) == 0.1


def test_no_recorded_settle_falls_back_to_the_constant():
    session_mod._RESET_SETTLE_MS = 2500
    try:
        assert session_mod._settle_after_reset([]) == 2.5
    finally:
        session_mod._RESET_SETTLE_MS = 0


def test_already_at_the_entry_does_not_wait():
    # Nothing moved, so there is nothing to paint. This path must stay free.
    session_mod._RESET_SETTLE_MS = 5000
    try:
        b = _Browser(ENTRY)
        assert session_mod._return_to_entry(b, ENTRY) is None
        assert b.commands == ["get_page_info"]
    finally:
        session_mod._RESET_SETTLE_MS = 0


def test_the_reset_never_presses_back():
    """go_back is not a way home; it is a way onto the sign-in page.

    The SPA's back stack on ICICI is [about:blank, /login-page, /overview, ...],
    so a press from a shallow point lands the LIVE session on the sign-in page —
    "your session has expired" from the member's side. Watched happening: the
    session died the moment a replay started, every time, and never while it sat
    idle.
    """
    b = _NoNavigate(DRIFTED, HOME_LINK)
    session_mod._return_to_entry(b, ENTRY, {ENTRY}, None, [], [])
    assert "go_back" not in b.commands


def test_a_drifted_page_still_gets_reset_without_history():
    b = _Browser(DRIFTED)
    assert session_mod._return_to_entry(b, ENTRY) is None
    assert b.commands == ["get_page_info", "navigate"]
    assert b.at == ENTRY
