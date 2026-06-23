"""Tests for compiler/runtime/execute_adapter.py (the default `tabby` runtime).

Covers A2 — porting the platform_jwt two-step into the execute adapter so the
default runtime can carry owner_user_id and trigger template auto-provisioning.

Invariants:
- The generated execute.py compiles (f-string brace escaping is correct).
- agent_token mode stays the working default (POST /auth/agent-token only).
- platform_jwt mode does the two-hop exchange (Adopt → Tabby token-exchange),
  then calls /execute/fetch with the exchanged Tabby JWT.
- Bearer tokens are cached per mode across repeated execute_fetch() calls.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.activate.execute_adapter import generate_execute_adapter


def _generated() -> str:
    return generate_execute_adapter()


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        return None

    def json(self) -> dict:
        return self._payload


@contextmanager
def _generated_module(env: dict):
    """Exec the generated execute.py into a fresh namespace under a controlled env.

    Module-level code reads ADOPT_*/TABBY_* live via os.environ, so the env stays
    set for the duration of the ``with`` block. A dummy ``__file__`` keeps the
    .env walk-up from finding a real .env.
    """
    saved = dict(os.environ)
    try:
        for key in (
            "NOUI_TABBY_AUTH_MODE",
            "ADOPT_API_URL",
            "ADOPT_CLIENT_ID",
            "ADOPT_CLIENT_SECRET",
            "TABBY_CLIENT_ID",
            "TABBY_CLIENT_SECRET",
            "TABBY_API_URL",
            "TABBY_API_HOST",
            "NOUI_ENV_FILE",
        ):
            os.environ.pop(key, None)
        os.environ.update(env)
        g: dict = {"__file__": os.path.join(tempfile.gettempdir(), "noui_gen_execute_test.py")}
        exec(compile(_generated(), "<generated>", "exec"), g)
        yield g
    finally:
        os.environ.clear()
        os.environ.update(saved)


@contextmanager
def _fake_httpx(module: dict, responses: dict):
    """Patch the generated module's ``httpx`` with a recorder. Yields the call log.

    The execute adapter passes ``timeout=`` to ``post``, so the fake accepts it.
    """
    log: list[tuple] = []

    class _FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            log.append((url, json, headers))
            for suffix, payload in responses.items():
                if url.endswith(suffix):
                    return payload if isinstance(payload, _FakeResponse) else _FakeResponse(payload)
            raise AssertionError(f"unexpected POST url: {url}")

    original = module["httpx"]
    module["httpx"] = types.SimpleNamespace(AsyncClient=lambda *a, **k: _FakeAsyncClient())
    try:
        yield log
    finally:
        module["httpx"] = original


class TestGeneratedSourceCompiles:
    def test_compiles(self) -> None:
        compile(_generated(), "<generated>", "exec")


class TestAuthModeResolution:
    def test_defaults_to_agent_token(self) -> None:
        with _generated_module({}) as g:
            assert g["_resolve_auth_mode"]() == "agent_token"

    def test_auto_platform_jwt_when_adopt_creds_present(self) -> None:
        with _generated_module(
            {
                "ADOPT_API_URL": "https://api.adopt.ai",
                "ADOPT_CLIENT_ID": "c",
                "ADOPT_CLIENT_SECRET": "s",
            }
        ) as g:
            assert g["_resolve_auth_mode"]() == "platform_jwt"

    def test_explicit_mode_overrides_autodetect(self) -> None:
        with _generated_module(
            {
                "NOUI_TABBY_AUTH_MODE": "agent_token",
                "ADOPT_API_URL": "https://api.adopt.ai",
                "ADOPT_CLIENT_ID": "c",
                "ADOPT_CLIENT_SECRET": "s",
            }
        ) as g:
            assert g["_resolve_auth_mode"]() == "agent_token"

    def test_partial_adopt_creds_stay_agent_token(self) -> None:
        # Only ADOPT_API_URL set (no client id/secret) must NOT flip to platform_jwt.
        with _generated_module({"ADOPT_API_URL": "https://api.adopt.ai"}) as g:
            assert g["_resolve_auth_mode"]() == "agent_token"


_FETCH_OK = {"status": 200, "headers": {}, "body": '{"ok": true}'}


class TestAgentTokenExecute:
    def test_agent_token_is_default_path(self) -> None:
        env = {
            "TABBY_CLIENT_ID": "cid",
            "TABBY_CLIENT_SECRET": "sec",
            "TABBY_API_URL": "https://tabby.local",
        }
        responses = {
            "/auth/agent-token": {"access_token": "AGENT_JWT", "expires_in": 3600},
            "/execute/fetch": _FETCH_OK,
        }
        with _generated_module(env) as g, _fake_httpx(g, responses) as log:
            result = asyncio.run(g["execute_fetch"]("my-profile", "https://x/api"))

        urls = [u for (u, _j, _h) in log]
        assert urls == [
            "https://tabby.local/auth/agent-token",
            "https://tabby.local/execute/fetch",
        ]
        assert all("token-exchange" not in u for u in urls)
        # /execute/fetch is authorized with the agent token.
        _u, _b, fetch_headers = log[1]
        assert fetch_headers == {"Authorization": "Bearer AGENT_JWT"}
        assert result == {"ok": True}

    def test_missing_agent_creds_raise_actionable_error(self) -> None:
        with (
            _generated_module({"TABBY_API_URL": "https://tabby.local"}) as g,
            pytest.raises(RuntimeError) as exc,
        ):
            asyncio.run(g["execute_fetch"]("p", "https://x/api"))
        msg = str(exc.value)
        assert "TABBY_CLIENT_ID" in msg
        assert "platform_jwt" in msg


class TestPlatformJwtExecute:
    def test_two_hop_exchange_then_execute(self) -> None:
        env = {
            "ADOPT_API_URL": "https://api.adopt.ai",
            "ADOPT_CLIENT_ID": "cid",
            "ADOPT_CLIENT_SECRET": "sec",
            "TABBY_API_URL": "https://tabby.cloud",
        }
        responses = {
            "/v1/users/api-token": {"access_token": "PLATFORM_JWT"},
            "/auth/token-exchange": {"access_token": "TABBY_JWT", "expires_in": 3600},
            "/execute/fetch": _FETCH_OK,
        }
        with _generated_module(env) as g, _fake_httpx(g, responses) as log:
            asyncio.run(g["execute_fetch"]("my-profile", "https://x/api"))

        urls = [u for (u, _j, _h) in log]
        assert urls == [
            "https://api.adopt.ai/v1/users/api-token",
            "https://tabby.cloud/auth/token-exchange",
            "https://tabby.cloud/execute/fetch",
        ]
        # token-exchange receives the platform JWT as an oidc_jwt subject token.
        _u, exchange_body, _h = log[1]
        assert exchange_body == {"subject_token": "PLATFORM_JWT", "subject_token_type": "oidc_jwt"}
        # /execute/fetch is authorized with the exchanged (owner-scoped) Tabby JWT.
        _u, _b, fetch_headers = log[2]
        assert fetch_headers == {"Authorization": "Bearer TABBY_JWT"}

    def test_platform_jwt_does_not_require_agent_creds(self) -> None:
        # Regression: platform_jwt must work with NO TABBY_CLIENT_ID/SECRET set.
        env = {
            "NOUI_TABBY_AUTH_MODE": "platform_jwt",
            "ADOPT_API_URL": "https://api.adopt.ai",
            "ADOPT_CLIENT_ID": "cid",
            "ADOPT_CLIENT_SECRET": "sec",
            "TABBY_API_URL": "https://tabby.cloud",
        }
        responses = {
            "/v1/users/api-token": {"access_token": "PLATFORM_JWT"},
            "/auth/token-exchange": {"access_token": "TABBY_JWT", "expires_in": 3600},
            "/execute/fetch": _FETCH_OK,
        }
        with _generated_module(env) as g, _fake_httpx(g, responses):
            # Must not raise the "TABBY_CLIENT_ID required" error.
            asyncio.run(g["execute_fetch"]("p", "https://x/api"))


class TestTokenCaching:
    def test_bearer_cached_across_calls(self) -> None:
        env = {
            "ADOPT_API_URL": "https://api.adopt.ai",
            "ADOPT_CLIENT_ID": "cid",
            "ADOPT_CLIENT_SECRET": "sec",
            "TABBY_API_URL": "https://tabby.cloud",
        }
        responses = {
            "/v1/users/api-token": {"access_token": "PLATFORM_JWT"},
            "/auth/token-exchange": {"access_token": "TABBY_JWT", "expires_in": 3600},
            "/execute/fetch": _FETCH_OK,
        }
        with _generated_module(env) as g, _fake_httpx(g, responses) as log:

            async def _two() -> None:
                await g["execute_fetch"]("p", "https://x/a")
                await g["execute_fetch"]("p", "https://x/b")

            asyncio.run(_two())

        urls = [u for (u, _j, _h) in log]
        # Token endpoints hit exactly once; /execute/fetch hit twice.
        assert urls.count("https://api.adopt.ai/v1/users/api-token") == 1
        assert urls.count("https://tabby.cloud/auth/token-exchange") == 1
        assert urls.count("https://tabby.cloud/execute/fetch") == 2

    def test_agent_token_cached_across_calls(self) -> None:
        env = {
            "TABBY_CLIENT_ID": "cid",
            "TABBY_CLIENT_SECRET": "sec",
            "TABBY_API_URL": "https://tabby.local",
        }
        responses = {
            "/auth/agent-token": {"access_token": "AGENT_JWT", "expires_in": 3600},
            "/execute/fetch": _FETCH_OK,
        }
        with _generated_module(env) as g, _fake_httpx(g, responses) as log:

            async def _two() -> None:
                await g["execute_fetch"]("p", "https://x/a")
                await g["execute_fetch"]("p", "https://x/b")

            asyncio.run(_two())

        urls = [u for (u, _j, _h) in log]
        assert urls.count("https://tabby.local/auth/agent-token") == 1
        assert urls.count("https://tabby.local/execute/fetch") == 2


class TestSourceShape:
    def test_no_client_side_cdp_connection(self) -> None:
        # No client-side CDP/WebSocket connection — execute is plain HTTP. The
        # CDP port :9222 must never appear as a connection target.
        src = _generated()
        assert ":9222" not in src
        assert "Runtime.evaluate" not in src

    def test_calls_execute_fetch_endpoint(self) -> None:
        assert "/execute/fetch" in _generated()
