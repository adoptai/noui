"""Recording sessions must accept ``platform_jwt`` credentials.

``capture.recording.resolve_agent_token`` used to accept only broker mode and
agent_token, while ``activate.register.resolve_admin_token`` already preferred
platform_jwt. A runtime configured the platform way could therefore list app
templates but not provision the recording session needed to create one. These
tests pin the shared preference order:

    broker  >  platform_jwt (ADOPT_*)  >  agent_token (TABBY_CLIENT_*)

with an explicitly pinned NOUI_TABBY_AUTH_MODE failing loudly rather than
silently resolving a different identity.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from noui_core import tabby_client
from noui_core.capture import recording
from noui_core.config import settings

PLATFORM_ENV = {
    "ADOPT_API_URL": "https://api.adopt.ai",
    "ADOPT_CLIENT_ID": "pat-cid",
    "ADOPT_CLIENT_SECRET": "pat-secret",
}


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("TABBY_CLIENT_ID", "TABBY_CLIENT_SECRET", "ADOPT_API_URL",
                "ADOPT_CLIENT_ID", "ADOPT_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "tabby_auth_mode", "")
    monkeypatch.setattr(settings, "broker_token", "")


def _set(monkeypatch, env: dict) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)


class TestPlatformJwtAccepted:
    def test_explicit_platform_jwt_mode_exchanges_platform_creds(self, clean_env, monkeypatch):
        monkeypatch.setattr(settings, "tabby_auth_mode", "platform_jwt")
        _set(monkeypatch, PLATFORM_ENV)
        with patch.object(tabby_client, "get_platform_tabby_token", return_value="tabby-jwt") as ex:
            assert recording.resolve_agent_token() == "tabby-jwt"
        ex.assert_called_once_with("https://api.adopt.ai", "pat-cid", "pat-secret")

    def test_platform_creds_preferred_when_mode_unpinned(self, clean_env, monkeypatch):
        """Matches resolve_admin_token: platform creds win over agent creds."""
        _set(monkeypatch, {**PLATFORM_ENV, "TABBY_CLIENT_ID": "cid", "TABBY_CLIENT_SECRET": "csec"})
        with patch.object(tabby_client, "get_platform_tabby_token", return_value="tabby-jwt"), \
             patch.object(tabby_client, "get_agent_token") as mint:
            assert recording.resolve_agent_token() == "tabby-jwt"
            mint.assert_not_called()

    def test_trailing_slash_stripped_from_api_url(self, clean_env, monkeypatch):
        _set(monkeypatch, {**PLATFORM_ENV, "ADOPT_API_URL": "https://api.adopt.ai/"})
        with patch.object(tabby_client, "get_platform_tabby_token", return_value="t") as ex:
            recording.resolve_agent_token()
        assert ex.call_args[0][0] == "https://api.adopt.ai"

    def test_explicit_platform_jwt_without_creds_raises(self, clean_env, monkeypatch):
        monkeypatch.setattr(settings, "tabby_auth_mode", "platform_jwt")
        with pytest.raises(RuntimeError, match="ADOPT_API_URL"):
            recording.resolve_agent_token()


class TestAgentTokenUnaffected:
    def test_agent_creds_used_when_no_platform_creds(self, clean_env, monkeypatch):
        _set(monkeypatch, {"TABBY_CLIENT_ID": "cid", "TABBY_CLIENT_SECRET": "csec"})
        with patch.object(tabby_client, "get_agent_token", return_value="minted") as mint:
            assert recording.resolve_agent_token() == "minted"
        mint.assert_called_once_with("cid", "csec")

    def test_pinned_agent_token_never_falls_through_to_platform(self, clean_env, monkeypatch):
        """A pinned mode must fail loudly, not silently resolve another identity."""
        monkeypatch.setattr(settings, "tabby_auth_mode", "agent_token")
        _set(monkeypatch, PLATFORM_ENV)
        with patch.object(tabby_client, "get_platform_tabby_token") as ex:
            with pytest.raises(RuntimeError, match="TABBY_CLIENT_ID"):
                recording.resolve_agent_token()
            ex.assert_not_called()

    def test_no_credentials_at_all_names_both_paths(self, clean_env):
        with pytest.raises(RuntimeError) as exc:
            recording.resolve_agent_token()
        assert "ADOPT_CLIENT_ID" in str(exc.value) and "TABBY_CLIENT_ID" in str(exc.value)


class TestBrokerStillWins:
    def test_broker_mode_ignores_platform_creds(self, clean_env, monkeypatch):
        monkeypatch.setattr(settings, "tabby_auth_mode", "broker")
        monkeypatch.setattr(settings, "broker_token", "cap-tok")
        _set(monkeypatch, PLATFORM_ENV)
        with patch.object(tabby_client, "get_platform_tabby_token") as ex:
            assert recording.resolve_agent_token() == "cap-tok"
            ex.assert_not_called()
