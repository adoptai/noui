"""Tests for compiler/mcp/api_doc_generator.py"""

from __future__ import annotations

from noui_core.compile.api_doc_generator import (
    _extract_base_url,
    _format_auth_detail,
    _format_auth_summary,
    _infer_source,
    _schema_like_body,
    generate_api_markdown,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _tool_def(
    name: str = "get_users",
    method: str = "GET",
    path: str = "/v1/users",
    params: list[dict] | None = None,
    auth_headers: list[str] | None = None,
    auth_cookies: list[str] | None = None,
    request_headers: list[dict] | None = None,
    request_body: dict | None = None,
    auth_source: str = "",
    base_url: str = "https://api.example.com",
) -> dict:
    return {
        "name": name,
        "description": f"Test tool: {name}",
        "method": method,
        "path": path,
        "url": f"{base_url}{path}",
        "base_url": base_url,
        "params": params or [],
        "auth_headers": auth_headers or [],
        "auth_cookies": auth_cookies or [],
        "auth_source": auth_source,
        "request_headers": request_headers or [],
        "request_body": request_body,
        "request_content_type": "",
        "response_status": 200,
    }


# ── generate_api_markdown ────────────────────────────────────────────────────


class TestGenerateApiMarkdown:
    def test_header_contains_app_name(self) -> None:
        md = generate_api_markdown(
            server_id="srv1",
            app_name="My App",
            app_slug="my-app",
            workflow_name="My Workflow",
            tool_defs=[],
            tabby_profile_id="",
        )
        assert "# API Reference: My App" in md

    def test_no_tools_message(self) -> None:
        md = generate_api_markdown(
            server_id="s",
            app_name="A",
            app_slug="a",
            workflow_name="W",
            tool_defs=[],
            tabby_profile_id="",
        )
        assert "No APIs detected" in md

    def test_summary_table_present(self) -> None:
        td = _tool_def()
        md = generate_api_markdown(
            server_id="s",
            app_name="A",
            app_slug="a",
            workflow_name="W",
            tool_defs=[td],
            tabby_profile_id="",
        )
        assert "## Summary" in md
        assert "get_users" in md

    def test_per_tool_section_present(self) -> None:
        td = _tool_def(name="create_order", method="POST", path="/v1/orders")
        md = generate_api_markdown(
            server_id="s",
            app_name="A",
            app_slug="a",
            workflow_name="W",
            tool_defs=[td],
            tabby_profile_id="",
        )
        assert "## `create_order`" in md
        assert "POST" in md

    def test_generated_at_included(self) -> None:
        md = generate_api_markdown(
            server_id="s",
            app_name="A",
            app_slug="a",
            workflow_name="W",
            tool_defs=[],
            tabby_profile_id="",
            generated_at="2024-01-01T00:00:00Z",
        )
        assert "2024-01-01T00:00:00Z" in md

    def test_params_table_in_tool_section(self) -> None:
        td = _tool_def(
            params=[{"name": "user_id", "type": "string", "required": True, "source": "path"}]
        )
        md = generate_api_markdown(
            server_id="s",
            app_name="A",
            app_slug="a",
            workflow_name="W",
            tool_defs=[td],
            tabby_profile_id="",
        )
        assert "user_id" in md

    def test_no_params_message(self) -> None:
        td = _tool_def(params=[])
        md = generate_api_markdown(
            server_id="s",
            app_name="A",
            app_slug="a",
            workflow_name="W",
            tool_defs=[td],
            tabby_profile_id="",
        )
        assert "no parameters" in md

    def test_request_body_shape_shown(self) -> None:
        td = _tool_def(
            method="POST",
            request_body={"name": "Alice", "age": 30},
        )
        md = generate_api_markdown(
            server_id="s",
            app_name="A",
            app_slug="a",
            workflow_name="W",
            tool_defs=[td],
            tabby_profile_id="",
        )
        assert "Captured Body Shape" in md
        assert "<string>" in md  # name
        assert "<int>" in md  # age


# ── _format_auth_summary ──────────────────────────────────────────────────────


class TestFormatAuthSummary:
    def test_no_auth_no_profile(self) -> None:
        td = _tool_def()
        assert _format_auth_summary(td, "") == "none"

    def test_no_auth_with_tabby_profile(self) -> None:
        td = _tool_def()
        result = _format_auth_summary(td, "profile-uuid-12345678")
        assert "profile-" in result

    def test_auth_headers_shown(self) -> None:
        td = _tool_def(auth_headers=["Authorization"])
        result = _format_auth_summary(td, "")
        assert "Authorization" in result
        assert "headers" in result

    def test_auth_cookies_shown(self) -> None:
        td = _tool_def(auth_cookies=["session", "csrf_token"])
        result = _format_auth_summary(td, "")
        assert "session" in result

    def test_auth_source_prefix(self) -> None:
        td = _tool_def(auth_cookies=["sid"], auth_source="tabby")
        result = _format_auth_summary(td, "")
        assert "Tabby" in result or "tabby" in result.lower()


# ── _format_auth_detail ───────────────────────────────────────────────────────


class TestFormatAuthDetail:
    def test_no_auth_returns_none(self) -> None:
        td = _tool_def()
        assert _format_auth_detail(td, "") == "none"

    def test_tabby_profile_shown(self) -> None:
        td = _tool_def()
        result = _format_auth_detail(td, "my-profile-123")
        assert "Tabby" in result or "my-profi" in result

    def test_auth_headers_detail(self) -> None:
        td = _tool_def(auth_headers=["Authorization"])
        result = _format_auth_detail(td, "")
        assert "Authorization" in result


# ── _infer_source ─────────────────────────────────────────────────────────────


class TestInferSource:
    def test_path_param_in_braces(self) -> None:
        assert _infer_source("user_id", "/v1/users/{user_id}", "GET") == "path"

    def test_get_method_defaults_to_query(self) -> None:
        assert _infer_source("filter", "/v1/users", "GET") == "query"

    def test_delete_defaults_to_query(self) -> None:
        assert _infer_source("cascade", "/v1/users", "DELETE") == "query"

    def test_post_defaults_to_body(self) -> None:
        assert _infer_source("name", "/v1/users", "POST") == "body"

    def test_put_defaults_to_body(self) -> None:
        assert _infer_source("email", "/v1/users/123", "PUT") == "body"


# ── _extract_base_url ─────────────────────────────────────────────────────────


class TestExtractBaseUrl:
    def test_standard_https(self) -> None:
        assert _extract_base_url("https://api.example.com/v1/users") == "https://api.example.com"

    def test_with_port(self) -> None:
        assert _extract_base_url("http://localhost:8000/api") == "http://localhost:8000"

    def test_empty_string(self) -> None:
        assert _extract_base_url("") == "?"

    def test_no_path(self) -> None:
        assert _extract_base_url("https://api.example.com") == "https://api.example.com"


# ── _schema_like_body ─────────────────────────────────────────────────────────


class TestSchemaLikeBody:
    def test_string_values(self) -> None:
        result = _schema_like_body({"name": "Alice"})
        assert "<string>" in result
        assert "name" in result

    def test_int_values(self) -> None:
        result = _schema_like_body({"count": 42})
        assert "<int>" in result

    def test_bool_values(self) -> None:
        result = _schema_like_body({"active": True})
        assert "<bool>" in result

    def test_float_values(self) -> None:
        result = _schema_like_body({"score": 3.14})
        assert "<float>" in result

    def test_sensitive_keys_redacted(self) -> None:
        result = _schema_like_body({"password": "secret123"})
        assert "<redacted>" in result
        assert "secret123" not in result

    def test_nested_dict(self) -> None:
        result = _schema_like_body({"user": {"name": "Alice"}})
        assert "user" in result
        assert "<string>" in result

    def test_list_value(self) -> None:
        result = _schema_like_body({"tags": ["a", "b"]})
        assert "[...]" in result

    def test_empty_body(self) -> None:
        assert _schema_like_body({}) == ""

    def test_none_body(self) -> None:
        assert _schema_like_body(None) == ""  # type: ignore[arg-type]
