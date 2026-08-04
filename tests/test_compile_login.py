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


def test_default_credential_mode_is_manual_never_k8s():
    """Regression: with no explicit credential mode, compile DEFAULTS to `manual:`
    and must NEVER implicitly provision a k8s:secret. The Airbnb incident came
    from compile_login.py (auth_mode=agent_token, no manual flag) silently
    emitting credential_ref=k8s:secret/tabby-airbnb."""
    from noui_core.compile.login import compile_login_bundle

    bundle = {
        "recording_mode": "login",
        "url_events": [{"to_url": "https://x.com/login"}],
        "click_events": [],
        "har": {"log": {"entries": []}},
        "cookies": [],
    }
    # No manual_credentials passed → must be manual on both drafts.
    default = compile_login_bundle(session_id="s", bundle=bundle, name="x")
    assert default["application_draft"]["login_config"]["credential_ref"] == "manual:"
    assert default["service_profile_draft"]["login_config"]["credential_ref"] == "manual:"

    # agent_token must NOT couple to k8s anymore — storage is manual unless opted in.
    agent = compile_login_bundle(session_id="s", bundle=bundle, name="x", auth_mode="agent_token")
    assert agent["application_draft"]["login_config"]["credential_ref"] == "manual:"


def test_manual_takeover_emits_confirm_step():
    from noui_core.compile.login import compile_login_bundle

    # Same-origin login+landing (root path) → no reliable auto-resolve pattern,
    # so just goto + confirm (the human clicks Mark as Resolved).
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
    assert [s["action"] for s in lc["steps"]] == ["goto", "request_human_input"]
    assert lc["steps"][1]["input_type"] == "confirm"
    assert res["validation"]["generator_valid"] is True


def test_manual_takeover_autoresolve_wait_for_url():
    from noui_core.compile.login import compile_login_bundle

    # Distinguishing post-login path (Salesforce-like) → wait_for_url auto-resolve.
    bundle = {
        "recording_mode": "login",
        "url_events": [
            {"from_url": "", "to_url": "https://test.salesforce.com/"},
            {
                "from_url": "https://test.salesforce.com/",
                "to_url": "https://x.lightning.force.com/lightning/page/home",
            },
        ],
        "click_events": [],
        "har": {"log": {"entries": []}},
        "cookies": [],
    }
    res = compile_login_bundle(
        session_id="s",
        bundle=bundle,
        name="sf",
        login_url="https://test.salesforce.com/",
        manual_takeover=True,
    )
    steps = res["application_draft"]["login_config"]["steps"]
    assert [s["action"] for s in steps] == ["goto", "request_human_input", "wait_for_url"]
    wfu = steps[2]
    assert "lightning" in wfu["pattern"]
    assert wfu["on_failure"]["action"] == "request_help"
    assert wfu["on_failure"]["input_type"] == "confirm"

    # Explicit pattern wins even for same-origin apps.
    res2 = compile_login_bundle(
        session_id="s",
        bundle={
            "recording_mode": "login",
            "url_events": [{"to_url": "https://x.com/login"}],
            "click_events": [],
            "har": {"log": {"entries": []}},
            "cookies": [],
        },
        name="x",
        manual_takeover=True,
        post_login_url_pattern="https://x.com/dashboard/**",
    )
    s2 = res2["application_draft"]["login_config"]["steps"]
    assert s2[-1]["action"] == "wait_for_url" and s2[-1]["pattern"] == "https://x.com/dashboard/**"


def test_dynamic_header_widens_scope_to_post_login_origin():
    """A client-managed bearer header (e.g. QBO's) is only ever attached on
    requests to the app the login lands on, not the login flow itself — so
    target_urls/target_domains must cover the post-login origin too, and
    Tabby needs a short refresh interval + a keepalive action to keep
    capturing it. Regression for the QBO capture that found this gap:
    the login origin alone (test.salesforce.com here) never matches requests
    to the landing app (x.lightning.force.com), so the declared header would
    never get a captured value."""
    from noui_core.compile.login import compile_login_bundle

    bundle = {
        "recording_mode": "login",
        "url_events": [
            {"from_url": "", "to_url": "https://test.salesforce.com/"},
            {
                "from_url": "https://test.salesforce.com/",
                "to_url": "https://x.lightning.force.com/lightning/page/home",
            },
        ],
        "click_events": [],
        "har": {
            "log": {
                "entries": [
                    {
                        "request": {
                            "url": "https://x.lightning.force.com/api/data",
                            "headers": [{"name": "Authorization", "value": "Bearer tok"}],
                        },
                        "response": {"headers": []},
                    }
                ]
            }
        },
        "cookies": [],
    }
    res = compile_login_bundle(
        session_id="s",
        bundle=bundle,
        name="sf",
        login_url="https://test.salesforce.com/",
        manual_takeover=True,
    )
    app = res["application_draft"]
    profile = res["service_profile_draft"]

    # "/**" is required for Tabby's request-header-capture matcher (a fully
    # anchored regex) to match real request paths, not just the bare origin.
    assert set(app["target_urls"]) == {
        "https://test.salesforce.com/**",
        "https://x.lightning.force.com/**",
    }
    assert set(profile["target_domains"]) == {"test.salesforce.com", "x.lightning.force.com"}
    assert app["export_policy"]["refresh_interval_seconds"] == 180
    keepalive_goto_urls = [
        a["url"] for a in app["keepalive_config"]["actions"] if a.get("action") == "goto"
    ]
    assert "https://x.lightning.force.com/lightning/page/home" in keepalive_goto_urls


def test_no_dynamic_headers_keeps_scope_to_login_origin_only():
    """No auth headers observed → no reason to widen scope or set a fast
    refresh interval; behavior for cookie-only logins is unchanged."""
    from noui_core.compile.login import compile_login_bundle

    bundle = {
        "recording_mode": "login",
        "url_events": [
            {"from_url": "", "to_url": "https://test.salesforce.com/"},
            {
                "from_url": "https://test.salesforce.com/",
                "to_url": "https://x.lightning.force.com/lightning/page/home",
            },
        ],
        "click_events": [],
        "har": {"log": {"entries": []}},
        "cookies": [],
    }
    res = compile_login_bundle(
        session_id="s",
        bundle=bundle,
        name="sf",
        login_url="https://test.salesforce.com/",
        manual_takeover=True,
    )
    app = res["application_draft"]
    assert "refresh_interval_seconds" not in app["export_policy"]
    assert app["keepalive_config"]["actions"] == []


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


def _har_with_request_header(name: str, value: str = "tok", url: str = "https://x.com/api/thing"):
    return {"log": {"entries": [{
        "request": {"url": url, "headers": [{"name": name, "value": value}]},
        "response": {"headers": []},
    }]}}


def test_dynamic_auth_header_emits_request_header_allowlist():
    """A declared credential header is useless without the allowlist that captures it.

    Tabby's registerRequestHeaderCapture() early-returns on an empty
    export_policy.request_header_allowlist, so the listener is never attached and
    nothing is captured — silently. credential_types.headers only controls which
    *captured* headers get surfaced. Emitting one without the other produced
    profiles that looked correct, returned 200 from /execute/fetch with no auth
    header attached, and 403'd at the target.
    """
    from noui_core.compile.login import compile_login_bundle

    bundle = {
        "recording_mode": "login",
        "url_events": [{"to_url": "https://x.com/login"}, {"to_url": "https://x.com/home"}],
        "click_events": [],
        "har": _har_with_request_header("xsrf-token"),
        "cookies": [],
    }
    res = compile_login_bundle(session_id="s", bundle=bundle, name="x", manual_takeover=True)
    ep = res["application_draft"]["export_policy"]

    assert "xsrf-token" in ep["request_header_allowlist"], (
        "capture allowlist missing — header would never be captured"
    )
    # Declared-vs-captured must agree, or the surfaced set is empty at runtime.
    declared = res["service_profile_draft"]["credential_types"]["headers"]
    assert set(declared) == set(ep["request_header_allowlist"])
    assert ep["refresh_interval_seconds"] == 180


def test_allowlist_preserves_the_on_the_wire_header_spelling():
    """Names come from the HAR verbatim; do not normalise or prefix them."""
    from noui_core.compile.login import compile_login_bundle

    bundle = {
        "recording_mode": "login",
        "url_events": [{"to_url": "https://x.com/login"}, {"to_url": "https://x.com/home"}],
        "click_events": [],
        "har": _har_with_request_header("X-Custom-Csrf"),
        "cookies": [],
    }
    res = compile_login_bundle(session_id="s", bundle=bundle, name="x", manual_takeover=True)
    assert "X-Custom-Csrf" in res["application_draft"]["export_policy"]["request_header_allowlist"]


def test_no_dynamic_headers_leaves_allowlist_unset():
    """Cookie-only apps must not get an empty allowlist — Tabby's validator
    rejects request_header_allowlist: [] as a non-empty-array violation."""
    from noui_core.compile.login import compile_login_bundle

    bundle = {
        "recording_mode": "login",
        "url_events": [{"to_url": "https://x.com/login"}],
        "click_events": [],
        "har": {"log": {"entries": []}},
        "cookies": [],
    }
    res = compile_login_bundle(session_id="s", bundle=bundle, name="x", manual_takeover=True)
    assert "request_header_allowlist" not in res["application_draft"]["export_policy"]


# ---------------------------------------------------------------------------
# Profile binding guard
# ---------------------------------------------------------------------------

def _authed_workflow_bundle():
    """A workflow whose operations carry a session cookie — i.e. needs a profile."""
    return {
        "recording_mode": "workflow",
        "url_events": [{"to_url": "https://x.com/home"}],
        "click_events": [],
        "cookies": [],
        "har": {"log": {"entries": [{
            "request": {
                "method": "GET",
                "url": "https://x.com/api/accounts",
                "headers": [{"name": "cookie", "value": "SESSION=abc"}],
            },
            "response": {"status": 200, "headers": [], "content": {"mimeType": "application/json", "text": "{}"}},
        }]}},
    }


def test_authed_workflow_without_profile_slug_is_rejected(tmp_path):
    """--profile-slug defaults to "", so an authenticated workflow silently compiled
    into a skill that could never authenticate: api_doc rendered auth as "none",
    and at run time the assistant had to ask the user which profile to use."""
    import pytest
    from noui_core.compile.workflow import compile_workflow_bundle

    with pytest.raises(ValueError, match="no Tabby profile was bound"):
        compile_workflow_bundle(
            session_id="s", bundle=_authed_workflow_bundle(), name="x",
            target="skill", profile_slug="", output_root=str(tmp_path),
            login_credential_headers=[],
        )


def test_allow_unbound_profile_escape_hatch(tmp_path):
    from noui_core.compile.workflow import compile_workflow_bundle

    res = compile_workflow_bundle(
        session_id="s", bundle=_authed_workflow_bundle(), name="x",
        target="skill", profile_slug="", output_root=str(tmp_path),
        login_credential_headers=[], allow_unbound_profile=True,
    )
    assert res.get("skill")


def test_bound_profile_compiles_normally(tmp_path):
    from noui_core.compile.workflow import compile_workflow_bundle

    res = compile_workflow_bundle(
        session_id="s", bundle=_authed_workflow_bundle(), name="x",
        target="skill", profile_slug="x-profile", output_root=str(tmp_path),
        login_credential_headers=[],
    )
    assert res.get("skill")
