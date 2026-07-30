"""Regression tests for the adopt-ai-skills friction session (2026-07-30).

Building one skill against app.adopt.ai took 3 recordings and 5 human sign-ins
instead of 1 and 2. Two distinct defects, both silent:

1. app.adopt.ai enforces a CSP whose `connect-src` excludes the DOM recorder's
   beacon host, so the browser refused every interaction beacon and the capture
   arrived with zero `click_events`. `find_login_boundary` then found no
   credential field, `--mode combined` degraded to workflow-only, and no App
   Template was registered — reported to the operator as one calm line.
   (Fixed in the worker by posting the beacon same-origin; NoUI must still
   recognise and explain the shape, including for bundles already captured.)

2. The login profile's capture scope only ever covered the origins the human
   *navigated* (app.adopt.ai), never the origin the bearer is actually sent to
   (api.adopt.ai). Tabby's request-header sniffer is filtered by that scope, so
   the declared `authorization` header never got a captured value and every
   compiled operation 401'd against a HEALTHY session.

The bundle below is shaped exactly as Tabby drains one from that site.
"""

from __future__ import annotations

from noui_core.capture.classify import (
    classify_bundle,
    diagnose_missing_login,
    missing_login_advice,
)
from noui_core.capture.split import split_bundle
from noui_core.compile.login_assets import _analyze_har, generate

BEARER = "Bearer eyJhbGciOiJSUzI1NiJ9.PAYLOAD.SIG"


def _hdrs(*pairs: tuple[str, str]) -> list[dict]:
    return [{"name": n, "value": v} for n, v in pairs]


def _entry(ts: str, method: str, url: str, headers: list[dict]) -> dict:
    return {
        "startedDateTime": ts,
        "request": {"method": method, "url": url, "headers": headers, "queryString": []},
        "response": {"status": 200, "content": {"mimeType": "application/json"}, "headers": []},
        "_resourceType": "fetch",
    }


AUTHED = _hdrs(("accept", "*/*"), ("authorization", BEARER))


def _adopt_bundle(*, click_events: list[dict] | None = None) -> dict:
    """A combined (login + workflow) capture of app.adopt.ai.

    click_events defaults to [] — what the CSP-silenced recorder produced.
    """
    return {
        "session_id": "adopt-skills",
        "recording_mode": "login",  # warm-pool stamp; NoUI ignores it
        "click_events": click_events if click_events is not None else [],
        "url_events": [
            {
                "from_url": "about:blank",
                "to_url": "https://app.adopt.ai/",
                "timestamp": "2026-07-30T10:00:05.000Z",
            },
            {
                "from_url": "https://app.adopt.ai/",
                "to_url": "https://app.adopt.ai/prelogin",
                "timestamp": "2026-07-30T10:00:07.000Z",
            },
            {
                "from_url": "https://app.adopt.ai/prelogin",
                "to_url": "https://app.adopt.ai/account/login",
                "timestamp": "2026-07-30T10:00:09.000Z",
            },
            {
                "from_url": "https://app.adopt.ai/account/login",
                "to_url": "https://app.adopt.ai/oauth/callback?code=abc",
                "timestamp": "2026-07-30T10:01:22.000Z",
            },
            {
                "from_url": "https://app.adopt.ai/oauth/callback?code=abc",
                "to_url": "https://app.adopt.ai/",
                "timestamp": "2026-07-30T10:01:24.000Z",
            },
        ],
        "har": {
            "log": {
                "entries": [
                    _entry(
                        "2026-07-30T10:00:10.000Z",
                        "POST",
                        "https://app.adopt.ai/frontegg/identity/resources/auth/v2/user/token/refresh",
                        _hdrs(("accept", "*/*")),
                    ),
                    # The bearer only ever rides on the API origin, never the UI origin.
                    _entry(
                        "2026-07-30T10:02:31.000Z",
                        "GET",
                        "https://api.adopt.ai/v1/end-user/agent-harness/skills",
                        AUTHED,
                    ),
                ]
            }
        },
        "cookies": [],
    }


# ── 1. A silenced recorder must not read as "no login happened" ──────────────


class TestSilencedRecorderDiagnosis:
    def test_capture_has_no_login_boundary(self) -> None:
        """The precondition: this is what the operator actually hit."""
        bundle = _adopt_bundle()
        assert split_bundle(bundle) is None
        assert classify_bundle(bundle) == "workflow"

    def test_zero_interactions_with_real_traffic_is_flagged(self) -> None:
        diagnosis = diagnose_missing_login(_adopt_bundle())
        assert diagnosis is not None
        assert diagnosis["reason"] == "recorder_silent"
        assert diagnosis["recorder_silent"] is True
        assert "Content-Security-Policy" in diagnosis["detail"]

    def test_flags_the_signin_urls_it_saw(self) -> None:
        diagnosis = diagnose_missing_login(_adopt_bundle())
        assert diagnosis is not None
        assert any("/account/login" in u for u in diagnosis["login_urls"])

    def test_advice_names_the_mode_login_recovery(self) -> None:
        """The workaround took source-reading to find; it must be printed."""
        advice = "\n".join(missing_login_advice(_adopt_bundle(), "sess-123", "adopt-ai-skills"))
        assert "--mode login" in advice
        assert "sess-123" in advice
        assert "no App Template" in advice.lower() or "NO App Template" in advice
        assert "re-record" in advice.lower()

    def test_login_without_credential_field_is_a_distinct_reason(self) -> None:
        """SSO / passwordless / warm-pool: interactions exist, no credential field."""
        clicks = [
            {
                "event_type": "click",
                "tag_name": "BUTTON",
                "field_role": None,
                "selector": "#sso",
                "timestamp": "2026-07-30T10:00:08.000Z",
            }
        ]
        diagnosis = diagnose_missing_login(_adopt_bundle(click_events=clicks))
        assert diagnosis is not None
        assert diagnosis["reason"] == "login_without_credential_field"
        assert diagnosis["recorder_silent"] is False

    def test_genuine_workflow_capture_is_not_flagged(self) -> None:
        """No false alarm on a workflow recorded against an existing profile."""
        bundle = {
            "session_id": "wf",
            "click_events": [
                {
                    "event_type": "click",
                    "tag_name": "BUTTON",
                    "field_role": None,
                    "selector": "#export",
                    "timestamp": "2026-07-30T10:00:08.000Z",
                }
            ],
            "url_events": [
                {
                    "from_url": "",
                    "to_url": "https://app.example.com/reports",
                    "timestamp": "2026-07-30T10:00:05.000Z",
                }
            ],
            "har": {
                "log": {
                    "entries": [
                        _entry(
                            "2026-07-30T10:00:06.000Z",
                            "GET",
                            "https://app.example.com/api/reports",
                            _hdrs(("accept", "*/*")),
                        ),
                    ]
                }
            },
        }
        assert diagnose_missing_login(bundle) is None
        assert missing_login_advice(bundle, "sess-1") == []

    def test_no_diagnosis_when_a_login_was_detected(self) -> None:
        clicks = [
            {
                "event_type": "input",
                "tag_name": "INPUT",
                "field_role": "username",
                "field_name": "email",
                "selector": "#email",
                "value": "a@b.c",
                "timestamp": "2026-07-30T10:00:08.000Z",
            }
        ]
        assert diagnose_missing_login(_adopt_bundle(click_events=clicks)) is None


# ── 2. Capture scope must follow the token, not the human ────────────────────


class TestAuthHeaderOriginsInScope:
    def test_analyze_har_records_the_origin_carrying_the_bearer(self) -> None:
        analysis = _analyze_har(_adopt_bundle()["har"])
        assert analysis["auth_header_origins"] == ["https://api.adopt.ai"]

    def test_analyze_har_ignores_origins_with_no_auth_header(self) -> None:
        analysis = _analyze_har(_adopt_bundle()["har"])
        assert "https://app.adopt.ai" not in analysis["auth_header_origins"]

    def _draft(self) -> dict:
        bundle = _adopt_bundle()
        return generate(
            {
                "id": "adopt-skills",
                "app_name": "Adopt AI Skills",
                "login_url": "https://app.adopt.ai/account/login",
            },
            [],
            bundle["url_events"],
            har=bundle["har"],
        )

    def test_request_header_allowlist_turns_capture_on(self) -> None:
        """Tabby's registerRequestHeaderCapture() returns immediately on an empty
        allowlist, so without this nothing is captured however right the scope is."""
        export_policy = self._draft()["application_draft"]["export_policy"]
        assert export_policy["request_header_allowlist"] == ["authorization"]

    def test_allowlist_never_contains_cookie(self) -> None:
        """Tabby's validator rejects Cookie — cookies have their own path."""
        allowlist = self._draft()["application_draft"]["export_policy"]["request_header_allowlist"]
        assert all(h.lower() != "cookie" for h in allowlist)

    def test_allowlist_agrees_with_declared_credential_types(self) -> None:
        """The two halves of the contract: what the worker CAPTURES must cover
        what /credentials/request SERVES, or the value is always empty."""
        draft = self._draft()
        served = draft["service_profile_draft"]["credential_types"]["headers"]
        captured = draft["application_draft"]["export_policy"]["request_header_allowlist"]
        assert {h.lower() for h in served} <= {h.lower() for h in captured}

    def test_api_origin_lands_in_target_urls(self) -> None:
        """Without this the request-header sniffer never fires and auth stays empty."""
        target_urls = self._draft()["application_draft"]["target_urls"]
        assert "https://api.adopt.ai/**" in target_urls

    def test_ui_origin_is_still_covered(self) -> None:
        target_urls = self._draft()["application_draft"]["target_urls"]
        assert "https://app.adopt.ai/**" in target_urls

    def test_export_policy_scope_matches(self) -> None:
        """export_policy.target_urls is the list Tabby actually filters capture on."""
        export_policy = self._draft()["application_draft"]["export_policy"]
        assert "https://api.adopt.ai/**" in export_policy["target_urls"]

    def test_target_urls_keep_the_glob_suffix(self) -> None:
        """Tabby anchors each glob: a bare origin matches nothing with a path."""
        for url in self._draft()["application_draft"]["target_urls"]:
            assert url.endswith("/**"), url

    def test_api_domain_reaches_target_domains(self) -> None:
        """target_domains gates execute/fetch's attach_captured_credentials."""
        domains = self._draft()["service_profile_draft"].get("target_domains", [])
        assert "api.adopt.ai" in domains
