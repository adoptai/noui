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


# --- running against a real profile session -----------------------------------
#
# Replay uses the ordinary profile session — the same one an installed skill gets
# — rather than inheriting the recording's auth. Inheriting it does not survive
# real apps: ICICI carries its session in the URL
# (AuthenticationController;jsessionid=…) and SPAs keep tokens in sessionStorage,
# so a cookie-seeded cold browser reproduces neither and replay would fail for
# reasons that have nothing to do with the plan.

from noui_core.verify.replay import SessionNotReadyError, substitute_parameters  # noqa: E402
from noui_core.verify.session import run_replay, session_is_ready  # noqa: E402


def _patch_exec(monkeypatch, fn):
    from noui_core import tabby_client

    monkeypatch.setattr(tabby_client, "execute_browser", fn)


def test_no_session_is_reported_as_login_required_not_as_a_broken_plan(monkeypatch):
    # Blaming the skill because nobody has signed in yet would be a lie, and one
    # the human would act on by re-recording a plan that was fine.
    import pytest
    from noui_core.verify.session import _executor

    def no_session(*_a, **_k):
        raise RuntimeError("HTTP 404 from POST /execute/browser: no healthy session")

    _patch_exec(monkeypatch, no_session)
    with pytest.raises(SessionNotReadyError):
        _executor("icici", "tok")("get_page_info", {})


def test_an_ordinary_failure_is_not_mistaken_for_a_missing_session(monkeypatch):
    # A 500 is a real failure of the step; only 404/409 mean "not signed in".
    import pytest
    from noui_core.verify.session import _executor

    def boom(*_a, **_k):
        raise RuntimeError("HTTP 500 from POST /execute/browser: worker exploded")

    _patch_exec(monkeypatch, boom)
    with pytest.raises(RuntimeError) as exc:
        _executor("icici", "tok")("get_page_info", {})
    assert not isinstance(exc.value, SessionNotReadyError)


def test_session_readiness_is_probed_without_touching_the_app(monkeypatch):
    seen = []
    _patch_exec(monkeypatch, lambda _p, c, _params, **_k: seen.append(c) or {"success": True})

    assert session_is_ready("icici", "tok") is True
    assert seen == ["get_page_info"]  # reads nothing, changes nothing


def test_a_dead_session_mid_run_keeps_what_already_ran(monkeypatch):
    # The steps that ran are still evidence. Discarding them would make a timed
    # out session look like a plan that does nothing.
    calls = {"n": 0}

    def flaky(_profile, command, _params, **_kw):
        # The run opens with one read of its own -- the download baseline, so a
        # file left by an earlier replay cannot be mistaken for this one's -- and
        # that read is not a step. The session dies after the first real step.
        if command == "list_downloads" and calls["n"] == 0:
            return {"success": True, "data": {"downloads": []}}
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("HTTP 409 from POST /execute/browser: session gone")
        return {"success": True, "data": {}}

    _patch_exec(monkeypatch, flaky)
    report = run_replay([DOWNLOAD_OP], profile_slug="icici", token="tok")

    assert report["status"] == "login_required"
    assert report["operations"][0]["steps"][0]["status"] == OK
    assert report["installable"] is False


def test_a_replay_that_reaches_the_goal_is_still_not_installable(monkeypatch):
    # The file arrives DURING the run: nothing on disk when the replay starts,
    # one statement by the time the operation lists them. A run that starts with
    # the file already there has downloaded nothing, which is a different case
    # (see test_a_file_left_by_an_earlier_run_is_not_this_ones_evidence).
    seen = {"listed": 0}

    def fake(_p, c, _params, **_k):
        if c != "list_downloads":
            return {"success": True, "data": {}}
        seen["listed"] += 1
        first = seen["listed"] == 1
        return {"success": True, "data": {"downloads": [] if first else [{"id": "dl-1"}]}}

    _patch_exec(monkeypatch, fake)
    report = run_replay([DOWNLOAD_OP], profile_slug="icici", token="tok")

    assert report["all_goals_reached"] is True
    assert report["installable"] is False  # only approve() sets this


def test_placeholders_are_filled_with_the_recorded_default():
    # Typing a literal "{{from_date}}" into a bank's date field would fail for a
    # reason that has nothing to do with the plan.
    op = {
        "parameters": [{"name": "from_date", "default": "2026-01-01"}],
        "steps": [{"command": "type_text", "params": {"selector": "#d", "text": "{{from_date}}"}}],
    }
    assert substitute_parameters(op)[0]["params"]["text"] == "2026-01-01"


def test_a_caller_supplied_value_wins_over_the_recorded_default():
    op = {
        "parameters": [{"name": "from_date", "default": "2026-01-01"}],
        "steps": [{"command": "type_text", "params": {"selector": "#d", "text": "{{from_date}}"}}],
    }
    filled = substitute_parameters(op, {"from_date": "2025-04-01"})
    assert filled[0]["params"]["text"] == "2025-04-01"


def test_a_download_operation_does_not_pass_on_an_earlier_ones_file():
    """The annual statement "succeeded" on the monthly PDF.

    `list_downloads` reports every download the browser has taken for the life
    of the session. Once ANY operation downloads anything, every later download
    check sees a file and passes -- so a replay where the annual branch silently
    did nothing reported all goals reached, which is precisely the failure the
    replay exists to catch.
    """
    from noui_core.verify.replay import build_report

    monthly = {"name": "monthly", "kind": "download", "steps": []}
    annual = {"name": "annual", "kind": "download", "steps": []}
    listed = [{"id": "dl-1", "state": "completed", "suggested_filename": "Statement.pdf"}]
    results = [
        [{"status": "ok", "command": "list_downloads", "data": {"downloads": listed}}],
        # The annual operation ran and produced nothing new: the same one file.
        [{"status": "ok", "command": "list_downloads", "data": {"downloads": listed}}],
    ]
    report = build_report([monthly, annual], results)
    by_name = {o["name"]: o for o in report["operations"]}
    assert by_name["monthly"]["goal_reached"] is True
    assert by_name["annual"]["goal_reached"] is False
    assert report["all_goals_reached"] is False


def test_a_second_download_operation_passes_on_its_own_file():
    from noui_core.verify.replay import build_report

    first = [{"id": "dl-1", "state": "completed"}]
    both = [*first, {"id": "dl-2", "state": "completed"}]
    report = build_report(
        [{"name": "monthly", "kind": "download"}, {"name": "annual", "kind": "download"}],
        [
            [{"status": "ok", "command": "list_downloads", "data": {"downloads": first}}],
            [{"status": "ok", "command": "list_downloads", "data": {"downloads": both}}],
        ],
    )
    assert report["all_goals_reached"] is True


def test_a_download_still_in_flight_or_failed_is_not_evidence():
    """A record exists from the moment the browser starts fetching."""
    from noui_core.verify.replay import goal_reached

    op = {"name": "d", "kind": "download"}
    for state in ("in_progress", "failed"):
        steps = [{"status": "ok", "command": "list_downloads",
                  "data": {"downloads": [{"id": "dl-1", "state": state}]}}]
        assert goal_reached(op, steps) is False, state


def test_a_download_operation_is_judged_by_its_file_not_its_step_list():
    """The annual replay produced the right statement and reported failure.

    The file arrived through #PDF_Download; a later click on a control the page
    had already moved past was recorded as blocked, and any blocked step made
    goal_reached false. That describes the plan, not the outcome — and the
    outcome is what this field is for. The blocked steps stay in the report.
    """
    from noui_core.verify.replay import goal_reached

    op = {"name": "download_statement", "kind": "download"}
    steps = [
        {"status": "ok", "command": "click_element"},
        {"status": "blocked", "command": "click_element"},
        {"status": "ok", "command": "list_downloads",
         "data": {"downloads": [{"id": "dl-7", "state": "completed", "size_bytes": 85216}]}},
    ]
    assert goal_reached(op, steps) is True


def test_a_blocked_download_with_no_file_is_still_a_failure():
    from noui_core.verify.replay import goal_reached

    op = {"name": "download_statement", "kind": "download"}
    steps = [
        {"status": "blocked", "command": "click_element"},
        {"status": "ok", "command": "list_downloads", "data": {"downloads": []}},
    ]
    assert goal_reached(op, steps) is False


def test_an_unanswered_approval_still_stops_a_download():
    """A human has not agreed to the thing being asked about."""
    from noui_core.verify.replay import goal_reached

    op = {"name": "download_statement", "kind": "download"}
    steps = [
        {"status": "needs_approval", "command": "click_element"},
        {"status": "ok", "command": "list_downloads",
         "data": {"downloads": [{"id": "dl-9", "state": "completed"}]}},
    ]
    assert goal_reached(op, steps) is False


def test_a_read_operation_is_still_judged_by_its_steps():
    """Only a download has evidence of its own; everything else has the steps."""
    from noui_core.verify.replay import goal_reached

    op = {"name": "read_page", "kind": "read"}
    assert goal_reached(op, [{"status": "blocked", "command": "click_element"}]) is False
    assert goal_reached(op, [{"status": "ok", "command": "get_page_summary"}]) is True


def test_a_file_left_by_an_earlier_run_is_not_this_ones_evidence(monkeypatch):
    """A replay that downloads nothing must not pass on yesterday's statement.

    The session accumulates downloads for its whole life. Seeded only from
    earlier operations in the same run, the baseline missed everything a
    PREVIOUS run had fetched — and a run whose download step blocked reported
    the goal reached, on the strength of a file it never produced. Observed
    live: two consecutive annual replays both reported success while the
    download count stayed at 9.
    """
    _patch_exec(
        monkeypatch,
        lambda _p, c, _params, **_k: {
            "success": True,
            "data": {"downloads": [{"id": "dl-old", "state": "completed"}]}
            if c == "list_downloads"
            else {},
        },
    )
    report = run_replay([DOWNLOAD_OP], profile_slug="icici", token="tok")

    assert report["all_goals_reached"] is False


def test_an_operation_that_ran_nothing_reached_no_goal():
    """The most misleading answer this field can give.

    A replay that executed zero steps reported goal_reached true and
    all_goals_reached true, because "no step was blocked" is trivially satisfied
    by an empty list — a report nobody would question.
    """
    from noui_core.verify.replay import goal_reached

    assert goal_reached({"name": "r", "kind": "read"}, []) is False
    assert goal_reached({"name": "d", "kind": "download"}, []) is False


def test_a_mixture_of_segmented_and_unsegmented_operations_is_refused(monkeypatch):
    """Segments decide how the WHOLE replay executes.

    One operation without a segment silently downgraded every operation to the
    legacy reset-before-each path. Seen live: a stray `read_overview` turned a
    five-operation run into one that executed nothing and reported every goal
    reached, with nothing in the report saying the model had changed.
    """
    from noui_core.verify import session as session_mod

    monkeypatch.setattr(session_mod, "_executor", lambda *a, **k: (lambda c, p=None: {"data": {}}))
    report = session_mod.run_replay(
        [
            {"name": "a", "tool": "call_web_browser", "steps": [], "segment_steps": []},
            {"name": "b", "tool": "call_web_browser", "steps": []},   # no segment
        ],
        profile_slug="p", token="t", entry_url="https://x.test/home",
    )

    assert report["status"] == "not_replayable"
    assert "b" in report["detail"]
