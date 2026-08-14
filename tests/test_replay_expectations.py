"""Replay checks it ended where the recording ended.

`expect` was carried into the result and never evaluated, so a replay could
execute every step, land on a completely different page, and report success —
"no step was blocked" was the whole test. That is how a run clicked its way onto
/discover and still called itself passing.
"""

from noui_core.verify.replay import (
    BLOCKED,
    OK,
    SKIPPED,
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
        return {
            "data": {
                "url": "https://infinity.icici.bank.in/corp/AuthenticationController;jsessionid=x"
            }
        }

    unmet = expectation_unmet(
        {"url": "https://infinity.icici.bank.in/corp/Finacle;jsessionid=y"}, execute
    )
    assert "expected to be on" in unmet


def test_a_download_step_does_not_pass_on_a_file_from_an_earlier_step():
    """Per step, the same staleness as per operation.

    The annual branch's download click fires into a busy form and produces
    nothing; the monthly PDF from four steps earlier is still listed, so the
    step's own `expect.download` was satisfied by it.
    """
    step = {"command": "click_element", "params": {"selector": "#dl"}, "expect": {"download": True}}
    known = {"dl-1"}
    res = replay_step(
        _exec(downloads=[{"id": "dl-1", "state": "completed"}]),
        step,
        recorded={_control_identity(step)},
        known_downloads=known,
    )
    assert res["status"] == BLOCKED
    assert "no NEW completed file" in res["detail"]


def test_a_download_step_passes_on_a_file_that_arrived_here():
    step = {"command": "click_element", "params": {"selector": "#dl"}, "expect": {"download": True}}
    known = {"dl-1"}
    res = replay_step(
        _exec(
            downloads=[{"id": "dl-1", "state": "completed"}, {"id": "dl-2", "state": "completed"}]
        ),
        step,
        recorded={_control_identity(step)},
        known_downloads=known,
    )
    assert res["status"] == OK
    # Both are accounted for now, so the NEXT download step cannot reuse either.
    assert known == {"dl-1", "dl-2"}


# --- arrived past a step ------------------------------------------------------


def _invisible(_cmd, _params=None):
    return {
        "success": False,
        "error": "this control is on the page but not visible, so acting on it would do nothing",
    }


def test_a_step_the_page_has_moved_past_is_skipped_not_blocked():
    """ICICI's GO button after the Annual radio.

    The human pressed GO because their radio selection did not submit the form.
    The replay's set_checked DOES submit it, so the page is already on the
    annual view and GO is hidden -- permanently: it stayed hidden through a full
    30s wait. The step is unnecessary, not broken.
    """
    calls = []

    def execute(command, params=None):
        calls.append((command, params))
        if command == "wait_for_selector":
            return {"success": True}  # the NEXT control is on screen
        return _invisible(command, params)

    step = {"command": "click_element", "params": {"selector": "#DUMMY1"}}
    res = replay_step(
        execute,
        step,
        recorded={_control_identity(step)},
        following=[{"command": "click_element", "params": {"selector": "#PDF_Download"}}],
    )

    assert res["status"] == SKIPPED
    assert "moved past" in res["detail"]


def test_an_invisible_control_whose_successor_is_also_hidden_still_blocks():
    """The hover menu, which is the reason 'invisible' cannot simply mean skip.

    The credit-card link is invisible until its menu opens, and so is everything
    after it. Blocking there is right -- something that should have revealed it
    has not run.
    """

    def execute(command, params=None):
        if command == "wait_for_selector":
            return {"success": False, "error": "Timeout"}  # successor not there either
        return _invisible(command, params)

    step = {"command": "click_element", "params": {"selector": "a.sub-menu-list-item-link"}}
    res = replay_step(
        execute,
        step,
        recorded={_control_identity(step)},
        following=[{"command": "click_element", "params": {"selector": "#deeper"}}],
    )

    assert res["status"] == BLOCKED


def test_the_last_step_of_an_operation_never_skips_this_way():
    """With no successor there is no evidence, and no evidence means blocked."""
    step = {"command": "click_element", "params": {"selector": "#DUMMY1"}}
    res = replay_step(_invisible, step, recorded={_control_identity(step)}, following=[])
    assert res["status"] == BLOCKED


def test_a_successor_named_only_by_text_is_not_evidence():
    """Only a CSS selector can be probed without acting on the page."""
    step = {"command": "click_element", "params": {"selector": "#DUMMY1"}}
    res = replay_step(
        _invisible,
        step,
        recorded={_control_identity(step)},
        following=[{"command": "click_by_text", "params": {"text": "FY2025-26"}}],
    )
    assert res["status"] == BLOCKED


def test_a_control_that_is_gone_entirely_is_the_same_situation():
    """The GO button showed up both ways on consecutive runs.

    Once present-but-hidden, once absent — how far the portal has re-rendered
    when the step fires varies. Both mean the step cannot be performed here, and
    what decides whether that is a failure is the same either way: what comes
    next.
    """

    def execute(command, params=None):
        if command == "wait_for_selector":
            return {"success": True}
        return {
            "success": False,
            "error": 'nothing on the page matches "#DUMMY1", nor any of the 1 recorded alternative(s)',
        }

    step = {"command": "click_element", "params": {"selector": "#DUMMY1"}}
    res = replay_step(
        execute,
        step,
        recorded={_control_identity(step)},
        following=[{"command": "click_element", "params": {"selector": "#PDF_Download"}}],
    )
    assert res["status"] == SKIPPED


def test_evidence_comes_from_the_next_step_that_names_a_control():
    """A read operation ends with get_page_summary, which addresses nothing.

    Anchoring on the immediate successor found no selector to probe and blocked
    — on the very case this rule exists for. ICICI's GO button is followed by
    get_page_summary in one operation and by the download icon in another; the
    situation is identical and so is the evidence.
    """
    probed = []

    def execute(command, params=None):
        if command == "wait_for_selector":
            probed.append((params or {}).get("selector"))
            return {"success": True}
        return {"success": False, "error": "this control is on the page but not visible"}

    step = {"command": "click_element", "params": {"selector": "#DUMMY1"}}
    res = replay_step(
        execute,
        step,
        recorded={_control_identity(step)},
        following=[
            {"command": "get_page_summary", "params": {}},  # names nothing
            {"command": "click_element", "params": {"selector": "#PDF_Download"}},
        ],
    )

    assert res["status"] == SKIPPED
    assert probed == ["#PDF_Download"]


def test_a_control_that_appears_on_the_second_attempt_is_not_a_failure():
    """The hover menu: its item is not rendered the instant the pointer lands.

    That made the ICICI replay a coin flip — it failed at step 0 about half the
    time and passed on an immediate retry with nothing changed. Waiting for the
    page to stop navigating did NOT fix it: the page was not navigating, the
    menu simply was not up yet.
    """
    attempts = {"n": 0}

    def execute(command, params=None):
        if command == "wait_for_selector":
            return {"success": True}
        attempts["n"] += 1
        if attempts["n"] == 1:
            return {"success": False, "error": "this control is on the page but not visible"}
        return {"success": True, "data": {"clicked": True}}

    step = {"command": "click_element", "params": {"selector": "a.sub-menu-list-item-link"}}
    res = replay_step(execute, step, recorded={_control_identity(step)})

    assert res["status"] == OK
    assert res["detail"] == "succeeded on a second attempt"
    assert attempts["n"] == 2


def test_the_retry_happens_before_deciding_the_page_moved_past_it():
    """A control that appears on the second attempt was never moved past."""
    attempts = {"n": 0}
    probed = []

    def execute(command, params=None):
        if command == "wait_for_selector":
            probed.append((params or {}).get("selector"))
            return {"success": True}
        attempts["n"] += 1
        if attempts["n"] == 1:
            return {"success": False, "error": "this control is on the page but not visible"}
        return {"success": True, "data": {}}

    step = {"command": "click_element", "params": {"selector": "#slow"}}
    res = replay_step(
        execute,
        step,
        recorded={_control_identity(step)},
        following=[{"command": "click_element", "params": {"selector": "#later"}}],
    )

    assert res["status"] == OK
    assert probed == []  # never had to ask whether we had moved past it


def test_a_control_that_stays_unreachable_still_blocks():
    def execute(command, params=None):
        if command == "wait_for_selector":
            return {"success": False, "error": "Timeout"}
        return {"success": False, "error": "this control is on the page but not visible"}

    step = {"command": "click_element", "params": {"selector": "#gone"}}
    res = replay_step(
        execute,
        step,
        recorded={_control_identity(step)},
        following=[{"command": "click_element", "params": {"selector": "#later"}}],
    )
    assert res["status"] == BLOCKED
