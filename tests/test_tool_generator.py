"""Regression tests for operation code generation via server_generator._render_operation.

Two sections:
- Legacy HTTP-mode invariants (execution_mode='http'): auth strategy drives
  resolve_auth() wiring, recorded headers merge with auth headers, unauth ops
  skip the auth import, etc.
- Tabby-mode invariants (default): operations import from noui_runtime.execute
  and call execute_fetch; recorded static headers are still embedded; no httpx
  appears. resolve_auth() is injected whenever required_auth.headers is
  non-empty (a client-managed bearer/CSRF header the browser session's cookies
  alone can't supply) — for BOTH static_secret_header and tabby_credentials
  strategies. A cookie-only tabby_credentials app (required_auth.headers
  empty) needs no injection, since execute_fetch already rides the session's
  cookies via credentials:'include'. Found via a QuickBooks Online capture
  whose auth_plan was tabby_credentials + a dynamically-captured Authorization
  header — the old tabby-mode codegen never wired resolve_auth() in for
  tabby_credentials at all, so that header was silently dropped.
"""

from __future__ import annotations

import sys
from pathlib import Path

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.compile.server_generator import _render_operation


def _render_http(td: dict, *, auth_plan: dict) -> str:
    """Shorthand: render an operation in legacy HTTP mode."""
    return _render_operation(td, auth_plan=auth_plan, execution_mode="http")


def _render_tabby(td: dict, *, auth_plan: dict) -> str:
    """Shorthand: render an operation in tabby mode (default)."""
    return _render_operation(td, auth_plan=auth_plan, execution_mode="tabby")


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _simple_tool(
    name: str = "list_documents",
    method: str = "GET",
    path: str = "/documents",
    base_url: str = "https://api.example.com",
    request_headers: list[dict] | None = None,
    params: list[dict] | None = None,
) -> dict:
    return {
        "name": name,
        "method": method,
        "path": path,
        "base_url": base_url,
        "description": f"Execute {name}",
        "request_headers": request_headers or [],
        "request_body": None,
        "request_content_type": "",
        "params": params or [],
        "auth_headers": [],
        "auth_cookies": [],
    }


def _tabby_auth_plan(profile_slug: str = "example-bank") -> dict:
    return {
        "strategy": "tabby_credentials",
        "profile_slug": profile_slug,
        "profile_db_id": "",
        "required_auth": {"headers": ["Authorization"], "cookies": []},
        "fallbacks": [],
    }


def _tabby_cookie_only_auth_plan(profile_slug: str = "example-bank") -> dict:
    """tabby_credentials with no required headers — the pure session-cookie
    case, where execute_fetch's credentials:'include' is the only auth needed."""
    return {
        "strategy": "tabby_credentials",
        "profile_slug": profile_slug,
        "profile_db_id": "",
        "required_auth": {"headers": [], "cookies": ["session_id"]},
        "fallbacks": [],
    }


def _static_auth_plan(env_var: str = "ADOPT_BANK_API_KEY") -> dict:
    return {
        "strategy": "static_secret_header",
        "profile_slug": "",
        "profile_db_id": "",
        "required_auth": {"headers": ["Authorization"], "cookies": []},
        "fallbacks": [
            {
                "type": "static_secret_header",
                "header": "Authorization",
                "value_template": f"Bearer ${{{env_var}}}",
                "secret_env_var": env_var,
            }
        ],
    }


# ── Authenticated operations ─────────────────────────────────────────────────


class TestAuthenticatedOperations:
    """Legacy HTTP mode: resolve_auth wiring invariants."""

    def test_uses_resolve_auth_not_get_auth_headers(self) -> None:
        src = _render_http(_simple_tool(), auth_plan=_tabby_auth_plan())
        assert "resolve_auth" in src, "Authenticated op must call resolve_auth()"
        assert "get_auth_headers" not in src, (
            "Authenticated op must not use legacy get_auth_headers(TABBY_PROFILE_ID)"
        )

    def test_imports_resolve_auth(self) -> None:
        src = _render_http(_simple_tool(), auth_plan=_tabby_auth_plan())
        assert "from noui_runtime.auth import resolve_auth" in src

    def test_no_tabby_profile_id_constant(self) -> None:
        """TABBY_PROFILE_ID constant must not be embedded — it belongs in auth_plan.json."""
        src = _render_http(_simple_tool(), auth_plan=_tabby_auth_plan("my-slug"))
        assert "TABBY_PROFILE_ID" not in src, (
            "Profile ID must not be baked into operation module; it lives in auth_plan.json"
        )

    def test_static_auth_also_uses_resolve_auth(self) -> None:
        src = _render_http(_simple_tool(), auth_plan=_static_auth_plan())
        assert "resolve_auth" in src

    def test_headers_passed_to_httpx(self) -> None:
        src = _render_http(_simple_tool(), auth_plan=_tabby_auth_plan())
        assert "headers=" in src, "auth headers must be passed to httpx call"


# ── Header merging ───────────────────────────────────────────────────────────


class TestHeaderMerging:
    """Legacy HTTP mode: recorded+auth header merge invariants."""

    def test_recorded_headers_preserved_when_auth_present(self) -> None:
        """Non-auth recorded headers (Accept, Content-Type) must survive auth header injection."""
        tool = _simple_tool(
            request_headers=[
                {"name": "Accept", "value": "application/json"},
                {"name": "X-Request-Version", "value": "2"},
            ]
        )
        src = _render_http(tool, auth_plan=_tabby_auth_plan())
        assert "'Accept'" in src or '"Accept"' in src, (
            "Recorded Accept header must appear in generated operation source"
        )
        assert "'application/json'" in src or '"application/json"' in src, (
            "Recorded Accept header value must appear in generated source"
        )

    def test_auth_overrides_recorded_via_merge(self) -> None:
        """Auth headers from resolve_auth() must take precedence over recorded placeholders."""
        tool = _simple_tool(request_headers=[{"name": "Accept", "value": "application/json"}])
        src = _render_http(tool, auth_plan=_tabby_auth_plan())
        assert "resolve_auth" in src
        assert "_recorded" in src or "{**" in src, (
            "Generated code must merge recorded headers with auth headers"
        )

    def test_no_recorded_headers_no_merge_dict(self) -> None:
        """When there are no recorded non-auth headers, skip the _recorded merge dict."""
        tool = _simple_tool(request_headers=[])
        src = _render_http(tool, auth_plan=_tabby_auth_plan())
        assert "_recorded" not in src, (
            "When no recorded headers exist, the _recorded dict should be omitted"
        )


# ── Unauthenticated operations ───────────────────────────────────────────────


class TestUnauthenticatedOperations:
    """Legacy HTTP mode: unauth ops must skip the auth runtime entirely."""

    def test_no_auth_import_when_empty_plan(self) -> None:
        src = _render_http(_simple_tool(), auth_plan={})
        assert "resolve_auth" not in src
        assert "get_auth_headers" not in src
        assert "noui_runtime" not in src

    def test_recorded_headers_still_included_without_auth(self) -> None:
        """Even without auth, recorded static headers should appear in generated code."""
        tool = _simple_tool(request_headers=[{"name": "Accept", "value": "application/json"}])
        src = _render_http(tool, auth_plan={})
        assert "'Accept'" in src or '"Accept"' in src

    def test_no_resolve_auth_when_no_auth_plan(self) -> None:
        src = _render_http(_simple_tool(request_headers=[]), auth_plan={})
        assert "resolve_auth" not in src


# ── Operation structure ──────────────────────────────────────────────────────


class TestOperationStructure:
    """Legacy HTTP mode: rendered Python structure."""

    def test_base_url_embedded(self) -> None:
        src = _render_http(
            _simple_tool(base_url="https://api.myapp.com"), auth_plan=_tabby_auth_plan()
        )
        assert "https://api.myapp.com" in src

    def test_path_in_url_expression(self) -> None:
        src = _render_http(
            _simple_tool(path="/v2/clients/{client_id}/documents"), auth_plan=_tabby_auth_plan()
        )
        assert "/v2/clients/{client_id}/documents" in src

    def test_async_execute_function(self) -> None:
        src = _render_http(_simple_tool(), auth_plan={})
        assert "async def execute(" in src or "async def execute() -> dict:" in src

    def test_returns_json_or_text_fallback(self) -> None:
        src = _render_http(_simple_tool(), auth_plan={})
        assert "resp.json()" in src
        assert "resp.status_code" in src


# ── Tabby (default) mode ──────────────────────────────────────────────────────


class TestTabbyModeRender:
    """Execute-fetch mode invariants for _render_operation (the default)."""

    def test_imports_execute_runtime(self) -> None:
        src = _render_tabby(_simple_tool(), auth_plan=_tabby_auth_plan())
        assert "from noui_runtime.execute import" in src
        assert "execute_fetch" in src

    def test_no_httpx(self) -> None:
        src = _render_tabby(_simple_tool(), auth_plan=_tabby_auth_plan())
        assert "import httpx" not in src

    def test_cookie_only_app_has_no_resolve_auth(self) -> None:
        """Pure session-cookie auth needs no extra injection — execute_fetch's
        credentials:'include' already carries the browser session's cookies."""
        src = _render_tabby(_simple_tool(), auth_plan=_tabby_cookie_only_auth_plan())
        assert "resolve_auth" not in src

    def test_tabby_credentials_with_required_header_calls_resolve_auth(self) -> None:
        """The QBO-shaped case: tabby_credentials strategy but a required
        non-cookie header (a dynamically-captured bearer) — execute_fetch's
        cookie-only credentials:'include' can't supply this, so resolve_auth()
        must be called and merged into headers."""
        src = _render_tabby(_simple_tool(), auth_plan=_tabby_auth_plan())
        assert "from noui_runtime.auth import resolve_auth" in src
        assert "await resolve_auth()" in src

    def test_static_secret_header_still_calls_resolve_auth(self) -> None:
        """Regression: the pre-existing static_secret_header case must keep working."""
        src = _render_tabby(_simple_tool(), auth_plan=_static_auth_plan())
        assert "from noui_runtime.auth import resolve_auth" in src
        assert "await resolve_auth()" in src

    def test_profile_slug_embedded(self) -> None:
        """PROFILE_SLUG must come from the auth_plan so execute_fetch can resolve the session."""
        src = _render_tabby(
            _simple_tool(base_url="https://api.myapp.com"), auth_plan=_tabby_auth_plan()
        )
        assert "PROFILE_SLUG = 'example-bank'" in src

    def test_base_url_still_embedded(self) -> None:
        src = _render_tabby(
            _simple_tool(base_url="https://api.myapp.com"), auth_plan=_tabby_auth_plan()
        )
        assert "https://api.myapp.com" in src

    def test_recorded_headers_preserved(self) -> None:
        """Recorded static headers must still be passed to execute_fetch."""
        tool = _simple_tool(request_headers=[{"name": "Accept", "value": "application/json"}])
        src = _render_tabby(tool, auth_plan={})
        assert "'Accept'" in src or '"Accept"' in src
        assert "'application/json'" in src or '"application/json"' in src

    def test_query_params_url_encoded(self) -> None:
        tool = _simple_tool(
            params=[{"name": "limit", "type": "int", "source": "query", "required": False}]
        )
        src = _render_tabby(tool, auth_plan={})
        assert "urllib.parse.urlencode" in src

    def test_unauth_still_generates_execute_fetch(self) -> None:
        """Even with no auth, default mode still generates in-browser execution."""
        src = _render_tabby(_simple_tool(), auth_plan={})
        assert "execute_fetch" in src
        assert "PROFILE_SLUG" in src
