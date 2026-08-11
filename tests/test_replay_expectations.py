"""Replay checks it ended where the recording ended.

`expect` was carried into the result and never evaluated, so a replay could
execute every step, land on a completely different page, and report success —
"no step was blocked" was the whole test. That is how a run clicked its way onto
/discover and still called itself passing.
"""

from noui_core.verify.replay import (
    BLOCKED,
    OK,
    _control_identity,
    expectation_unmet,
    replay_step,
)


def _exec(pages=None, downloads=None):
    """A fake executor: records commands, answers page-info/list-downloads."""
    calls = []

    def run(command, params=None):
        calls.append(command)
        if command == "get_page_info":
            return {"success": True, "data": {"url": (pages or [""])[0]}}
        if command == "list_downloads":
            return {"success": True, "data": {"downloads": downloads or []}}
        return {"success": True, "data": {}}

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_a_step_that_lands_on_the_wrong_page_is_blocked():
    step = {
        "command": "click_element",
        "params": {"selector": "#statements"},
        "expect": {"url": "https://bank.test/statements"},
    }
    res = replay_step(
        _exec(pages=["https://bank.test/discover"]), step, recorded={_control_identity(step)}
    )
    assert res["status"] == BLOCKED
    assert "discover" in res["detail"] and "statements" in res["detail"]


def test_the_recorded_page_still_passes_when_the_portal_adds_its_own_query():
    # Session tokens churn between the recording and the replay; the route is
    # what was recorded, not the parameters hung off it.
    step = {
        "command": "click_element",
        "params": {"selector": "#s"},
        "expect": {"url": "https://bank.test/statements"},
    }
    res = replay_step(
        _exec(pages=["https://bank.test/statements?tok=NEW"]),
        step,
        recorded={_control_identity(step)},
    )
    assert res["status"] == OK


def test_a_download_step_that_produced_no_file_is_blocked():
    step = {"command": "click_element", "params": {"selector": "#dl"}, "expect": {"download": True}}
    res = replay_step(_exec(downloads=[]), step, recorded={_control_identity(step)})
    assert res["status"] == BLOCKED
    assert "no file arrived" in res["detail"]


def test_a_download_step_passes_when_the_file_arrives():
    step = {"command": "click_element", "params": {"selector": "#dl"}, "expect": {"download": True}}
    res = replay_step(
        _exec(downloads=[{"suggested_filename": "statement.pdf"}]),
        step,
        recorded={_control_identity(step)},
    )
    assert res["status"] == OK


def test_a_step_with_no_expectation_is_left_alone():
    # Most steps carry none; they must not pay for a page-info round trip.
    run = _exec()
    res = replay_step(
        run,
        {"command": "click_element", "params": {"selector": "#x"}},
        recorded={_control_identity({"command": "click_element", "params": {"selector": "#x"}})},
    )
    assert res["status"] == OK
    assert "get_page_info" not in run.calls


def test_an_unreadable_url_is_not_treated_as_a_failed_expectation():
    def broken(command, params=None):
        if command == "get_page_info":
            raise RuntimeError("page closed")
        return {"success": True, "data": {}}

    assert expectation_unmet({"url": "https://bank.test/x"}, broken) == ""


def test_a_jsessionid_does_not_fail_the_page_expectation():
    """ICICI carries its portal session as ;jsessionid=... in the PATH.

    The recorded URL and the live one name the same page with different tokens,
    and neither is a prefix of the other — so the download's own page assertion
    would have failed every replay for the wrong reason.
    """
    from noui_core.verify.replay import expectation_unmet

    portal = "https://infinity.icici.bank.in/corp/Finacle"
    live = portal + ";jsessionid=0000LIVE:CR21n41xcb?bwayparam=abc"

    def execute(cmd, params):
        return {"data": {"url": live}}

    assert expectation_unmet({"url": portal + ";jsessionid=0000RECORDED"}, execute) == ""


def test_a_genuinely_different_page_still_fails():
    from noui_core.verify.replay import expectation_unmet

    def execute(cmd, params):
        return {"data": {"url": "https://infinity.icici.bank.in/corp/AuthenticationController;jsessionid=x"}}

    unmet = expectation_unmet(
        {"url": "https://infinity.icici.bank.in/corp/Finacle;jsessionid=y"}, execute
    )
    assert "expected to be on" in unmet
