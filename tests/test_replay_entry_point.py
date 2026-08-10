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
    assert b.commands == ["get_page_info", "navigate"]


def test_failed_reset_is_reported_as_the_reset_not_as_step_zero():
    b = _Browser(DRIFTED, navigate_fails=True)
    failed = session_mod._return_to_entry(b, ENTRY)
    assert failed is not None
    assert failed["command"] == "navigate"
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
