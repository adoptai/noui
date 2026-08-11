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
    assert b.commands == ["get_page_info", "go_back", "navigate"]
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
    assert b.commands == ["get_page_info", "go_back", "navigate"]


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


def test_history_walks_back_to_the_entry_page():
    portal = "https://infinity.icici.bank.in/corp/AuthenticationController"
    b = _History([ENTRY, portal])
    # known_urls is what run_replay supplies: the portal is a page the recording
    # visited, so "auth" in its path must not read as a sign-in screen.
    assert session_mod._return_to_entry(b, ENTRY, {portal}) is None
    assert b.at == ENTRY
    assert "navigate" not in b.commands, "history got there; nothing else should be tried"


def test_history_is_tried_before_navigate():
    b = _History([ENTRY, DRIFTED])
    session_mod._return_to_entry(b, ENTRY)
    assert b.commands.index("go_back") < len(b.commands)
    assert b.commands[1] == "go_back"


def test_exhausted_history_stops_instead_of_pressing_back_forever():
    # moved=False means there is nowhere further back. Walking on would only
    # waste calls, and eventually leave the app entirely.
    portal = "https://infinity.icici.bank.in/corp/AuthenticationController"
    b = _History([portal])
    session_mod._return_to_entry(b, ENTRY, {portal})
    assert b.commands.count("go_back") == 1


def test_history_that_never_reaches_the_entry_is_bounded():
    deep = [f"https://infinity.icici.bank.in/corp/p{n}" for n in range(30)]
    b = _History(deep)
    session_mod._return_to_entry(b, ENTRY)
    assert b.commands.count("go_back") <= session_mod._MAX_BACK_STEPS


# --- history does not stop at the landing page ---------------------------------
#
# Measured on ICICI: from /overview one back press lands on /login-page, another
# on about:blank. A signed-in session walked to its own sign-in page looks
# exactly like being logged out — and pressing on abandons the app entirely.

LOGIN_PG = "https://retailnetbanking.icici.bank.in/login-page"


def test_the_walk_stops_before_the_sign_in_page():
    b = _History([ "about:blank", LOGIN_PG, ENTRY, DRIFTED])
    # Deliberately ask for a page that is NOT in the stack, so the walk would
    # keep pressing if nothing stopped it.
    missing = "https://retailnetbanking.icici.bank.in/nowhere"
    session_mod._return_to_entry(b, missing, {missing})
    assert b.at != "about:blank", "walked out of the app"
    assert b.at in (LOGIN_PG, ENTRY, DRIFTED)
    assert b.commands.count("go_back") <= 2


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
