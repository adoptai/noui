"""Unit tests for recording.provision_live_link — verify a recording session is
live before surfacing its link, and refresh (restart, else re-provision) a stale
one instead of handing over a dead link."""

from unittest.mock import patch

from noui_core.capture import recording


def _session(sid, vnc="https://t/vnc/x?mode=recording#token=STREAMTOK"):
    return {"session_id": sid, "vnc_url": vnc, "recording_mode": "workflow"}


def test_live_on_first_try_surfaces_link_without_refresh():
    with (
        patch.object(recording, "resolve_agent_token", return_value="agent"),
        patch.object(recording, "start", return_value=_session("s1")) as start,
        patch.object(
            recording.tabby_client,
            "get_recording_panel_state",
            return_value={"state": "HEALTHY"},
        ),
        patch.object(
            recording.tabby_client, "create_short_link", return_value="https://t/s/aaa"
        ) as sl,
    ):
        out = recording.provision_live_link("workflow", "https://x")
    assert out["login_url"] == "https://t/s/aaa"
    assert "refreshed" not in out  # live first try → no refresh
    assert start.call_count == 1
    assert sl.call_count == 1


def test_waits_through_starting_then_surfaces_link():
    # pod is STARTING twice, then HEALTHY — we poll until it leaves STARTING
    # before minting the link, and never sleep once it has.
    with (
        patch.object(recording, "resolve_agent_token", return_value="agent"),
        patch.object(recording, "start", return_value=_session("s1")),
        patch.object(
            recording.tabby_client,
            "get_recording_panel_state",
            side_effect=[
                {"state": "STARTING"},
                {"state": "STARTING"},
                {"state": "HEALTHY"},
            ],
        ) as panel,
        patch.object(recording.tabby_client, "create_short_link", return_value="https://t/s/aaa"),
        patch.object(recording.time, "sleep") as sleep,
    ):
        out = recording.provision_live_link("workflow", "https://x")
    assert out["login_url"] == "https://t/s/aaa"
    assert "refreshed" not in out
    assert panel.call_count == 3  # polled until it left STARTING
    assert sleep.call_count == 2  # slept once per STARTING observation, not after


def test_stale_session_is_restarted_in_place():
    # short-link 400s on the dead session, restart revives it, second mint works.
    with (
        patch.object(recording, "resolve_agent_token", return_value="agent"),
        patch.object(recording, "start", return_value=_session("s1")) as start,
        patch.object(
            recording.tabby_client,
            "get_recording_panel_state",
            return_value={"state": "TERMINATED"},
        ),
        patch.object(
            recording.tabby_client,
            "create_short_link",
            side_effect=[RuntimeError("Cannot open stream"), "https://t/s/live"],
        ),
        patch.object(
            recording.tabby_client, "restart_recording_session", return_value=True
        ) as restart,
    ):
        out = recording.provision_live_link("workflow", "https://x")
    assert out["login_url"] == "https://t/s/live"
    assert out["refreshed"] == "restart"
    assert out["session_id"] == "s1"  # same session kept
    restart.assert_called_once_with("s1", "STREAMTOK")
    assert start.call_count == 1  # not re-provisioned


def test_dead_session_reprovisions_when_restart_fails():
    with (
        patch.object(recording, "resolve_agent_token", return_value="agent"),
        patch.object(recording, "start", side_effect=[_session("s1"), _session("s2")]) as start,
        patch.object(
            recording.tabby_client,
            "get_recording_panel_state",
            return_value={"state": "TERMINATED"},
        ),
        patch.object(
            recording.tabby_client,
            "create_short_link",
            side_effect=[RuntimeError("Cannot open stream"), "https://t/s/fresh"],
        ),
        patch.object(recording.tabby_client, "restart_recording_session", return_value=False),
    ):
        out = recording.provision_live_link("workflow", "https://x")
    assert out["login_url"] == "https://t/s/fresh"
    assert out["refreshed"] == "reprovision"
    assert out["session_id"] == "s2"  # fresh session
    assert start.call_count == 2
