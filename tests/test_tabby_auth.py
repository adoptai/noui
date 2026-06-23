"""Tests for runtime/tabby_auth.py — pure helper _build_headers_from_data and mocked network calls."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from noui_core.auth import _build_headers_from_data, get_auth_headers, get_auth_headers_sync


class TestBuildHeadersFromData:
    def test_empty_payload(self) -> None:
        result = _build_headers_from_data({})
        assert result == {}

    def test_headers_only(self) -> None:
        data = {
            "headers": [
                {"name": "Authorization", "value": "Bearer tok123"},
                {"name": "X-Api-Key", "value": "secret"},
            ],
            "cookies": [],
        }
        result = _build_headers_from_data(data)
        assert result["Authorization"] == "Bearer tok123"
        assert result["X-Api-Key"] == "secret"

    def test_cookies_merged_into_cookie_header(self) -> None:
        data = {
            "headers": [],
            "cookies": [
                {"name": "session", "value": "abc123"},
                {"name": "csrf_token", "value": "xyz"},
            ],
        }
        result = _build_headers_from_data(data)
        assert "Cookie" in result
        assert "session=abc123" in result["Cookie"]
        assert "csrf_token=xyz" in result["Cookie"]

    def test_single_cookie(self) -> None:
        data = {"cookies": [{"name": "sid", "value": "qwerty"}]}
        result = _build_headers_from_data(data)
        assert result["Cookie"] == "sid=qwerty"

    def test_empty_cookie_name_skipped(self) -> None:
        data = {"cookies": [{"name": "", "value": "ignored"}]}
        result = _build_headers_from_data(data)
        assert "Cookie" not in result

    def test_headers_and_cookies_combined(self) -> None:
        data = {
            "headers": [{"name": "Authorization", "value": "Bearer tok"}],
            "cookies": [{"name": "session", "value": "s123"}],
        }
        result = _build_headers_from_data(data)
        assert result["Authorization"] == "Bearer tok"
        assert "session=s123" in result["Cookie"]

    def test_existing_cookie_header_merged(self) -> None:
        data = {
            "headers": [{"name": "Cookie", "value": "existing=val"}],
            "cookies": [{"name": "new_cookie", "value": "newval"}],
        }
        result = _build_headers_from_data(data)
        assert "existing=val" in result["Cookie"]
        assert "new_cookie=newval" in result["Cookie"]

    def test_empty_header_name_skipped(self) -> None:
        data = {"headers": [{"name": "", "value": "ignored"}]}
        result = _build_headers_from_data(data)
        assert result == {}

    def test_missing_keys_in_header_dict(self) -> None:
        data: dict = {"headers": [{}]}
        result = _build_headers_from_data(data)
        assert result == {}


# ── get_auth_headers (async, mocked) ──────────────────────────────────────────


class TestGetAuthHeaders:
    def test_calls_tabby_endpoint(self) -> None:
        payload = {"headers": [{"name": "Authorization", "value": "Bearer tok"}], "cookies": []}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=payload)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("noui_core.auth.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(get_auth_headers("my-profile"))

        assert result["Authorization"] == "Bearer tok"

    def test_raises_on_http_error(self) -> None:
        req_mock = MagicMock()
        resp_mock = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("404", request=req_mock, response=resp_mock)
        )
        mock_resp.json = MagicMock(return_value={})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("noui_core.auth.httpx.AsyncClient", return_value=mock_client),
            pytest.raises(httpx.HTTPStatusError),
        ):
            asyncio.run(get_auth_headers("bad-profile"))


# ── get_auth_headers_sync (mocked) ───────────────────────────────────────────


class TestGetAuthHeadersSync:
    def test_returns_headers(self) -> None:
        payload = {"headers": [{"name": "X-Api-Key", "value": "key123"}], "cookies": []}
        raw = json.dumps(payload).encode()

        mock_response = MagicMock()
        mock_response.read = MagicMock(return_value=raw)
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)

        with patch("noui_core.auth.urllib.request.urlopen", return_value=mock_response):
            result = get_auth_headers_sync("my-profile")

        assert result["X-Api-Key"] == "key123"
