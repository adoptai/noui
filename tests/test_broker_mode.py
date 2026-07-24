"""Tests for NoUI ``broker`` auth mode (harness control-plane broker).

In broker mode NoUI runs inside the credential-less Agent-Harness sandbox: it
sends an opaque per-conversation capability token to the broker URL and never
mints or holds a real Tabby credential. The broker swaps the capability for the
real per-user Tabby bearer. See plans/adoptai-workflows/noui-control-plane-broker-plan.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from noui_core import tabby_client
from noui_core.activate import register
from noui_core.capture import recording
from noui_core.config import settings


@pytest.fixture
def broker(monkeypatch):
    """Put the process in broker mode with a capability token."""
    monkeypatch.setattr(settings, "tabby_auth_mode", "broker")
    monkeypatch.setattr(settings, "broker_token", "cap-tok-abc123")
    # Ensure no real Tabby creds are present — the sandbox has none.
    monkeypatch.delenv("TABBY_CLIENT_ID", raising=False)
    monkeypatch.delenv("TABBY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TABBY_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(settings, "tabby_admin_token", "")


class TestBrokerModeFlag:
    def test_broker_mode_true(self, monkeypatch):
        monkeypatch.setattr(settings, "tabby_auth_mode", "broker")
        assert settings.broker_mode() is True

    @pytest.mark.parametrize("mode", ["", "agent_token", "platform_jwt"])
    def test_broker_mode_false(self, monkeypatch, mode):
        monkeypatch.setattr(settings, "tabby_auth_mode", mode)
        assert settings.broker_mode() is False


class TestResolveAgentToken:
    def test_returns_capability_token_without_client_creds(self, broker):
        # No TABBY_CLIENT_ID/SECRET set, yet resolution must succeed.
        with patch.object(tabby_client, "get_agent_token") as mint:
            assert recording.resolve_agent_token() == "cap-tok-abc123"
            mint.assert_not_called()  # broker mode must never mint a Tabby token

    def test_missing_capability_token_raises(self, broker, monkeypatch):
        monkeypatch.setattr(settings, "broker_token", "")
        with pytest.raises(RuntimeError, match="NOUI_BROKER_TOKEN"):
            recording.resolve_agent_token()

    def test_non_broker_mode_still_mints(self, monkeypatch):
        monkeypatch.setattr(settings, "tabby_auth_mode", "agent_token")
        monkeypatch.setenv("TABBY_CLIENT_ID", "cid")
        monkeypatch.setenv("TABBY_CLIENT_SECRET", "csec")
        with patch.object(tabby_client, "get_agent_token", return_value="minted") as mint:
            assert recording.resolve_agent_token() == "minted"
            mint.assert_called_once_with("cid", "csec")


class TestResolveAdminToken:
    def test_returns_capability_token(self, broker):
        assert register.resolve_admin_token() == "cap-tok-abc123"

    def test_missing_capability_token_raises(self, broker, monkeypatch):
        monkeypatch.setattr(settings, "broker_token", "")
        with pytest.raises(RuntimeError, match="NOUI_BROKER_TOKEN"):
            register.resolve_admin_token()

    def test_non_broker_mode_uses_admin_env(self, monkeypatch):
        monkeypatch.setattr(settings, "tabby_auth_mode", "")
        monkeypatch.setenv("TABBY_ADMIN_TOKEN", "admintok")
        assert register.resolve_admin_token() == "admintok"


class TestControlPlaneRoutesCapabilityToBroker:
    """The capability token must flow into the actual Tabby HTTP call unchanged,
    so the broker (sitting at ``tabby_api_host``) can swap it for the real bearer."""

    def test_recording_session_sends_capability_bearer(self, broker, monkeypatch):
        monkeypatch.setattr(settings, "tabby_api_host", "http://broker.local")
        seen = {}

        def fake_http(method, path, body=None, token=None, timeout=15, retries=0):
            seen.update(method=method, path=path, token=token)
            return {"session_id": "s1", "vnc_url": "https://vnc"}

        monkeypatch.setattr(tabby_client, "_tabby_http", fake_http)
        recording.start("workflow", "https://example.com")
        assert seen["path"] == "/recording/sessions"
        assert seen["token"] == "cap-tok-abc123"  # capability, not a Tabby token

    def test_execute_browser_sends_capability_bearer(self, broker, monkeypatch):
        seen = {}

        def fake_http(method, path, body=None, token=None, timeout=15, retries=0):
            seen.update(path=path, token=token)
            return {"success": True, "data": {}}

        monkeypatch.setattr(tabby_client, "_tabby_http", fake_http)
        tabby_client.execute_browser(
            "some-profile", "navigate", {"url": "https://x"}, token=recording.resolve_agent_token()
        )
        assert seen["path"] == "/execute/browser"
        assert seen["token"] == "cap-tok-abc123"
