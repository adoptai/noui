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
    # A miss must NOT re-prompt the human: the preceding request_human_input is
    # already a human attestation that they logged in, and the pattern is derived
    # from a single observed landing route that portals are free to vary per user
    # or product. Asking twice is what ICICI sessions actually did.
    assert wfu["on_failure"] == {"action": "skip"}

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
    # Default (HAR-replay) keepalive revisits the landing page to keep captured
    # request headers fresh.
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
    return {
        "log": {
            "entries": [
                {
                    "request": {"url": url, "headers": [{"name": name, "value": value}]},
                    "response": {"headers": []},
                }
            ]
        }
    }


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
        "har": {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "GET",
                            "url": "https://x.com/api/accounts",
                            "headers": [{"name": "cookie", "value": "SESSION=abc"}],
                        },
                        "response": {
                            "status": 200,
                            "headers": [],
                            "content": {"mimeType": "application/json", "text": "{}"},
                        },
                    }
                ]
            }
        },
    }


def test_authed_workflow_without_profile_slug_is_rejected(tmp_path):
    """--profile-slug defaults to "", so an authenticated workflow silently compiled
    into a skill that could never authenticate: api_doc rendered auth as "none",
    and at run time the assistant had to ask the user which profile to use."""
    import pytest
    from noui_core.compile.workflow import compile_workflow_bundle

    with pytest.raises(ValueError, match="no Tabby profile was bound"):
        compile_workflow_bundle(
            session_id="s",
            bundle=_authed_workflow_bundle(),
            name="x",
            target="skill",
            profile_slug="",
            output_root=str(tmp_path),
            login_credential_headers=[],
        )


def test_allow_unbound_profile_escape_hatch(tmp_path):
    from noui_core.compile.workflow import compile_workflow_bundle

    res = compile_workflow_bundle(
        session_id="s",
        bundle=_authed_workflow_bundle(),
        name="x",
        target="skill",
        profile_slug="",
        output_root=str(tmp_path),
        login_credential_headers=[],
        allow_unbound_profile=True,
    )
    assert res.get("skill")


def test_bound_profile_compiles_normally(tmp_path):
    from noui_core.compile.workflow import compile_workflow_bundle

    res = compile_workflow_bundle(
        session_id="s",
        bundle=_authed_workflow_bundle(),
        name="x",
        target="skill",
        profile_slug="x-profile",
        output_root=str(tmp_path),
        login_credential_headers=[],
    )
    assert res.get("skill")


# ---------------------------------------------------------------------------
# Landing-page inference + health check
# ---------------------------------------------------------------------------


def _login_bundle(url_events):
    return {
        "recording_mode": "login",
        "url_events": url_events,
        "click_events": [],
        "har": {"log": {"entries": []}},
        "cookies": [],
    }


def test_landing_page_stops_at_the_first_origin_change():
    """A multi-host recording must not wait on the LAST host visited.

    ICICI retail login produced wait_for_url "https://infinity.icici.bank.in/corp**"
    — the CORPORATE portal — because stable_urls[-1] was where the human finished
    the workflow. A retail login can never reach it, so wait_for_url timed out on
    every session and the human was asked to "finish logging in" after they had.
    """
    from noui_core.compile.login import compile_login_bundle

    bundle = _login_bundle(
        [
            {"to_url": "https://retail.bank.in/login-page"},
            {"to_url": "https://retail.bank.in/dashboard"},  # ← the landing page
            {"to_url": "https://corp.bank.in/corp/Finacle"},  # ← later workflow host
        ]
    )
    res = compile_login_bundle(session_id="s", bundle=bundle, name="x", manual_takeover=True)
    steps = res["application_draft"]["login_config"]["steps"]
    wait = next(s for s in steps if s["action"] == "wait_for_url")
    assert "retail.bank.in" in wait["pattern"]
    assert "corp.bank.in" not in wait["pattern"]


def test_landing_page_follows_same_origin_settling_bounce():
    """Logins commonly settle through one more same-origin hop, so do not just
    take stable_urls[0] (classify.py documents Expedia's /onboarding -> /?ref)."""
    from noui_core.compile.login import compile_login_bundle

    bundle = _login_bundle(
        [
            {"to_url": "https://x.com/login"},
            {"to_url": "https://x.com/onboarding?originUrl=a"},
            {"to_url": "https://x.com/?challengeReferer=noref"},
        ]
    )
    res = compile_login_bundle(session_id="s", bundle=bundle, name="x", manual_takeover=True)
    hc = res["application_draft"]["keepalive_config"]["health_checks"][0]
    # Kept verbatim: this query carries no session-bound material, and stripping
    # it would break apps whose query IS the routing (see _has_volatile_query).
    assert hc["url"] == "https://x.com/?challengeReferer=noref"


def test_health_check_can_actually_fail():
    """dom_check on body passes on the login page too, so a signed-out session
    reported HEALTHY forever and consumers only found out via 401/403."""
    from noui_core.compile.login import compile_login_bundle

    bundle = _login_bundle(
        [
            {"to_url": "https://x.com/login-page"},
            {"to_url": "https://x.com/dashboard"},
        ]
    )
    res = compile_login_bundle(session_id="s", bundle=bundle, name="x", manual_takeover=True)
    checks = res["application_draft"]["keepalive_config"]["health_checks"]
    assert [c["type"] for c in checks] == ["url_check"]
    hc = checks[0]
    assert hc["url"] == "https://x.com/dashboard"
    assert hc["expect_status"] == 200
    # Must catch a bounce back to this app's own login path, which the built-in
    # heuristic (login|signin|sso|…) would miss for a non-standard path.
    import re as _re

    assert _re.search(hc["auth_redirect_pattern"], "https://x.com/login-page", _re.I)
    assert not _re.search(hc["auth_redirect_pattern"], "https://x.com/dashboard", _re.I)


def test_unverifiable_health_check_is_flagged_when_no_landing_page():
    from noui_core.compile.login import compile_login_bundle

    # Only the login page itself → no post-login URL can be inferred.
    bundle = _login_bundle([{"to_url": "https://x.com/login"}])
    res = compile_login_bundle(session_id="s", bundle=bundle, name="x", manual_takeover=True)
    checks = res["application_draft"]["keepalive_config"]["health_checks"]
    assert checks[0]["type"] == "dom_check"
    kinds = {i["type"] for i in res["review_items"]}
    assert "unverifiable_health_check" in kinds


# ---------------------------------------------------------------------------
# Profile-slug existence check
# ---------------------------------------------------------------------------


def test_nonexistent_profile_slug_is_rejected(tmp_path, monkeypatch):
    """A bound-but-wrong slug is harder to spot than an unbound one: the skill
    routes through call_web_api and looks right, but Tabby resolves the name to
    nothing so every call reports login_required and the user is told to sign in
    to a profile that does not exist."""
    import pytest
    from noui_core.compile import workflow as wf

    monkeypatch.setattr(wf, "_profile_slug_resolves", lambda slug: False)
    monkeypatch.setattr(wf, "_known_profile_slugs", lambda: ["icici-retail-netbanking"])

    with pytest.raises(ValueError, match="does not exist") as exc:
        wf.compile_workflow_bundle(
            session_id="s",
            bundle=_authed_workflow_bundle(),
            name="x",
            target="skill",
            profile_slug="icici-credit-card",
            output_root=str(tmp_path),
            login_credential_headers=[],
        )
    # The remedy must name what IS available, or the user is left guessing.
    assert "icici-retail-netbanking" in str(exc.value)


def test_unknown_profile_lookup_does_not_block_compile(tmp_path, monkeypatch):
    """None means 'could not check' (offline, no admin token) — compiling must
    still work rather than failing on an unverifiable binding."""
    from noui_core.compile import workflow as wf

    monkeypatch.setattr(wf, "_profile_slug_resolves", lambda slug: None)
    res = wf.compile_workflow_bundle(
        session_id="s",
        bundle=_authed_workflow_bundle(),
        name="x",
        target="skill",
        profile_slug="whatever",
        output_root=str(tmp_path),
        login_credential_headers=[],
    )
    assert res.get("skill")


def test_existing_profile_slug_compiles(tmp_path, monkeypatch):
    from noui_core.compile import workflow as wf

    monkeypatch.setattr(wf, "_profile_slug_resolves", lambda slug: True)
    res = wf.compile_workflow_bundle(
        session_id="s",
        bundle=_authed_workflow_bundle(),
        name="x",
        target="skill",
        profile_slug="real-profile",
        output_root=str(tmp_path),
        login_credential_headers=[],
    )
    assert res.get("skill")


def test_allow_unbound_profile_also_skips_the_existence_check(tmp_path, monkeypatch):
    from noui_core.compile import workflow as wf

    monkeypatch.setattr(wf, "_profile_slug_resolves", lambda slug: False)
    res = wf.compile_workflow_bundle(
        session_id="s",
        bundle=_authed_workflow_bundle(),
        name="x",
        target="skill",
        profile_slug="ghost",
        output_root=str(tmp_path),
        login_credential_headers=[],
        allow_unbound_profile=True,
    )
    assert res.get("skill")


def test_template_without_profile_counts_as_resolvable(monkeypatch):
    """Tabby auto-provisions a profile from a matching App Template on first use,
    so binding to a template that has no profile yet is legitimate."""
    from noui_core import tabby_client
    from noui_core.activate import register
    from noui_core.compile import workflow as wf

    seen = {}
    monkeypatch.setattr(register, "resolve_admin_token", lambda: "tok")
    monkeypatch.setattr(
        tabby_client,
        "get_service_profile_by_slug",
        lambda slug, token: seen.setdefault("profile", slug) and None,
    )
    monkeypatch.setattr(
        tabby_client,
        "get_app_template_by_profile_slug",
        lambda slug, token: (seen.setdefault("template", slug), {"id": "tpl-1"})[1],
    )

    assert wf._profile_slug_resolves("only-a-template") is True
    assert seen == {"profile": "only-a-template", "template": "only-a-template"}


def test_resolves_false_only_when_both_lookups_come_back_empty(monkeypatch):
    from noui_core import tabby_client
    from noui_core.activate import register
    from noui_core.compile import workflow as wf

    monkeypatch.setattr(register, "resolve_admin_token", lambda: "tok")
    monkeypatch.setattr(tabby_client, "get_service_profile_by_slug", lambda s, t: None)
    monkeypatch.setattr(tabby_client, "get_app_template_by_profile_slug", lambda s, t: None)
    assert wf._profile_slug_resolves("ghost") is False


def test_lookup_failure_is_unknown_not_absent(monkeypatch):
    """Tabby unreachable must not be mistaken for 'profile does not exist' —
    that would fail every offline compile."""
    from noui_core import tabby_client
    from noui_core.activate import register
    from noui_core.compile import workflow as wf

    def _boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(register, "resolve_admin_token", lambda: "tok")
    monkeypatch.setattr(tabby_client, "get_service_profile_by_slug", _boom)
    assert wf._profile_slug_resolves("anything") is None


# ---------------------------------------------------------------------------
# Manifest / operations routing agreement
# ---------------------------------------------------------------------------


def _read_skill_artifacts(out_root, skill_id="x"):
    """Return (manifest, operations) for a compiled skill under out_root."""
    import json
    from pathlib import Path

    md = next(Path(out_root).rglob("manifest.json"))
    ops = next(Path(out_root).rglob("operations.json"))
    return json.loads(md.read_text()), json.loads(ops.read_text())


def test_manifest_and_operations_agree_on_routing(tmp_path, monkeypatch):
    """A manifest declaring browser routing while every operation emits curl is the
    shape that hid this bug: the manifest reads correctly so nothing questions it,
    and the skill fails as 403s with no sign-in card (call_web_api is never invoked,
    so the login_required path that renders the card never runs).

    Observed live on icici-credit-card: 8/8 operations tool=bash, profile_slug None,
    strategy tabby_credentials, execution_strategy harness_call_web_api.
    """
    import pytest
    from noui_core.compile import workflow as wf

    monkeypatch.setattr(wf, "_profile_slug_resolves", lambda slug: True)

    with pytest.raises(ValueError, match="tool=bash"):
        wf.compile_workflow_bundle(
            session_id="s",
            bundle=_authed_workflow_bundle(),
            name="x",
            target="skill",
            profile_slug="",
            execution_mode="harness",
            output_root=str(tmp_path),
            login_credential_headers=[],
            allow_unbound_profile=True,  # even the opt-out must not permit this
        )


def test_bound_harness_skill_routes_every_operation_through_call_web_api(tmp_path, monkeypatch):
    from noui_core.compile import workflow as wf

    monkeypatch.setattr(wf, "_profile_slug_resolves", lambda slug: True)
    wf.compile_workflow_bundle(
        session_id="s",
        bundle=_authed_workflow_bundle(),
        name="x",
        target="skill",
        profile_slug="real-profile",
        execution_mode="harness",
        output_root=str(tmp_path),
        login_credential_headers=[],
    )
    manifest, ops = _read_skill_artifacts(tmp_path)

    assert manifest["auth"]["profile_slug"] == "real-profile"
    assert manifest["auth"]["execution_strategy"] == "harness_call_web_api"
    tools = {o["tool"] for o in ops["operations"]}
    assert tools == {"call_web_api"}, f"manifest says browser routing but ops use {tools}"


def test_unreplayable_landing_url_emits_no_keepalive_goto():
    """ICICI's recorded landing URL carried a one-time UX_TOKEN, and replaying it
    every 300s threw the live browser onto an expired-token error page — silently
    discarding whatever the user had signed into.

    Stripping the query does not rescue it either: in Finacle/JSP-style apps the
    query IS the routing, so the bare path returns "Page temporarily unavailable"
    (verified against the live portal). With no replayable form, emit nothing and
    say so rather than emit something broken.
    """
    from noui_core.compile.login import compile_login_bundle

    landing = (
        "https://infinity.icici.bank.in/corp/AuthenticationController"
        "?FORMSGROUP_ID__=AuthenticationFG&UX_TOKEN=RE%2B5MjthAGhn&CTA_FLAG=CCPSTM"
    )
    bundle = {
        "recording_mode": "login",
        "url_events": [
            {"to_url": "https://infinity.icici.bank.in/corp/Login"},
            {"to_url": landing},
        ],
        "click_events": [],
        "har": {
            "log": {
                "entries": [
                    {
                        "request": {
                            "url": landing,
                            "headers": [{"name": "xsrf-token", "value": "t"}],
                        },
                        "response": {"headers": []},
                    }
                ]
            }
        },
        "cookies": [],
    }
    res = compile_login_bundle(session_id="s", bundle=bundle, name="icici", manual_takeover=True)

    gotos = [
        a
        for a in res["application_draft"]["keepalive_config"]["actions"]
        if a.get("action") == "goto"
    ]
    assert gotos == [], "a URL with one-time query material must not be replayed"
    assert "keepalive_goto_skipped" in {i["type"] for i in res["review_items"]}


def test_stable_landing_url_still_gets_a_keepalive_goto():
    """The skip is targeted: a landing URL with no session-bound material keeps its
    goto (default HAR-replay style), which guarantees traffic for header capture."""
    from noui_core.compile.login import compile_login_bundle

    landing = "https://app.example.com/home?tab=overview"
    bundle = {
        "recording_mode": "login",
        "url_events": [
            {"to_url": "https://app.example.com/login"},
            {"to_url": landing},
        ],
        "click_events": [],
        "har": {
            "log": {
                "entries": [
                    {
                        "request": {
                            "url": landing,
                            "headers": [{"name": "authorization", "value": "Bearer x"}],
                        },
                        "response": {"headers": []},
                    }
                ]
            }
        },
        "cookies": [],
    }
    res = compile_login_bundle(session_id="s", bundle=bundle, name="app", manual_takeover=True)
    gotos = [
        a
        for a in res["application_draft"]["keepalive_config"]["actions"]
        if a.get("action") == "goto"
    ]
    assert gotos and gotos[0]["url"] == landing


def test_activity_keepalive_style_holds_session_without_reload():
    """keepalive_style='activity' (browser-driven default) emits the human-like
    nudge at a tight interval and NO goto — proven to hold ICICI 11+ min idle."""
    from noui_core.compile.login import compile_login_bundle

    landing = "https://app.example.com/home?tab=overview"
    bundle = {
        "recording_mode": "login",
        "url_events": [
            {"to_url": "https://app.example.com/login"},
            {"to_url": landing},
        ],
        "click_events": [],
        # dynamic header present — under 'goto' this would emit a reload; under
        # 'activity' it must NOT (browser skills read the DOM, need no header nav).
        "har": {
            "log": {
                "entries": [
                    {
                        "request": {
                            "url": landing,
                            "headers": [{"name": "authorization", "value": "Bearer x"}],
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
        name="app",
        manual_takeover=True,
        keepalive_style="activity",
    )
    ka = res["application_draft"]["keepalive_config"]
    assert [a["action"] for a in ka["actions"]] == ["activity"]
    assert not any(a.get("action") == "goto" for a in ka["actions"])
    assert ka["interval_seconds"] <= 60


def test_keepalive_interval_default_goto():
    """Default (goto) keepalive interval stays within Tabby's 120-300s guidance."""
    from noui_core.compile.login import compile_login_bundle

    res = compile_login_bundle(
        session_id="s",
        bundle={
            "recording_mode": "login",
            "url_events": [
                {"from_url": "", "to_url": "https://bank.test/login-page"},
                {
                    "from_url": "https://bank.test/login-page",
                    "to_url": "https://bank.test/dashboard",
                },
            ],
            "click_events": [],
            "har": {"log": {"entries": []}},
            "cookies": [],
        },
        name="bank",
        login_url="https://bank.test/login-page",
        manual_takeover=True,
    )
    ka = res["application_draft"]["keepalive_config"]
    assert ka["interval_seconds"] <= 120, ka["interval_seconds"]
