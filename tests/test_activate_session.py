"""ensure_session: surface the profile's activation sign-in before anyone tests.

A profile's own session (the one call_web_api resolves) is a different browser
from the recording sessions the human drove, and starts LOGIN_NEEDED because
nothing is stored. Finding that out mid-test reads as a bug; finding out at
import time is a step.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from noui_core import tabby_client
from noui_core.activate import session as act
from noui_core.config import settings

_NOUI_ROOT = Path(__file__).resolve().parent.parent
for _p in (_NOUI_ROOT / "skills" / "noui", _NOUI_ROOT / "skills" / "noui" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import capture_import as ci  # noqa: E402


@pytest.fixture(autouse=True)
def broker(monkeypatch):
    """Broker mode so token resolution needs no Tabby client creds."""
    monkeypatch.setattr(settings, "tabby_auth_mode", "broker")
    monkeypatch.setattr(settings, "broker_token", "cap-tok")
    monkeypatch.setattr(act.time, "sleep", lambda *_a: None)


@pytest.fixture
def creds_empty():
    with patch.object(tabby_client, "request_credentials", return_value={}) as m:
        yield m


class TestNeedsLogin:
    def test_login_needed_returns_a_short_link(self, creds_empty):
        status = {"session_id": "s-1", "state": "LOGIN_NEEDED", "hitl_active": True}
        with (
            patch.object(tabby_client, "get_session_status", return_value=status),
            patch.object(tabby_client, "create_short_link", return_value="https://t/s/abc123"),
        ):
            result = act.ensure_session("acme")
        assert result["needs_login"] is True
        assert result["login_url"] == "https://t/s/abc123"
        assert result["state"] == "LOGIN_NEEDED"

    def test_short_link_uses_the_resolve_panel_not_the_recording_viewer(self, creds_empty):
        status = {"session_id": "s-1", "state": "LOGIN_NEEDED", "hitl_active": True}
        with (
            patch.object(tabby_client, "get_session_status", return_value=status),
            patch.object(tabby_client, "create_short_link", return_value="https://t/s/x") as link,
        ):
            act.ensure_session("acme")
        # mode="recording" would give the capture toolbar; this is a HITL login.
        assert link.call_args.kwargs.get("mode", "") == ""

    def test_falls_back_to_the_stream_url_when_short_link_fails(self, creds_empty):
        status = {
            "session_id": "s-1",
            "state": "LOGIN_IN_PROGRESS",
            "hitl_active": True,
            "vnc_stream": {"url": "https://t/vnc/s-1#token=x"},
        }
        with (
            patch.object(tabby_client, "get_session_status", return_value=status),
            patch.object(tabby_client, "create_short_link", side_effect=RuntimeError("nope")),
        ):
            result = act.ensure_session("acme")
        assert result["login_url"] == "https://t/vnc/s-1#token=x"

    def test_notice_explains_why_a_third_sign_in_is_needed(self, creds_empty):
        status = {"session_id": "s-1", "state": "LOGIN_NEEDED", "hitl_active": True}
        with (
            patch.object(tabby_client, "get_session_status", return_value=status),
            patch.object(tabby_client, "create_short_link", return_value="https://t/s/abc"),
        ):
            notice = act.format_activation_notice(act.ensure_session("acme"))
        assert "https://t/s/abc" in notice
        assert "OWN Tabby session" in notice
        assert "different browser" in notice
        assert "Nothing is stored" in notice


class TestHealthy:
    def test_healthy_session_needs_no_sign_in(self):
        with (
            patch.object(tabby_client, "request_credentials", return_value={"headers": [{}]}),
            patch.object(
                tabby_client,
                "get_session_status",
                return_value={"session_id": "s-1", "state": "HEALTHY"},
            ),
        ):
            result = act.ensure_session("acme")
        assert result["needs_login"] is False
        assert result["credentials_ready"] is True
        assert "no activation sign-in needed" in act.format_activation_notice(result)

    def test_provisioning_is_provoked_before_polling(self):
        """POST /credentials/request is what makes Tabby clone the per-user profile."""
        with (
            patch.object(tabby_client, "request_credentials", return_value={}) as req,
            patch.object(tabby_client, "get_session_status", return_value={"state": "HEALTHY"}),
        ):
            act.ensure_session("acme")
        assert req.call_args.args[0] == "acme"


class TestNoFalseGreen:
    def test_starting_then_login_needed(self, creds_empty):
        states = [
            {"state": "STARTING"},
            {"state": "STARTING"},
            {"session_id": "s-1", "state": "LOGIN_NEEDED", "hitl_active": True},
        ]
        with (
            patch.object(tabby_client, "get_session_status", side_effect=states),
            patch.object(tabby_client, "create_short_link", return_value="https://t/s/z"),
        ):
            result = act.ensure_session("acme", wait_seconds=30, poll_interval=0)
        assert result["needs_login"] is True

    def test_timeout_is_unknown_not_healthy(self, creds_empty):
        with patch.object(tabby_client, "get_session_status", return_value={"state": "STARTING"}):
            result = act.ensure_session("acme", wait_seconds=0, poll_interval=0)
        assert result["needs_login"] is None
        assert result["timed_out"] is True
        notice = act.format_activation_notice(result)
        assert "still provisioning" in notice
        assert "activate_session.py acme" in notice

    def test_terminal_state_is_reported_as_unrecoverable(self, creds_empty):
        with patch.object(tabby_client, "get_session_status", return_value={"state": "FAILED"}):
            result = act.ensure_session("acme", wait_seconds=0, poll_interval=0)
        assert result["needs_login"] is None
        assert "will not recover" in act.format_activation_notice(result)

    def test_missing_profile_keeps_polling_then_times_out(self, creds_empty):
        """A 404 while the profile is being cloned must not look like a failure."""
        with patch.object(
            tabby_client, "get_session_status", side_effect=RuntimeError("HTTP 404 …")
        ):
            result = act.ensure_session("acme", wait_seconds=0, poll_interval=0)
        assert result["needs_login"] is None
        assert result["state"] == ""


class TestCaptureImportIntegration:
    def _args(self, activate: bool):
        return SimpleNamespace(activate_session=activate)

    def test_flag_off_does_nothing(self, capsys):
        with patch.object(act, "ensure_session") as ensure:
            ci._maybe_activate_session(self._args(False), "acme")
        ensure.assert_not_called()
        assert capsys.readouterr().err == ""

    def test_flag_on_prints_the_notice(self, capsys):
        result = {
            "profile_slug": "acme",
            "state": "LOGIN_NEEDED",
            "session_id": "s-1",
            "needs_login": True,
            "login_url": "https://t/s/abc",
            "credentials_ready": False,
            "timed_out": False,
        }
        with patch.object(act, "ensure_session", return_value=result):
            ci._maybe_activate_session(self._args(True), "acme")
        err = capsys.readouterr().err
        assert "Activating the session for profile 'acme'" in err
        assert "https://t/s/abc" in err

    def test_never_fails_the_import(self, capsys):
        """The template is already registered — a session check must not undo that."""
        with patch.object(act, "ensure_session", side_effect=RuntimeError("broker down")):
            ci._maybe_activate_session(self._args(True), "acme")
        err = capsys.readouterr().err
        assert "could not check the session" in err
        assert "activate_session.py acme" in err

    def test_skipped_without_a_profile_slug(self, capsys):
        with patch.object(act, "ensure_session") as ensure:
            ci._maybe_activate_session(self._args(True), "")
        ensure.assert_not_called()
