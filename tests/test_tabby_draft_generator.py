"""Tests for compiler/login/tabby_draft_generator.py — pure helpers."""

from __future__ import annotations

import json

from noui_core.compile.login_assets import (
    _build_selector,
    _is_redirect_hop,
    _selector_confidence,
    _url_domain,
    _url_origin,
)

# ── _selector_confidence ──────────────────────────────────────────────────────


class TestSelectorConfidence:
    def test_data_testid_is_high(self) -> None:
        ev = {"data_attrs_json": json.dumps({"data-testid": "email-input"})}
        assert _selector_confidence(ev) == "high"

    def test_data_test_is_high(self) -> None:
        ev = {"data_attrs_json": json.dumps({"data-test": "btn-submit"})}
        assert _selector_confidence(ev) == "high"

    def test_stable_element_id_is_high(self) -> None:
        ev = {"element_id": "login-button"}
        assert _selector_confidence(ev) == "high"

    def test_random_uuid_id_not_high(self) -> None:
        # UUIDs with 8+ hex chars are considered unstable
        ev = {"element_id": "a1b2c3d4e5f6"}
        result = _selector_confidence(ev)
        # Should be medium or low, not high
        assert result != "high"

    def test_field_name_is_medium(self) -> None:
        ev = {"field_name": "email"}
        assert _selector_confidence(ev) == "medium"

    def test_autocomplete_is_medium(self) -> None:
        ev = {"autocomplete": "username"}
        assert _selector_confidence(ev) == "medium"

    def test_aria_label_is_medium(self) -> None:
        ev = {"aria_label": "Email address"}
        assert _selector_confidence(ev) == "medium"

    def test_placeholder_is_medium(self) -> None:
        ev = {"placeholder": "Enter email"}
        assert _selector_confidence(ev) == "medium"

    def test_no_hints_is_low(self) -> None:
        ev = {"tag_name": "div"}
        assert _selector_confidence(ev) == "low"

    def test_invalid_data_attrs_json_handled(self) -> None:
        ev = {"data_attrs_json": "not-json"}
        # Should not crash, falls through to other checks
        result = _selector_confidence(ev)
        assert result in ("high", "medium", "low")


# ── _build_selector ───────────────────────────────────────────────────────────


class TestBuildSelector:
    def test_data_testid_priority(self) -> None:
        ev = {
            "data_attrs_json": json.dumps({"data-testid": "email-field"}),
            "element_id": "some-id",
        }
        assert _build_selector(ev) == '[data-testid="email-field"]'

    def test_data_test_priority(self) -> None:
        ev = {"data_attrs_json": json.dumps({"data-test": "submit-btn"})}
        assert _build_selector(ev) == '[data-test="submit-btn"]'

    def test_stable_id_selector(self) -> None:
        ev = {"element_id": "login-form"}
        assert _build_selector(ev) == "#login-form"

    def test_random_id_skipped(self) -> None:
        ev = {
            "element_id": "a1b2c3d4e5f6",  # looks random (8+ hex)
            "field_name": "username",
            "tag_name": "input",
        }
        selector = _build_selector(ev)
        # Should use field_name fallback
        assert 'name="username"' in selector

    def test_name_attribute_selector(self) -> None:
        ev = {"field_name": "email", "tag_name": "input"}
        assert _build_selector(ev) == 'input[name="email"]'

    def test_autocomplete_selector(self) -> None:
        ev = {"autocomplete": "current-password", "tag_name": "input"}
        assert _build_selector(ev) == 'input[autocomplete="current-password"]'

    def test_aria_label_selector(self) -> None:
        ev = {"aria_label": "Password field", "tag_name": "input", "input_type": "password"}
        selector = _build_selector(ev)
        assert 'aria-label="Password field"' in selector

    def test_placeholder_selector(self) -> None:
        ev = {"placeholder": "Enter email", "tag_name": "input", "input_type": "email"}
        selector = _build_selector(ev)
        assert 'placeholder="Enter email"' in selector

    def test_fallback_to_existing_selector(self) -> None:
        ev = {"selector": "div.form-group > input", "tag_name": "input"}
        assert _build_selector(ev) == "div.form-group > input"

    def test_fallback_to_tag_name(self) -> None:
        ev = {"tag_name": "button"}
        assert _build_selector(ev) == "button"

    def test_invalid_data_attrs_json_handled(self) -> None:
        ev = {"data_attrs_json": "bad json", "tag_name": "input"}
        # Should not crash
        result = _build_selector(ev)
        assert isinstance(result, str)

    def test_default_tag_input_when_missing(self) -> None:
        ev: dict = {}  # No tag_name, no selector
        result = _build_selector(ev)
        assert result == "input"  # default tag


# ── _url_origin ───────────────────────────────────────────────────────────────


class TestUrlOrigin:
    def test_standard_https(self) -> None:
        assert _url_origin("https://example.com/path") == "https://example.com"

    def test_http_with_port(self) -> None:
        assert _url_origin("http://localhost:8080/api/v1") == "http://localhost:8080"

    def test_empty_url(self) -> None:
        # Should return something without crashing
        result = _url_origin("")
        assert isinstance(result, str)

    def test_url_with_query(self) -> None:
        assert _url_origin("https://example.com/path?foo=bar") == "https://example.com"


# ── _url_domain ───────────────────────────────────────────────────────────────


class TestUrlDomain:
    def test_standard_domain(self) -> None:
        assert _url_domain("https://example.com/path") == "example.com"

    def test_domain_with_port(self) -> None:
        assert _url_domain("http://localhost:3000/") == "localhost:3000"

    def test_empty_url(self) -> None:
        result = _url_domain("")
        assert isinstance(result, str)


# ── _is_redirect_hop ──────────────────────────────────────────────────────────


class TestIsRedirectHop:
    def test_callback_path_is_redirect(self) -> None:
        assert (
            _is_redirect_hop(
                "https://app.example.com/login",
                "https://app.example.com/callback",
            )
            is True
        )

    def test_sso_path_is_redirect(self) -> None:
        assert (
            _is_redirect_hop(
                "https://app.example.com/login",
                "https://app.example.com/sso/saml",
            )
            is True
        )

    def test_auth_path_is_redirect(self) -> None:
        assert (
            _is_redirect_hop(
                "https://app.example.com/login",
                "https://app.example.com/auth/token",
            )
            is True
        )

    def test_oauth_path_is_redirect(self) -> None:
        assert (
            _is_redirect_hop(
                "https://app.example.com/",
                "https://app.example.com/oauth/authorize",
            )
            is True
        )

    def test_different_domain_not_redirect(self) -> None:
        assert (
            _is_redirect_hop(
                "https://app.example.com/login",
                "https://other.com/callback",
            )
            is False
        )

    def test_regular_page_not_redirect(self) -> None:
        assert (
            _is_redirect_hop(
                "https://app.example.com/login",
                "https://app.example.com/dashboard",
            )
            is False
        )

    def test_empty_urls(self) -> None:
        # Should not crash
        result = _is_redirect_hop("", "")
        assert isinstance(result, bool)
