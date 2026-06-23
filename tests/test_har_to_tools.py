"""Tests for compiler/mcp/har_to_tools.py"""

from __future__ import annotations

import json

import pytest
from noui_core.compile.har_to_tools import (
    HarValidationError,
    _body_to_params,
    _id_param_name,
    _is_api_call,
    _path_template,
    _tool_name,
    har_to_tool_defs,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _entry(
    url: str = "https://api.example.com/v1/users",
    method: str = "GET",
    resp_mime: str = "application/json",
    status: int = 200,
    headers: list[dict] | None = None,
    post_data: dict | None = None,
    qs: list[dict] | None = None,
    initiator_type: str = "",
    resource_type: str = "",
) -> dict:
    return {
        "request": {
            "method": method,
            "url": url,
            "headers": headers or [],
            "queryString": qs or [],
            "postData": post_data or {},
        },
        "response": {
            "status": status,
            "content": {"mimeType": resp_mime},
        },
        "_initiatorType": initiator_type,
        "_resourceType": resource_type,
    }


def _har(entries: list[dict]) -> dict:
    return {"log": {"entries": entries}}


# ── _is_api_call ──────────────────────────────────────────────────────────────


class TestIsApiCall:
    def test_options_not_api(self) -> None:
        assert _is_api_call(_entry(method="OPTIONS")) is False

    def test_head_not_api(self) -> None:
        assert _is_api_call(_entry(method="HEAD")) is False

    def test_data_url_not_api(self) -> None:
        assert _is_api_call(_entry(url="data:text/plain;base64,abc")) is False

    def test_blob_url_not_api(self) -> None:
        assert _is_api_call(_entry(url="blob:http://example.com/abc")) is False

    def test_skip_extension_js(self) -> None:
        assert _is_api_call(_entry(url="https://cdn.example.com/bundle.js")) is False

    def test_skip_extension_css(self) -> None:
        assert _is_api_call(_entry(url="https://cdn.example.com/style.css")) is False

    def test_skip_extension_png(self) -> None:
        assert _is_api_call(_entry(url="https://cdn.example.com/img.png")) is False

    def test_skip_analytics_url(self) -> None:
        assert _is_api_call(_entry(url="https://cdn.example.com/analytics/track")) is False

    def test_skip_redirect_status(self) -> None:
        assert _is_api_call(_entry(status=302)) is False

    def test_skip_no_content_status(self) -> None:
        assert _is_api_call(_entry(status=204)) is False

    def test_json_response_is_api(self) -> None:
        assert _is_api_call(_entry(resp_mime="application/json")) is True

    def test_xml_response_is_api(self) -> None:
        assert _is_api_call(_entry(resp_mime="text/xml")) is True

    def test_form_urlencoded_request_is_api(self) -> None:
        entry = _entry(
            method="POST",
            resp_mime="text/html",
            post_data={"mimeType": "application/x-www-form-urlencoded", "text": "k=v"},
        )
        assert _is_api_call(entry) is True

    def test_fetch_initiator_is_api(self) -> None:
        assert _is_api_call(_entry(initiator_type="fetch", resp_mime="")) is True

    def test_xhr_initiator_is_api(self) -> None:
        assert _is_api_call(_entry(initiator_type="xmlhttprequest", resp_mime="")) is True

    def test_fetch_resource_type_is_api(self) -> None:
        assert _is_api_call(_entry(resource_type="fetch", resp_mime="")) is True

    def test_post_without_mime_is_api(self) -> None:
        assert _is_api_call(_entry(method="POST", resp_mime="")) is True

    def test_delete_is_api(self) -> None:
        assert _is_api_call(_entry(method="DELETE", resp_mime="")) is True

    def test_get_without_json_not_api(self) -> None:
        assert _is_api_call(_entry(method="GET", resp_mime="text/html")) is False


# ── _path_template ────────────────────────────────────────────────────────────


class TestPathTemplate:
    def test_static_path(self) -> None:
        template, params = _path_template("/v1/users")
        assert template == "/v1/users"
        assert params == []

    def test_uuid_segment_replaced(self) -> None:
        template, params = _path_template("/v1/users/123e4567-e89b-12d3-a456-426614174000")
        assert "{user_id}" in template
        assert "user_id" in params

    def test_numeric_id_replaced(self) -> None:
        template, params = _path_template("/v1/orders/42")
        assert "{order_id}" in template
        assert "order_id" in params

    def test_hex_id_replaced(self) -> None:
        template, params = _path_template("/v1/items/abcdef1234567890")
        assert len(params) == 1

    def test_multiple_ids(self) -> None:
        template, params = _path_template("/v1/users/42/posts/99")
        assert len(params) == 2

    def test_preserves_static_segments(self) -> None:
        template, _ = _path_template("/v1/users/42/profile")
        assert "profile" in template

    def test_root_path(self) -> None:
        template, params = _path_template("/")
        assert template == "/"
        assert params == []


# ── _id_param_name ────────────────────────────────────────────────────────────


class TestIdParamName:
    def test_plural_singularized(self) -> None:
        assert _id_param_name("users") == "user_id"

    def test_singular_unchanged(self) -> None:
        assert _id_param_name("order") == "order_id"

    def test_empty_returns_id(self) -> None:
        assert _id_param_name("") == "id"

    def test_hyphenated(self) -> None:
        result = _id_param_name("line-items")
        assert result.endswith("_id")


# ── _tool_name ────────────────────────────────────────────────────────────────


class TestToolName:
    def test_get_prefix(self) -> None:
        name = _tool_name("GET", "/v1/users", set())
        assert name.startswith("get_")

    def test_post_prefix(self) -> None:
        name = _tool_name("POST", "/v1/orders", set())
        assert name.startswith("create_")

    def test_put_prefix(self) -> None:
        name = _tool_name("PUT", "/v1/users/{id}", set())
        assert name.startswith("update_")

    def test_delete_prefix(self) -> None:
        name = _tool_name("DELETE", "/v1/users/{id}", set())
        assert name.startswith("delete_")

    def test_uniqueness(self) -> None:
        used: set[str] = {"get_users"}
        name = _tool_name("GET", "/v1/users", used)
        assert name != "get_users"
        assert "users" in name

    def test_path_params_excluded_from_name(self) -> None:
        name = _tool_name("GET", "/v1/users/{user_id}/posts", set())
        assert "user_id" not in name
        assert "users" in name or "posts" in name


# ── har_to_tool_defs ─────────────────────────────────────────────────────────


def _htd(har: dict, workflow_name: str = "W", profile: str = "") -> list[dict]:
    return har_to_tool_defs(har, workflow_name=workflow_name, tabby_profile_id=profile)


class TestHarToToolDefs:
    def test_empty_entries_raises(self) -> None:
        with pytest.raises(HarValidationError, match="no entries"):
            _htd({"log": {"entries": []}})

    def test_missing_log_raises(self) -> None:
        with pytest.raises(HarValidationError, match="log.entries"):
            _htd({})

    def test_log_without_entries_raises(self) -> None:
        with pytest.raises(HarValidationError, match="log.entries"):
            _htd({"log": {"version": "1.2"}})

    def test_non_dict_har_raises(self) -> None:
        with pytest.raises(HarValidationError, match="JSON object"):
            _htd("not a dict")  # type: ignore[arg-type]

    def test_single_entry(self) -> None:
        har = _har([_entry(url="https://api.example.com/v1/users")])
        result = _htd(har, workflow_name="My Workflow")
        assert len(result) == 1
        assert result[0]["method"] == "GET"
        assert result[0]["path"] == "/v1/users"

    def test_deduplication(self) -> None:
        har = _har(
            [
                _entry(url="https://api.example.com/v1/users"),
                _entry(url="https://api.example.com/v1/users"),
            ]
        )
        result = _htd(har)
        assert len(result) == 1

    def test_different_methods_not_deduped(self) -> None:
        har = _har(
            [
                _entry(url="https://api.example.com/v1/users", method="GET"),
                _entry(
                    url="https://api.example.com/v1/users",
                    method="POST",
                    resp_mime="application/json",
                ),
            ]
        )
        result = _htd(har)
        assert len(result) == 2

    def test_only_static_assets_raises(self) -> None:
        har = _har(
            [
                _entry(url="https://cdn.example.com/app.js", resp_mime="", status=200),
                _entry(url="https://cdn.example.com/style.css", resp_mime="", status=200),
            ]
        )
        with pytest.raises(HarValidationError, match="none look like API calls"):
            _htd(har)

    def test_only_redirects_raises(self) -> None:
        har = _har(
            [
                _entry(url="https://example.com/redirect", status=302, resp_mime=""),
            ]
        )
        with pytest.raises(HarValidationError, match="none look like API calls"):
            _htd(har)

    def test_mixed_static_and_api_keeps_api(self) -> None:
        har = _har(
            [
                _entry(url="https://cdn.example.com/app.js", resp_mime="", status=200),
                _entry(url="https://api.example.com/v1/users"),
            ]
        )
        result = _htd(har)
        assert len(result) == 1
        assert "users" in result[0]["url"]

    def test_auth_header_detected(self) -> None:
        har = _har(
            [
                _entry(
                    url="https://api.example.com/v1/users",
                    headers=[{"name": "Authorization", "value": "Bearer tok"}],
                )
            ]
        )
        result = _htd(har, profile="profile-id")
        assert result[0]["auth_headers"] == ["Authorization"]

    def test_query_params_extracted(self) -> None:
        har = _har(
            [
                _entry(
                    url="https://api.example.com/v1/users",
                    qs=[{"name": "limit", "value": "10"}],
                )
            ]
        )
        result = _htd(har)
        params = {p["name"]: p for p in result[0]["params"]}
        assert "limit" in params
        assert params["limit"]["source"] == "query"

    def test_json_body_extracted(self) -> None:
        body = json.dumps({"name": "Alice", "email": "alice@example.com"})
        har = _har(
            [
                _entry(
                    url="https://api.example.com/v1/users",
                    method="POST",
                    post_data={"mimeType": "application/json", "text": body},
                    resp_mime="application/json",
                )
            ]
        )
        result = _htd(har)
        params = {p["name"] for p in result[0]["params"]}
        assert "name" in params
        assert "email" in params

    def test_path_params_extracted(self) -> None:
        har = _har([_entry(url="https://api.example.com/v1/users/42/posts")])
        result = _htd(har)
        params = {p["name"]: p for p in result[0]["params"]}
        assert any(p["source"] == "path" for p in params.values())


# ── _body_to_params ───────────────────────────────────────────────────────────


class TestBodyToParams:
    def test_string_type(self) -> None:
        params = _body_to_params({"name": "Alice"})
        assert params[0]["type"] == "string"

    def test_int_type(self) -> None:
        params = _body_to_params({"count": 42})
        assert params[0]["type"] == "int"

    def test_bool_type(self) -> None:
        params = _body_to_params({"active": True})
        assert params[0]["type"] == "bool"

    def test_float_type(self) -> None:
        params = _body_to_params({"score": 3.14})
        assert params[0]["type"] == "float"

    def test_empty_body(self) -> None:
        assert _body_to_params({}) == []
        assert _body_to_params(None) == []  # type: ignore[arg-type]

    def test_all_required(self) -> None:
        params = _body_to_params({"a": "x", "b": "y"})
        assert all(p["required"] for p in params)

    def test_source_is_body(self) -> None:
        params = _body_to_params({"field": "value"})
        assert params[0]["source"] == "body"
