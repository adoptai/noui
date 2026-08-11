"""A workflow can contain a second login, to another origin.

ICICI's statement portal has its own sign-in inside the slice the splitter
already separated the first login from. The recording clicked "Log In" and the
URL gained a jsessionid; a replay whose session already satisfies it arrives
past that point, so the button is absent and the compiled step matched nothing.
The step is unnecessary, not broken — and on a cold replay it is needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.compile.browser_skill import _is_login_control, _step_for_click  # noqa: E402
from noui_core.verify.replay import (  # noqa: E402
    BLOCKED,
    SKIPPED,
    _control_identity,
    replay_step,
)

# The real candidates recorded for #DEH_LOGIN (bundle icici-cc-hover-1f5ad255).
LOGIN_CLICK = {
    "candidates": [
        {"kind": "id", "value": "#DEH_LOGIN", "match_count": 1},
        {"kind": "name", "value": 'input[name="Action\\.DEH_LOGIN"]', "match_count": 1},
        {"kind": "role_name", "value": "button|Log In", "match_count": 1},
    ],
    "locator": {"kind": "id", "value": "#DEH_LOGIN", "is_css": True,
                "match_count": 1, "confidence": "unique"},
}


def test_a_login_control_is_recognised_by_its_accessible_name():
    # #DEH_LOGIN says nothing; "button|Log In" is the control announcing itself.
    assert _is_login_control(LOGIN_CLICK) is True


def test_an_ordinary_control_is_not():
    assert _is_login_control({"candidates": [
        {"kind": "role_name", "value": "button|GO"},
        {"kind": "id", "value": "#DUMMY1"},
    ]}) is False


def test_a_page_about_logins_is_not_a_login_control():
    # The match is anchored to the end of the accessible name.
    assert _is_login_control({"candidates": [
        {"kind": "role_name", "value": "link|Login history"},
    ]}) is False


def test_the_step_is_marked_optional_with_a_reason():
    step = _step_for_click(LOGIN_CLICK)
    assert step["optional"] is True
    assert "already have done" in step["optional_reason"]


def test_an_absent_login_control_is_skipped_not_blocked():
    step = {**_step_for_click(LOGIN_CLICK), "command": "click_element"}

    def execute(cmd, params):
        raise RuntimeError("nothing on the page matches \"#DEH_LOGIN\"")

    res = replay_step(execute, step, recorded={_control_identity(step)})
    assert res["status"] == SKIPPED
    assert "already satisfied" in res["detail"]


def test_a_login_control_that_is_present_and_fails_still_blocks():
    # Absent means already signed in. Present-and-failing is a real failure and
    # must not be waved through.
    step = {**_step_for_click(LOGIN_CLICK), "command": "click_element"}

    def execute(cmd, params):
        raise RuntimeError("element is covered by an overlay")

    assert replay_step(execute, step, recorded={_control_identity(step)})["status"] == BLOCKED


def test_a_non_optional_step_is_never_skipped():
    step = {"command": "click_element", "params": {"selector": "#DL"}}

    def execute(cmd, params):
        raise RuntimeError("nothing on the page matches \"#DL\"")

    assert replay_step(execute, step, recorded={_control_identity(step)})["status"] == BLOCKED
