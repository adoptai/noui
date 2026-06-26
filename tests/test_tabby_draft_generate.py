"""Tests for compiler/login/tabby_draft_generator.py — generate() function."""

from __future__ import annotations

import json

import pytest
from noui_core.compile.login_assets import (
    _analyze_har,
    build_app_template_payload,
    egress_allowlist_from_domains,
    generate,
    merge_template_export_policy,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _session(
    app_name: str = "My App",
    login_url: str = "https://app.example.com/login",
    session_id: str = "sess-001",
) -> dict:
    return {"id": session_id, "app_name": app_name, "login_url": login_url}


def _click(
    event_type: str = "input",
    field_role: str = "username",
    field_name: str = "username",
    value: str = "alice",
    element_id: str = "username-input",
    tag_name: str = "input",
    input_type: str = "text",
) -> dict:
    return {
        "event_type": event_type,
        "field_role": field_role,
        "field_name": field_name,
        "value": value,
        "element_id": element_id,
        "tag_name": tag_name,
        "input_type": input_type,
        "selector": f"#{element_id}",
        "is_redacted": False,
        "data_attrs_json": None,
        "autocomplete": None,
        "aria_label": None,
        "placeholder": None,
    }


def _url_event(to_url: str, from_url: str = "") -> dict:
    return {"to_url": to_url, "from_url": from_url}


# ── generate() ───────────────────────────────────────────────────────────────


class TestGenerate:
    def test_returns_required_keys(self) -> None:
        result = generate(_session(), [], [])
        assert "recording" in result
        assert "application_draft" in result
        assert "service_profile_draft" in result
        assert "review_items" in result
        assert "validation" in result

    def test_recording_has_session_id(self) -> None:
        result = generate(_session(session_id="test-123"), [], [])
        assert result["recording"]["session_id"] == "test-123"

    def test_app_name_used(self) -> None:
        result = generate(_session(app_name="My Cool App"), [], [])
        app = result["application_draft"]
        assert "my-cool-app" in app.get("profile_id", "") or "My Cool App" in str(app)

    def test_application_draft_sets_execute_enabled(self) -> None:
        # A4: execute_enabled must be true or /execute/fetch is dead in K8s.
        result = generate(_session(), [], [])
        assert result["application_draft"]["execute_enabled"] is True

    def _steps(self, result: dict) -> list[dict]:
        """Steps are in application_draft.login_config.steps."""
        return result["application_draft"]["login_config"]["steps"]

    def test_login_url_in_steps(self) -> None:
        result = generate(_session(login_url="https://app.example.com/login"), [], [])
        steps = self._steps(result)
        assert any(s.get("url") == "https://app.example.com/login" for s in steps)

    def test_no_login_url_uses_placeholder(self) -> None:
        session = {"id": "s1", "app_name": "App", "login_url": ""}
        result = generate(session, [], [])
        issues = result["validation"]["issues"]
        assert any("placeholder" in i.lower() for i in issues)

    def test_url_events_determine_first_url(self) -> None:
        session = {"id": "s1", "app_name": "App", "login_url": ""}
        url_events = [_url_event("https://observed.example.com/login")]
        result = generate(session, [], url_events)
        steps = self._steps(result)
        assert any("observed.example.com" in str(s) for s in steps)

    def test_username_fill_step_generated(self) -> None:
        # fill steps (${USERNAME}) are the stored-credential rendering — opt in.
        clicks = [_click(event_type="input", field_role="username", value="alice")]
        result = generate(_session(), clicks, [], manual_credentials=False)
        steps = self._steps(result)
        fill_steps = [
            s for s in steps if s.get("action") == "fill" and s.get("value") == "${USERNAME}"
        ]
        assert len(fill_steps) >= 1

    def test_password_fill_step_generated(self) -> None:
        clicks = [
            _click(event_type="input", field_role="password", input_type="password", value="secret")
        ]
        result = generate(_session(), clicks, [], manual_credentials=False)
        steps = self._steps(result)
        fill_steps = [
            s for s in steps if s.get("action") == "fill" and s.get("value") == "${PASSWORD}"
        ]
        assert len(fill_steps) >= 1

    def test_password_fill_has_sensitive_flag(self) -> None:
        clicks = [
            _click(event_type="input", field_role="password", input_type="password", value="s")
        ]
        result = generate(_session(), clicks, [], manual_credentials=False)
        steps = self._steps(result)
        pw_steps = [s for s in steps if s.get("value") == "${PASSWORD}"]
        assert pw_steps  # not vacuous
        assert all(s.get("sensitive") is True for s in pw_steps)

    def test_default_credential_ref_is_manual_not_k8s(self) -> None:
        # Regression: default must be manual: — never an implicit k8s secret.
        clicks = [_click(event_type="input", field_role="username", value="alice")]
        result = generate(_session(), clicks, [])
        cref = result["application_draft"]["login_config"]["credential_ref"]
        assert cref == "manual:"

    def test_stored_mode_uses_k8s_secret(self) -> None:
        clicks = [_click(event_type="input", field_role="username", value="alice")]
        result = generate(_session(), clicks, [], manual_credentials=False)
        cref = result["application_draft"]["login_config"]["credential_ref"]
        assert cref.startswith("k8s:secret/")

    def test_platform_mode_uses_manual_credential_ref(self) -> None:
        clicks = [_click(event_type="input", field_role="username", value="alice")]
        result = generate(_session(), clicks, [], auth_mode="platform_jwt")
        cref = result["application_draft"]["login_config"]["credential_ref"]
        assert cref == "manual:"

    def test_platform_mode_emits_human_input_for_credentials(self) -> None:
        clicks = [
            _click(event_type="input", field_role="username", value="alice"),
            _click(
                event_type="input",
                field_role="password",
                input_type="password",
                value="s",
                element_id="password-input",
                field_name="password",
            ),
        ]
        result = generate(_session(), clicks, [], auth_mode="platform_jwt")
        steps = self._steps(result)
        hi = [s for s in steps if s.get("action") == "request_human_input"]
        kinds = {s.get("input_type") for s in hi}
        assert "email" in kinds and "password" in kinds
        # No stored-credential placeholders in platform mode.
        assert not [s for s in steps if s.get("value") in ("${USERNAME}", "${PASSWORD}")]
        pw = [s for s in hi if s.get("input_type") == "password"]
        assert all(s.get("sensitive") is True for s in pw)

    def test_click_step_generated(self) -> None:
        clicks = [
            {
                "event_type": "click",
                "field_role": "",
                "tag_name": "button",
                "input_type": "submit",
                "text_content": "Login",
                "selector": "button[type='submit']",
                "element_id": "",
                "field_name": "",
                "value": "",
                "is_redacted": False,
                "data_attrs_json": None,
                "autocomplete": None,
                "aria_label": None,
                "placeholder": None,
            }
        ]
        result = generate(_session(), clicks, [])
        steps = self._steps(result)
        click_steps = [s for s in steps if s.get("action") == "click"]
        assert len(click_steps) >= 1

    def test_otp_wait_for_step(self) -> None:
        clicks = [_click(event_type="input", field_role="otp", value="123456")]
        result = generate(_session(), clicks, [])
        steps = self._steps(result)
        otp_steps = [s for s in steps if s.get("action") == "wait_for"]
        assert len(otp_steps) >= 1

    def test_low_confidence_produces_review_item(self) -> None:
        clicks = [
            {
                "event_type": "input",
                "field_role": "username",
                "tag_name": "input",
                "input_type": "text",
                "selector": "input",
                "element_id": "",
                "field_name": "",
                "value": "alice",
                "is_redacted": False,
                "data_attrs_json": None,
                "autocomplete": None,
                "aria_label": None,
                "placeholder": None,
            }
        ]
        result = generate(_session(), clicks, [])
        review = result["review_items"]
        assert any(r.get("type") == "selector_confidence" for r in review)

    def test_deduplication_of_consecutive_inputs(self) -> None:
        clicks = [
            _click(event_type="input", field_role="username", value="ali"),
            _click(event_type="input", field_role="username", value="alice"),
        ]
        result = generate(_session(), clicks, [], manual_credentials=False)
        steps = self._steps(result)
        fill_steps = [s for s in steps if s.get("value") == "${USERNAME}"]
        assert len(fill_steps) == 1

    def test_field_role_inferred_from_input_type(self) -> None:
        clicks = [
            {
                "event_type": "input",
                "field_role": "",
                "tag_name": "input",
                "input_type": "password",
                "field_name": "",
                "element_id": "pwd",
                "value": "secret",
                "selector": "#pwd",
                "is_redacted": False,
                "data_attrs_json": None,
                "autocomplete": None,
                "aria_label": None,
                "placeholder": None,
            }
        ]
        result = generate(_session(), clicks, [], manual_credentials=False)
        steps = self._steps(result)
        pw_steps = [s for s in steps if s.get("value") == "${PASSWORD}"]
        assert len(pw_steps) >= 1

    def test_field_role_inferred_from_email_field_name(self) -> None:
        clicks = [
            {
                "event_type": "input",
                "field_role": "",
                "tag_name": "input",
                "input_type": "text",
                "field_name": "email",
                "element_id": "email",
                "value": "alice@example.com",
                "selector": "#email",
                "is_redacted": False,
                "data_attrs_json": None,
                "autocomplete": None,
                "aria_label": None,
                "placeholder": None,
            }
        ]
        result = generate(_session(), clicks, [], manual_credentials=False)
        steps = self._steps(result)
        user_steps = [s for s in steps if s.get("value") == "${USERNAME}"]
        assert len(user_steps) >= 1

    def test_select_step_generated(self) -> None:
        clicks = [
            {
                "event_type": "input",
                "field_role": "",
                "tag_name": "select",
                "input_type": "",
                "field_name": "region",
                "element_id": "region",
                "value": "us-east",
                "selector": "#region",
                "is_redacted": False,
                "data_attrs_json": None,
                "autocomplete": None,
                "aria_label": None,
                "placeholder": None,
            }
        ]
        result = generate(_session(), clicks, [])
        steps = self._steps(result)
        select_steps = [s for s in steps if s.get("action") == "select"]
        assert len(select_steps) >= 1

    def test_abcd_url_event_format(self) -> None:
        # Test abcd metadata_json format for URL events
        url_events = [
            {
                "to_url": "",
                "from_url": "",
                "metadata_json": json.dumps(
                    {"url": "https://app.example.com/dashboard", "from_url": ""}
                ),
            }
        ]
        # Should not crash
        result = generate(_session(), [], url_events)
        assert "service_profile_draft" in result

    def test_with_har_data(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "url": "https://app.example.com/api/login",
                            "headers": [
                                {"name": "Set-Cookie", "value": "session=abc123"},
                                {"name": "Authorization", "value": "Bearer tok"},
                            ],
                        },
                        "response": {
                            "headers": [
                                {"name": "set-cookie", "value": "session=xyz; Path=/"},
                            ]
                        },
                    }
                ]
            }
        }
        result = generate(_session(), [], [], har=har)
        assert "service_profile_draft" in result

    def test_validation_generator_valid(self) -> None:
        result = generate(_session(), [], [])
        assert "generator_valid" in result["validation"]


# ── extra_egress_allowlist (egress loop closure) ───────────────────────────────


class TestEgressAllowlist:
    def test_helper_collapses_to_registrable_suffix(self) -> None:
        out = egress_allowlist_from_domains(
            ["www.expedia.com", "c.trvl-media.com", "www.expedia.com"]
        )
        assert out == [".expedia.com", ".trvl-media.com"]

    def test_helper_handles_compound_tld(self) -> None:
        assert egress_allowlist_from_domains(["login.acme.co.uk"]) == [".acme.co.uk"]

    def test_helper_skips_ip_localhost_and_bare(self) -> None:
        assert egress_allowlist_from_domains(["127.0.0.1", "localhost", "single", "", None]) == []

    def _har_two_domains(self) -> dict:
        return {
            "log": {
                "entries": [
                    {
                        "request": {"url": "https://www.expedia.com/login", "headers": []},
                        "response": {"headers": []},
                    },
                    {
                        "request": {"url": "https://c.trvl-media.com/asset.png", "headers": []},
                        "response": {"headers": []},
                    },
                ]
            }
        }

    def test_application_draft_populated_from_har(self) -> None:
        result = generate(_session(), [], [], har=self._har_two_domains())
        allowlist = result["application_draft"]["extra_egress_allowlist"]
        assert ".expedia.com" in allowlist
        assert ".trvl-media.com" in allowlist

    def test_template_payload_carries_allowlist(self) -> None:
        result = generate(_session(), [], [], har=self._har_two_domains())
        template = build_app_template_payload(
            result["application_draft"], result["service_profile_draft"]
        )
        assert ".expedia.com" in template["extra_egress_allowlist"]

    def test_merge_unions_allowlist_across_recordings(self) -> None:
        existing = {"extra_egress_allowlist": [".okta.com"]}
        new_payload = {"extra_egress_allowlist": [".expedia.com"]}
        merged = merge_template_export_policy(existing, new_payload)
        assert set(merged["extra_egress_allowlist"]) == {".okta.com", ".expedia.com"}


# ── _analyze_har ──────────────────────────────────────────────────────────────


class TestAnalyzeHar:
    def test_none_returns_defaults(self) -> None:
        result = _analyze_har(None)
        assert result["has_cookies"] is False
        assert result["has_auth_headers"] is False
        assert result["has_csrf"] is False

    def test_set_cookie_detected(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "request": {"url": "https://example.com/login", "headers": []},
                        "response": {
                            "headers": [{"name": "set-cookie", "value": "session=abc123; Path=/"}]
                        },
                    }
                ]
            }
        }
        result = _analyze_har(har)
        assert result["has_cookies"] is True
        assert "session" in result["set_cookie_headers"]

    def test_auth_header_detected(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "url": "https://example.com/api",
                            "headers": [{"name": "Authorization", "value": "Bearer tok"}],
                        },
                        "response": {"headers": []},
                    }
                ]
            }
        }
        result = _analyze_har(har)
        assert result["has_auth_headers"] is True

    def test_csrf_header_detected(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "url": "https://example.com/api",
                            "headers": [{"name": "X-CSRF-Token", "value": "abc"}],
                        },
                        "response": {"headers": []},
                    }
                ]
            }
        }
        result = _analyze_har(har)
        assert result["has_csrf"] is True

    def test_domains_collected(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "request": {"url": "https://api.example.com/v1/data", "headers": []},
                        "response": {"headers": []},
                    }
                ]
            }
        }
        result = _analyze_har(har)
        assert "api.example.com" in result["auth_domains"]

    def test_empty_har(self) -> None:
        result = _analyze_har({"log": {"entries": []}})
        assert result["has_cookies"] is False
        assert result["auth_domains"] == []


# ── build_app_template_payload() (A1) ────────────────────────────────────────


class TestBuildAppTemplatePayload:
    """The App Template emitter payload derived from the generated drafts."""

    def _drafts(self) -> tuple[dict, dict]:
        result = generate(
            _session(app_name="My App", login_url="https://app.example.com/login"),
            [_click(field_role="username"), _click(field_role="password", input_type="password")],
            [_url_event("https://app.example.com/home")],
        )
        return result["application_draft"], result["service_profile_draft"]

    def test_pattern_equals_profile_slug(self) -> None:
        # CRITICAL invariant: profile_name_pattern MUST equal the runtime slug,
        # or autoProvisionFromTemplate never matches.
        app, prof = self._drafts()
        payload = build_app_template_payload(app, prof)
        assert payload["profile_name_pattern"] == prof["profile_id"]
        assert payload["profile_name_pattern"] == "my-app"

    def test_required_template_fields_present(self) -> None:
        app, prof = self._drafts()
        payload = build_app_template_payload(app, prof)
        for key in (
            "name",
            "profile_name_pattern",
            "login_config",
            "keepalive_config",
            "export_policy",
            "browser_policy",
            "notification_config",
        ):
            assert key in payload, f"template payload missing {key}"

    def test_credential_types_folded_into_export_policy(self) -> None:
        # autoProvisionFromTemplate clones credential_types from export_policy,
        # not from a profile draft — so they must be folded in.
        app, prof = self._drafts()
        prof = {
            **prof,
            "credential_types": {
                "cookies": [{"name": "sid", "volatility": "STABLE"}],
                "headers": [],
            },
            "target_domains": ["app.example.com"],
        }
        payload = build_app_template_payload(app, prof)
        assert payload["export_policy"]["credential_types"] == prof["credential_types"]
        assert payload["export_policy"]["target_domains"] == ["app.example.com"]

    def test_execute_enabled_omitted_from_template(self) -> None:
        # A4: the App Template DTO rejects execute_enabled (400). It must NOT be
        # emitted; the auto-provisioned app sets it on its own creation path.
        app, prof = self._drafts()
        payload = build_app_template_payload(app, prof)
        assert "execute_enabled" not in payload

    def test_execute_enabled_omitted_even_when_app_sets_it(self) -> None:
        app, prof = self._drafts()
        app = {**app, "execute_enabled": True}
        payload = build_app_template_payload(app, prof)
        assert "execute_enabled" not in payload

    def test_merge_unions_export_policy_additive_fields(self) -> None:
        from noui_core.compile.login_assets import merge_template_export_policy

        existing = {
            "export_policy": {
                "artifact_types": ["cookies", "headers"],
                "header_allowlist": ["Authorization"],
                "target_urls": ["https://a.com"],
                "custom_extractions": [{"key": "aura", "type": "js_eval"}],
                "credential_types": {"cookies": [{"name": "sid"}], "headers": ["X-Old"]},
            }
        }
        new_payload = {
            "name": "app",
            "profile_name_pattern": "app",
            "login_config": {"steps": [1]},
            "export_policy": {
                "artifact_types": ["headers", "csrf_token"],
                "header_allowlist": ["X-CSRF"],
                "target_urls": ["https://b.com"],
                "custom_extractions": [{"key": "csrf", "type": "cookie"}],
                "credential_types": {"cookies": [{"name": "auth"}], "headers": ["X-New"]},
            },
        }
        out = merge_template_export_policy(existing, new_payload)
        ep = out["export_policy"]
        assert set(ep["artifact_types"]) == {"cookies", "headers", "csrf_token"}
        assert set(ep["header_allowlist"]) == {"Authorization", "X-CSRF"}
        assert set(ep["target_urls"]) == {"https://a.com", "https://b.com"}
        keys = {e["key"] for e in ep["custom_extractions"]}
        assert keys == {"aura", "csrf"}
        cookie_names = {c["name"] for c in ep["credential_types"]["cookies"]}
        assert cookie_names == {"sid", "auth"}
        assert set(ep["credential_types"]["headers"]) == {"X-Old", "X-New"}
        # Non-additive fields come from the new payload.
        assert out["login_config"] == {"steps": [1]}

    def test_merge_new_wins_on_key_conflict(self) -> None:
        from noui_core.compile.login_assets import merge_template_export_policy

        existing = {"export_policy": {"custom_extractions": [{"key": "t", "v": "old"}]}}
        new_payload = {"export_policy": {"custom_extractions": [{"key": "t", "v": "new"}]}}
        out = merge_template_export_policy(existing, new_payload)
        ce = out["export_policy"]["custom_extractions"]
        assert len(ce) == 1 and ce[0]["v"] == "new"

    def test_missing_profile_id_raises(self) -> None:
        app, prof = self._drafts()
        prof = {k: v for k, v in prof.items() if k != "profile_id"}
        with pytest.raises(ValueError, match="profile_id"):
            build_app_template_payload(app, prof)

    def test_browser_policy_defaults_when_absent(self) -> None:
        app, prof = self._drafts()
        payload = build_app_template_payload(app, prof)
        # app draft has no browser_policy → safe default, not None.
        assert payload["browser_policy"] == {
            "clipboard": False,
            "downloads": False,
            "file_chooser": False,
        }
