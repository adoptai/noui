"""Tests for noui_core.compile.login — credential_types enrichment from bundle cookies.

Tabby sanitizes Set-Cookie out of the recording HAR, so credential_types must be
derived from the bundle's top-level `cookies` field.
"""

from __future__ import annotations

from noui_core.compile.login import (
    _domain_matches,
    _enrich_credential_types_from_cookies,
)


def test_domain_matches():
    assert _domain_matches(".expedia.com", {"www.expedia.com"})
    assert _domain_matches("www.expedia.com", {"www.expedia.com"})
    assert _domain_matches("expedia.com", {"www.expedia.com"})
    assert not _domain_matches("evil.com", {"www.expedia.com"})
    assert _domain_matches("anything.com", set())  # no targets → match all


def test_enrich_from_bundle_cookies_when_har_empty():
    result = {
        "service_profile_draft": {
            "profile_id": "expedia-login",
            "credential_types": {"cookies": [], "headers": []},
            "target_domains": ["www.expedia.com"],
        }
    }
    bundle = {
        "cookies": [
            {"name": "tpid", "domain": ".expedia.com"},
            {"name": "ak_bmsc", "domain": ".expedia.com"},
            {"name": "tracker", "domain": ".doubleclick.net"},  # off-domain → excluded
            {"name": "", "domain": ".expedia.com"},  # nameless → skipped
        ]
    }
    _enrich_credential_types_from_cookies(result, bundle)
    cookies = result["service_profile_draft"]["credential_types"]["cookies"]
    names = {c["name"] for c in cookies}
    assert names == {"tpid", "ak_bmsc"}  # off-domain + nameless dropped
    # Akamai token marked volatile, normal cookie stable.
    vol = {c["name"]: c["volatility"] for c in cookies}
    assert vol["ak_bmsc"] == "VOLATILE"
    assert vol["tpid"] == "STABLE"


def test_enrich_does_not_override_har_derived():
    result = {
        "service_profile_draft": {
            "credential_types": {"cookies": [{"name": "from_har", "volatility": "STABLE"}]},
            "target_domains": ["x.com"],
        }
    }
    _enrich_credential_types_from_cookies(result, {"cookies": [{"name": "ctx", "domain": "x.com"}]})
    names = {c["name"] for c in result["service_profile_draft"]["credential_types"]["cookies"]}
    assert names == {"from_har"}  # untouched


def test_credential_mode_manual_vs_stored():
    from noui_core.compile.login import compile_login_bundle

    bundle = {
        "recording_mode": "login",
        "url_events": [{"to_url": "https://x.com/login"}],
        "click_events": [],
        "har": {"log": {"entries": []}},
        "cookies": [],
    }
    man = compile_login_bundle(session_id="s", bundle=bundle, name="x", manual_credentials=True)
    assert man["application_draft"]["login_config"]["credential_ref"] == "manual:"

    stored = compile_login_bundle(session_id="s", bundle=bundle, name="x", manual_credentials=False)
    assert stored["application_draft"]["login_config"]["credential_ref"].startswith("k8s:secret/")


def test_manual_takeover_emits_confirm_step():
    from noui_core.compile.login import compile_login_bundle

    bundle = {
        "recording_mode": "login",
        "url_events": [{"to_url": "https://x.com/login"}],
        "click_events": [],
        "har": {"log": {"entries": []}},
        "cookies": [],
    }
    res = compile_login_bundle(session_id="s", bundle=bundle, name="x", manual_takeover=True)
    lc = res["application_draft"]["login_config"]
    assert lc["credential_ref"] == "manual:"
    actions = [s["action"] for s in lc["steps"]]
    assert actions == ["goto", "request_human_input"]
    confirm = lc["steps"][1]
    assert confirm["input_type"] == "confirm"
    # Takeover has no username/password fields — must still validate.
    assert res["validation"]["generator_valid"] is True


def test_resolve_panel_url():
    from noui_core.capture.autopilot import resolve_panel_url

    # Inserts ?from=mcp before the fragment.
    assert (
        resolve_panel_url("https://t/vnc/abc#token=xyz") == "https://t/vnc/abc?from=mcp#token=xyz"
    )
    # Uses & when a query already exists; idempotent; no-op on empty.
    assert resolve_panel_url("https://t/vnc/abc?x=1#f") == "https://t/vnc/abc?x=1&from=mcp#f"
    assert resolve_panel_url("https://t/vnc/abc?from=mcp#f") == "https://t/vnc/abc?from=mcp#f"
    assert resolve_panel_url("") == ""


def test_enrich_noop_without_bundle_cookies():
    result = {"service_profile_draft": {"credential_types": {"cookies": []}}}
    _enrich_credential_types_from_cookies(result, {})  # no cookies field
    assert result["service_profile_draft"]["credential_types"]["cookies"] == []
