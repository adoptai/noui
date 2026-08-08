"""Tests for replaying a draft skill before anything is installed.

Every failure this project has hit looked right on paper — `a.mb-0` reads like a
selector, a missing download operation looks like a skill with three operations,
a hidden radio looks clickable. These pin the gate that actually tries the plan
against the live app, and the rules that keep an improvising agent from doing
something irreversible while it does.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.verify.replay import (  # noqa: E402
    BLOCKED,
    NEEDS_APPROVAL,
    OK,
    approve,
    build_report,
    classify_risk,
    goal_reached,
    plan_operations,
    recorded_controls,
    replay_step,
)


def step(command="click_element", **params) -> dict:
    return {"command": command, "params": params}


DOWNLOAD_OP = {
    "name": "download_statement",
    "kind": "download",
    "tool": "call_web_browser",
    "steps": [
        step(selector="#nav-cards"),
        step(selector="#stmt-dl"),
        {"command": "list_downloads"},
    ],
}


# --- what may be replayed unattended -----------------------------------------


def test_a_money_moving_control_is_never_replayed_unattended():
    # An improvising agent on a bank portal can reach Pay while hunting for a
    # statement. A false stop costs one question; a false proceed moves money.
    risk = classify_risk(step(text="Pay now"), recorded_controls={"text:pay now"})
    assert risk and "pay" in risk


def test_an_irreversible_control_is_stopped_even_if_the_human_used_it():
    # Being in the recording is not consent to repeat it.
    risk = classify_risk(step(text="Block card"), recorded_controls={"text:block card"})
    assert risk is not None


def test_a_control_the_human_never_touched_is_stopped():
    # THE catch-all: the plan only departs from the recording when the agent is
    # improvising, and that is exactly when nobody has verified what it does.
    risk = classify_risk(step(selector="#mystery"), recorded_controls={"selector:#known"})
    assert risk and "never touched" in risk


def test_a_recorded_harmless_control_replays_freely():
    assert (
        classify_risk(step(selector="#nav-cards"), recorded_controls={"selector:#nav-cards"})
        is None
    )


def test_reading_a_page_is_never_risky_whatever_it_says():
    # A page containing the word "Transfer" is not a transfer.
    assert (
        classify_risk({"command": "get_page_summary", "params": {}}, recorded_controls=set())
        is None
    )
    assert classify_risk({"command": "screenshot", "params": {}}, recorded_controls=set()) is None


def test_recorded_controls_come_from_the_draft_itself():
    # The draft is compiled from the recording, so its steps ARE what the human
    # did — no separate bookkeeping to drift out of date.
    assert recorded_controls([DOWNLOAD_OP]) == {"selector:#nav-cards", "selector:#stmt-dl"}


# --- running a step -----------------------------------------------------------


def test_a_step_that_throws_is_reported_not_raised():
    # A blocked step is the thing the human needs to see. Letting it abort the
    # run would lose every result after it.
    def boom(_c, _p):
        raise RuntimeError("Timeout 30000ms exceeded")

    out = replay_step(boom, step(selector="#nav-cards"), recorded={"selector:#nav-cards"})
    assert out["status"] == BLOCKED
    assert "Timeout" in out["detail"]


def test_a_worker_reported_failure_is_blocked_not_ok():
    # /execute/browser returns {success: false} for an in-page failure; that is a
    # real result, and treating it as success is how the Annual radio went
    # unnoticed.
    out = replay_step(
        lambda _c, _p: {"success": False, "error": "state did not change"},
        step(selector="#nav-cards"),
        recorded={"selector:#nav-cards"},
    )
    assert out["status"] == BLOCKED


def test_a_risky_step_asks_instead_of_running():
    calls = []
    out = replay_step(
        lambda c, p: calls.append((c, p)),
        step(text="Transfer funds"),
        recorded=set(),
    )
    assert out["status"] == NEEDS_APPROVAL
    assert calls == []  # it did not run


def test_an_approved_risky_step_runs():
    out = replay_step(
        lambda _c, _p: {"success": True, "data": {}},
        step(text="Submit request"),
        recorded=set(),
        approvals={"text:submit request"},
    )
    assert out["status"] == OK


# --- did it actually achieve anything ----------------------------------------


def test_a_download_that_produced_no_file_has_not_reached_its_goal():
    # Every step "passed" and nothing was downloaded. This is the distinction the
    # whole `kind` field exists for.
    steps = [
        {"status": OK, "command": "click_element"},
        {"status": OK, "command": "list_downloads", "data": {"downloads": []}},
    ]
    assert goal_reached(DOWNLOAD_OP, steps) is False


def test_a_download_that_produced_a_file_has():
    steps = [
        {"status": OK, "command": "click_element"},
        {"status": OK, "command": "list_downloads", "data": {"downloads": [{"id": "dl-1"}]}},
    ]
    assert goal_reached(DOWNLOAD_OP, steps) is True


def test_a_blocked_step_means_the_goal_was_not_reached():
    steps = [{"status": BLOCKED, "command": "click_element"}]
    assert goal_reached({"kind": "read"}, steps) is False


# --- the gate -----------------------------------------------------------------


def test_a_report_is_never_installable_however_well_it_went():
    # `installable` is a human decision, not a conclusion the replay may draw.
    steps = [
        [
            {"status": OK, "command": "click_element"},
            {"status": OK, "command": "list_downloads", "data": {"downloads": [{"id": "1"}]}},
        ]
    ]
    report = build_report([DOWNLOAD_OP], steps)

    assert report["all_goals_reached"] is True
    assert report["installable"] is False


def test_only_an_explicit_approval_makes_it_installable():
    report = build_report([DOWNLOAD_OP], [[{"status": OK, "command": "x"}]])
    assert approve(report)["installable"] is True


def test_the_report_names_the_goal_that_failed_not_just_a_step_count():
    # "11 of 12 steps passed" hides exactly the failure that matters.
    report = build_report(
        [DOWNLOAD_OP],
        [
            [
                {"status": OK, "command": "click_element"},
                {"status": BLOCKED, "command": "click_element"},
            ]
        ],
    )
    op = report["operations"][0]

    assert op["goal_reached"] is False
    assert op["blocked_count"] == 1
    assert op["name"] == "download_statement"


# --- scope --------------------------------------------------------------------


def test_only_browser_operations_are_replayed():
    # HAR-replay skills keep their existing test loop; replaying one means firing
    # recorded requests, which is a different risk profile.
    api_op = {"name": "list_txns", "tool": "call_web_api", "steps": []}
    assert plan_operations([DOWNLOAD_OP, api_op]) == [DOWNLOAD_OP]
