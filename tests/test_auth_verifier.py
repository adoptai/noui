"""Tests for the cloud (platform_jwt) auth mode in compiler/mcp/auth_verifier.py.

The verifier runs on the NoUI host during compile/diagnose. It must obtain a
Tabby bearer either via the local agent-token flow or, in cloud, via the
platform-JWT token-exchange flow — mirroring the generated runtime adapter.
"""

from __future__ import annotations

import asyncio
import sys
import types
from contextlib import contextmanager
from pathlib import Path

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.activate import verify as av


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@contextmanager
def _fake_httpx(responses: dict):
    """Patch auth_verifier.httpx with a recorder; yields the POST call log."""
    log: list[tuple] = []

    class _FakeAsyncClient:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            log.append((url, json, headers))
            for suffix, payload in responses.items():
                if url.endswith(suffix):
                    return _FakeResponse(payload)
            raise AssertionError(f"unexpected POST url: {url}")

    original = av.httpx
    av.httpx = types.SimpleNamespace(AsyncClient=lambda *a, **k: _FakeAsyncClient())
    try:
        yield log
    finally:
        av.httpx = original


def _verifier(**kwargs) -> av.AuthVerifier:
    return av.AuthVerifier(auth_plan={}, server_dir=".", **kwargs)


class TestAuthModeResolution:
    def test_defaults_to_agent_token(self) -> None:
        v = _verifier()
        # No platform creds and no explicit mode → local flow.
        assert v._resolve_auth_mode() in {"agent_token", "platform_jwt"}
        # Be explicit about the no-creds case by clearing any ambient platform creds.
        v.adopt_api_url = v.adopt_client_id = v.adopt_client_secret = ""
        v.auth_mode = ""
        assert v._resolve_auth_mode() == "agent_token"

    def test_auto_platform_jwt_when_adopt_creds_present(self) -> None:
        v = _verifier(
            adopt_api_url="https://api.adopt.ai", adopt_client_id="c", adopt_client_secret="s"
        )
        v.auth_mode = ""
        assert v._resolve_auth_mode() == "platform_jwt"

    def test_explicit_mode_overrides_autodetect(self) -> None:
        v = _verifier(
            auth_mode="agent_token",
            adopt_api_url="https://api.adopt.ai",
            adopt_client_id="c",
            adopt_client_secret="s",
        )
        assert v._resolve_auth_mode() == "agent_token"


class TestBearerFlow:
    def test_platform_jwt_two_hop(self) -> None:
        v = _verifier(
            tabby_api_host="https://tabby.cloud",
            adopt_api_url="https://api.adopt.ai",
            adopt_client_id="cid",
            adopt_client_secret="sec",
            auth_mode="platform_jwt",
        )
        responses = {
            "/v1/users/api-token": {"access_token": "PLATFORM_JWT"},
            "/auth/token-exchange": {"access_token": "TABBY_JWT"},
        }
        with _fake_httpx(responses) as log:
            bearer = asyncio.run(v._get_tabby_bearer())

        assert bearer == "TABBY_JWT"
        urls = [u for (u, _j, _h) in log]
        assert urls == [
            "https://api.adopt.ai/v1/users/api-token",
            "https://tabby.cloud/auth/token-exchange",
        ]
        _u, exchange_body, _h = log[1]
        assert exchange_body == {"subject_token": "PLATFORM_JWT", "subject_token_type": "oidc_jwt"}

    def test_agent_token_flow(self) -> None:
        v = _verifier(
            tabby_api_host="https://tabby.local",
            tabby_client_id="cid",
            tabby_client_secret="sec",
            auth_mode="agent_token",
        )
        with _fake_httpx({"/auth/agent-token": {"access_token": "AGENT_JWT"}}) as log:
            bearer = asyncio.run(v._get_tabby_bearer())

        assert bearer == "AGENT_JWT"
        urls = [u for (u, _j, _h) in log]
        assert urls == ["https://tabby.local/auth/agent-token"]
        assert all("token-exchange" not in u for u in urls)
