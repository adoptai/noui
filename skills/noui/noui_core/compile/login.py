"""Pillar 2 — compile a *login* capture bundle into Tabby App + ServiceProfile drafts.

Orchestration over login_assets.generate. The result dict carries
``application_draft`` and ``service_profile_draft`` consumed by
noui_core.activate.register.
"""

from __future__ import annotations

from typing import Any

from noui_core.compile.login_assets import _cookie_credential_types, generate


def _domain_matches(cookie_domain: str, target_domains: set[str]) -> bool:
    """True if a cookie's domain belongs to one of the app's target domains."""
    if not target_domains:
        return True
    dom = (cookie_domain or "").lstrip(".")
    for td in target_domains:
        t = td.lstrip(".")
        if dom == t or dom.endswith("." + t) or t.endswith("." + dom):
            return True
    return False


def _enrich_credential_types_from_cookies(result: dict, bundle: dict) -> None:
    """Populate ``credential_types.cookies`` from the bundle's captured cookies.

    Tabby's worker **sanitizes ``Set-Cookie`` out of the recording HAR**, so the
    HAR-based derivation in ``generate()`` yields no cookie credential types for a
    Tabby-captured login. The bundle instead exposes the browser context cookies
    in a top-level ``cookies`` field — the Tabby-native source. When the HAR path
    found nothing, derive credential types from those (scoped to the app domains).
    """
    spd = result.get("service_profile_draft") or {}
    ct = spd.get("credential_types") or {}
    if ct.get("cookies"):
        return  # HAR already yielded cookie names — leave it.

    raw = bundle.get("cookies") or []
    if not raw:
        return

    target_domains = set(spd.get("target_domains") or [])
    names = sorted(
        {
            c["name"]
            for c in raw
            if c.get("name") and _domain_matches(c.get("domain", ""), target_domains)
        }
    )
    if names:
        ct["cookies"] = _cookie_credential_types(names)
        spd["credential_types"] = ct
        result["service_profile_draft"] = spd


def compile_login_bundle(
    *,
    session_id: str,
    bundle: dict,
    name: str = "",
    login_url: str = "",
    auth_mode: str = "agent_token",
) -> dict[str, Any]:
    """Compile a login bundle into App/ServiceProfile drafts + review items."""
    name = name or f"recording-{session_id[:8]}"

    # Resolve the login URL (explicit wins, else first http(s) URL transition).
    if not login_url:
        for u in bundle.get("url_events", []) or []:
            to = (u.get("to_url") or "").strip()
            if to.startswith("http"):
                login_url = to
                break

    session = {"id": session_id, "app_name": name, "login_url": login_url}
    result = generate(
        session,
        bundle.get("click_events", []),
        bundle.get("url_events", []),
        har=bundle.get("har"),
        auth_mode=auth_mode,
    )
    _enrich_credential_types_from_cookies(result, bundle)
    return result
