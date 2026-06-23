"""Regression tests for compiler/runtime/auth_adapter.py.

Critical invariants:
- Generated auth.py must NOT contain the old /runtime/credentials/{profile_id} route.
- Generated auth.py MUST use POST /auth/agent-token + POST /credentials/request.
- Generated auth.py MUST load dotenv from the noui root.
- Generated auth.py MUST expose resolve_auth() (new) and get_auth_headers() (compat shim).
- Static secret strategy must fail clearly with a human-readable message when env var missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add noui root to sys.path so compiler imports work without installation
_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.activate.auth_adapter import generate_auth_adapter


def _generated() -> str:
    return generate_auth_adapter("http://localhost:8080")


class TestDeprecatedRouteNotGenerated:
    """Phase 1 regression: old /runtime/credentials/{profile_id} must not appear."""

    def test_no_runtime_credentials_route(self) -> None:
        src = _generated()
        assert "/runtime/credentials/" not in src, (
            "Generated auth.py must not use the deprecated GET /runtime/credentials/{profile_id} route"
        )

    def test_no_get_runtime_credentials(self) -> None:
        src = _generated()
        assert "client.get" not in src or "runtime/credentials" not in src, (
            "Generated auth.py must not do GET /runtime/credentials/"
        )


class TestCorrectTabbyFlow:
    """Generated auth.py must use the 2-step Tabby credential flow."""

    def test_uses_agent_token_endpoint(self) -> None:
        src = _generated()
        assert "/auth/agent-token" in src, (
            "Generated auth.py must POST /auth/agent-token to exchange client credentials"
        )

    def test_uses_credentials_request_endpoint(self) -> None:
        src = _generated()
        assert "/credentials/request" in src, (
            "Generated auth.py must POST /credentials/request to fetch live credentials"
        )

    def test_credentials_request_uses_profile_slug_field(self) -> None:
        """The credentials/request call must pass profile_id (slug, not DB UUID)."""
        src = _generated()
        assert '"profile_id"' in src or "'profile_id'" in src, (
            "Generated auth.py must pass profile_id (the slug) to /credentials/request"
        )


class TestEnvVarLoading:
    """Generated auth.py must load noui/.env and read client credentials from env."""

    def test_loads_dotenv(self) -> None:
        src = _generated()
        assert "load_dotenv" in src or "dotenv" in src, (
            "Generated auth.py must call load_dotenv() to pick up noui/.env"
        )

    def test_reads_client_id(self) -> None:
        src = _generated()
        assert "TABBY_CLIENT_ID" in src, (
            "Generated auth.py must read TABBY_CLIENT_ID from environment"
        )

    def test_reads_client_secret(self) -> None:
        src = _generated()
        assert "TABBY_CLIENT_SECRET" in src, (
            "Generated auth.py must read TABBY_CLIENT_SECRET from environment"
        )

    def test_missing_credentials_diagnostic(self) -> None:
        """Missing client credentials should produce a human-readable error, not a raw exception."""
        src = _generated()
        assert "noui tabby setup" in src or "TABBY_CLIENT_ID" in src, (
            "Generated auth.py must include guidance when TABBY_CLIENT_ID/SECRET are missing"
        )


class TestResolveAuthFunction:
    """Generated auth.py must expose resolve_auth() as the primary entry point."""

    def test_resolve_auth_defined(self) -> None:
        src = _generated()
        assert "async def resolve_auth()" in src, (
            "Generated auth.py must define resolve_auth() as the primary auth entry point"
        )

    def test_get_auth_headers_compat_shim(self) -> None:
        """get_auth_headers(profile_id) must still exist for backward compatibility."""
        src = _generated()
        assert "async def get_auth_headers(" in src, (
            "Generated auth.py must keep get_auth_headers() as a legacy shim"
        )


class TestStaticSecretStrategy:
    """resolve_auth() must support static_secret_header strategy via auth_plan.json."""

    def test_static_secret_header_handler(self) -> None:
        src = _generated()
        assert "static_secret_header" in src, (
            "Generated auth.py must handle static_secret_header strategy"
        )

    def test_missing_env_var_error_message(self) -> None:
        """Missing secret env var must produce a clear error, not a traceback."""
        src = _generated()
        assert "Missing required secret" in src or "missing" in src.lower(), (
            "Generated auth.py must raise a clear error when a required secret env var is absent"
        )

    def test_reads_auth_plan_json(self) -> None:
        src = _generated()
        assert "auth_plan.json" in src, "Generated auth.py must load strategy from auth_plan.json"


class TestTabbyApiHostConfig:
    """The baked-in Tabby host must be overridable via env var."""

    def test_custom_host_baked_in(self) -> None:
        src = generate_auth_adapter("http://tabby.internal:9090")
        assert "http://tabby.internal:9090" in src, (
            "Custom tabby_api_host must appear in the generated file"
        )

    def test_env_var_override(self) -> None:
        src = _generated()
        # Must read the single TABBY_API_URL env var
        assert "TABBY_API_URL" in src, (
            "Generated auth.py must allow overriding the Tabby base URL via TABBY_API_URL"
        )


# ---------------------------------------------------------------------------
# Cloud (platform_jwt) auth mode
# ---------------------------------------------------------------------------

import asyncio
import os
import tempfile
import types
from contextlib import contextmanager


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        return None

    def json(self) -> dict:
        return self._payload


@contextmanager
def _generated_module(env: dict):
    """Exec the generated auth.py into a fresh namespace under a controlled env.

    Module-level code reads ADOPT_*/TABBY_* at import time and ``_resolve_auth_mode``
    reads NOUI_TABBY_AUTH_MODE live, so the env stays set for the duration of the
    ``with`` block (matching a real deployment where env is stable). A dummy
    ``__file__`` keeps the .env walk-up from finding a real .env.
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
        g: dict = {"__file__": os.path.join(tempfile.gettempdir(), "noui_gen_auth_test.py")}
        exec(compile(_generated(), "<generated>", "exec"), g)
        yield g
    finally:
        os.environ.clear()
        os.environ.update(saved)


@contextmanager
def _fake_httpx(module: dict, responses: dict):
    """Patch the generated module's ``httpx`` with a recorder. Yields the call log."""
    log: list[tuple] = []

    class _FakeAsyncClient:
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

    original = module["httpx"]
    module["httpx"] = types.SimpleNamespace(AsyncClient=lambda *a, **k: _FakeAsyncClient())
    try:
        yield log
    finally:
        module["httpx"] = original


class TestGeneratedSourceCompiles:
    def test_compiles(self) -> None:
        """f-string brace escaping must produce valid Python."""
        compile(generate_auth_adapter("http://localhost:8080"), "<generated>", "exec")


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


class TestPlatformJwtFlow:
    _CREDS = {
        "credentials": {
            "headers": [{"name": "Authorization", "value": "Bearer LIVE"}],
            "cookies": [],
        }
    }

    def test_two_hop_exchange_then_credentials(self) -> None:
        env = {
            "ADOPT_API_URL": "https://api.adopt.ai",
            "ADOPT_CLIENT_ID": "cid",
            "ADOPT_CLIENT_SECRET": "sec",
            "TABBY_API_URL": "https://tabby.cloud",
        }
        responses = {
            "/v1/users/api-token": {"access_token": "PLATFORM_JWT"},
            "/auth/token-exchange": {"access_token": "TABBY_JWT", "expires_in": 3600},
            "/credentials/request": self._CREDS,
        }
        with _generated_module(env) as g, _fake_httpx(g, responses) as log:
            headers = asyncio.run(g["_tabby_credentials"]("my-profile"))

        urls = [u for (u, _j, _h) in log]
        assert urls == [
            "https://api.adopt.ai/v1/users/api-token",
            "https://tabby.cloud/auth/token-exchange",
            "https://tabby.cloud/credentials/request",
        ]
        # token-exchange receives the platform JWT as an oidc_jwt subject token
        _u, exchange_body, _h = log[1]
        assert exchange_body == {"subject_token": "PLATFORM_JWT", "subject_token_type": "oidc_jwt"}
        # credentials/request is authorized with the exchanged Tabby JWT
        _u, _b, creds_headers = log[2]
        assert creds_headers == {"Authorization": "Bearer TABBY_JWT"}
        assert headers == {"Authorization": "Bearer LIVE"}

    def test_agent_token_mode_does_not_call_token_exchange(self) -> None:
        env = {
            "TABBY_CLIENT_ID": "cid",
            "TABBY_CLIENT_SECRET": "sec",
            "TABBY_API_URL": "https://tabby.local",
        }
        responses = {
            "/auth/agent-token": {"access_token": "AGENT_JWT", "expires_in": 3600},
            "/credentials/request": self._CREDS,
        }
        with _generated_module(env) as g, _fake_httpx(g, responses) as log:
            asyncio.run(g["_tabby_credentials"]("my-profile"))

        urls = [u for (u, _j, _h) in log]
        assert urls == [
            "https://tabby.local/auth/agent-token",
            "https://tabby.local/credentials/request",
        ]
        assert all("token-exchange" not in u for u in urls)

    def test_token_is_cached_across_calls(self) -> None:
        env = {
            "ADOPT_API_URL": "https://api.adopt.ai",
            "ADOPT_CLIENT_ID": "cid",
            "ADOPT_CLIENT_SECRET": "sec",
            "TABBY_API_URL": "https://tabby.cloud",
        }
        responses = {
            "/v1/users/api-token": {"access_token": "PLATFORM_JWT"},
            "/auth/token-exchange": {"access_token": "TABBY_JWT", "expires_in": 3600},
            "/credentials/request": self._CREDS,
        }
        with _generated_module(env) as g, _fake_httpx(g, responses) as log:

            async def _two() -> None:
                await g["_tabby_credentials"]("p")
                await g["_tabby_credentials"]("p")

            asyncio.run(_two())

        urls = [u for (u, _j, _h) in log]
        # Token endpoints hit exactly once; credentials/request hit twice.
        assert urls.count("https://api.adopt.ai/v1/users/api-token") == 1
        assert urls.count("https://tabby.cloud/auth/token-exchange") == 1
        assert urls.count("https://tabby.cloud/credentials/request") == 2
