"""Pillar 2 — compile a *workflow* capture bundle into MCP and/or Skill assets.

Orchestration over the deterministic generators. Compiles directly from a
recording bundle ({har, click_events, url_events}) — no NoUI backend round-trip.
"""

from __future__ import annotations

import re
from pathlib import Path

from noui_core.compile.server_generator import compile_workflow
from noui_core.compile.skill_generator import compile_workflow_to_skill
from noui_core.config import settings


def app_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "app"


def _fetch_login_credential_headers(profile_slug: str) -> list[str] | None:
    """Best-effort lookup of a paired login profile's declared header names.

    Returns the `credential_types.headers` Tabby already tracks for
    ``profile_slug`` (dynamically captured from real page traffic per
    `login_assets.py::generate()`), or None if unreachable/not found/anything
    goes wrong — auth-plan generation falls back to its existing HAR-only
    heuristic in that case, so this is never load-bearing for correctness,
    only for the cross-capture case this closes (see
    `auth_plan.py::generate_auth_plan`'s `login_credential_headers` doc).
    """
    if not profile_slug:
        return None
    try:
        from noui_core import tabby_client
        from noui_core.activate.register import resolve_admin_token

        # /admin/profiles is Editor+-gated (see register.py's resolve_admin_token
        # docstring) — the agent token (client_id/secret) cannot see it.
        token = resolve_admin_token()
        profile = tabby_client.get_service_profile_by_slug(profile_slug, token)
        if not profile:
            return None
        headers = (profile.get("credential_types") or {}).get("headers")
        return list(headers) if headers else None
    except Exception:
        return None


def _profile_slug_resolves(profile_slug: str) -> bool | None:
    """Does ``profile_slug`` name something Tabby can actually resolve?

    Three-valued on purpose:
      True  — a ServiceProfile or an App Template matches the slug.
      False — the lookup ran and found neither, so a skill bound to it would
              resolve to nothing at run time.
      None  — we could not check (no admin token, Tabby unreachable, anything
              unexpected). Callers must treat this as "unknown", never as absent:
              compiling offline is legitimate and must not start failing.

    An App Template counts as existing even with no ServiceProfile yet — Tabby
    auto-provisions the profile from a matching ``profile_name_pattern`` on first
    credential request, so binding to it is valid ahead of time.
    """
    if not profile_slug:
        return None
    try:
        from noui_core import tabby_client
        from noui_core.activate.register import resolve_admin_token

        # /admin/* is Editor+-gated; the agent token cannot see it.
        token = resolve_admin_token()
    except Exception:
        return None
    try:
        if tabby_client.get_service_profile_by_slug(profile_slug, token):
            return True
        return bool(tabby_client.get_app_template_by_profile_slug(profile_slug, token))
    except Exception:
        return None


def _known_profile_slugs() -> list[str]:
    """Slugs to suggest when a binding does not resolve. Best-effort, never raises."""
    try:
        from noui_core import tabby_client
        from noui_core.activate.register import resolve_admin_token

        token = resolve_admin_token()
        return sorted(
            {
                str(t.get("profile_name_pattern") or "").strip()
                for t in (tabby_client.list_app_templates(token) or [])
                if str(t.get("profile_name_pattern") or "").strip()
            }
        )
    except Exception:
        return []


def compile_workflow_bundle(
    *,
    session_id: str,
    bundle: dict,
    name: str = "",
    target: str = "mcp",
    profile_slug: str = "",
    execution_mode: str = "tabby",
    output_root: str | None = None,
    start_url: str = "",
    login_credential_headers: list[str] | None = None,
    auth_type: str = "auto",
    api_key_header: str = "",
    allow_unbound_profile: bool = False,
    browser_driven: bool = False,
    auto_detect_browser: bool = True,
) -> dict:
    """Compile a workflow bundle to ``target`` ("mcp" | "skill" | "both").

    browser_driven: emit a browser-driven skill (drives the page via the
        call_web_browser harness tool and reads the rendered DOM) instead of a
        HAR-replay call_web_api skill. Use for apps whose requests cannot be
        replayed — SPAs that mint per-request encryption or per-session headers
        in JavaScript (ICICI's {data,key} bodies). Only affects the "skill"
        target; requires a bound profile_slug. The MCP target is unaffected.
    auto_detect_browser: when browser_driven is not already set, inspect the HAR
        for the unreplayable fingerprint (opaque {data,key} bodies + a key-fetch
        endpoint) and switch to browser mode automatically if it fires. The
        decision and its reasons are returned under result["browser_detection"].
        Set False to force the legacy replay compile regardless of the signal.

    Args:
        login_credential_headers: Header names already declared on the paired
            login profile (see `auth_plan.py::generate_auth_plan`). Auto-fetched
            best-effort from Tabby via `profile_slug` when not given explicitly
            (pass an explicit `[]` to opt out of the lookup entirely, e.g. in tests).
        auth_type: Explicit auth-model declaration from the operator, replacing the
            HAR heuristic:
              - "session"  → force `tabby_credentials` (a login/session was recorded;
                             the false "looks static" positive cannot happen).
              - "api-key"  → force `static_secret_header`; no login profile is
                             involved, so the login-header lookup and scope
                             extension are skipped, and `api_key_header` names the
                             header carried as a ${SECRET:name} placeholder.
              - "auto"     → legacy HAR heuristic (`_is_static_api_key_app`) plus the
                             `login_credential_headers` fallback. Default here so
                             existing programmatic callers are unchanged; the CLIs
                             default to "session".
        api_key_header: Auth header name for "api-key" mode (default "Authorization").

    Returns {"mcp": manifest?, "skill": manifest?} for whichever were built.
    """
    if target not in ("mcp", "skill", "both"):
        raise ValueError(f"target must be mcp|skill|both, got {target!r}")
    if auth_type not in ("auto", "session", "api-key"):
        raise ValueError(f"auth_type must be auto|session|api-key, got {auth_type!r}")

    # A bound-but-wrong slug is as broken as an unbound one, and harder to spot:
    # the skill routes through call_web_api and looks correct, but Tabby resolves
    # the name to nothing, so every call comes back as login_required and the
    # assistant tells the user to sign in to a profile that does not exist. Seen
    # live with a skill compiled as "icici-credit-card" against a profile actually
    # named "icici-retail-netbanking".
    #
    # Only fail on a definite negative — _profile_slug_resolves returns None when
    # it could not check, and an offline compile must still work.
    if profile_slug and not allow_unbound_profile:
        if _profile_slug_resolves(profile_slug) is False:
            known = _known_profile_slugs()
            hint = f" Known profiles: {', '.join(known)}." if known else ""
            raise ValueError(
                f"Tabby profile {profile_slug!r} does not exist, so the compiled skill "
                f"would resolve to nothing at run time and every call would report "
                f"login_required.{hint} Pass an existing --profile-slug, register the "
                "App Template first, or --allow-unbound-profile to skip this check."
            )

    declared_strategy: str | None = {
        "session": "tabby_credentials",
        "api-key": "static_secret_header",
        "auto": None,
    }[auth_type]
    static_secret_headers = [api_key_header or "Authorization"] if auth_type == "api-key" else None

    # A declared static API-key app has no paired login profile: don't try to
    # fetch login headers or widen a login scope that doesn't exist.
    if auth_type == "api-key":
        login_credential_headers = []
    elif login_credential_headers is None:
        login_credential_headers = _fetch_login_credential_headers(profile_slug)

    name = name or f"recording-{session_id[:8]}"
    slug = app_slug(name)
    server_id = f"{slug}-{session_id[:8]}"
    har = bundle.get("har") or {}
    clicks = bundle.get("click_events", [])
    urls = bundle.get("url_events", [])
    root = Path(output_root or settings.workbench_dir)

    result: dict = {}

    # Auto-route unreplayable apps to browser mode. A HAR-replay skill is useless
    # when the app encrypts every body in-page (the recorded request is an opaque
    # {data,key} blob only the live page can produce), and nothing downstream
    # notices until every call 403s at run time. Detect the fingerprint here and
    # switch, unless the caller already chose a mode. Skill target only.
    browser_detection: dict | None = None
    if (
        not browser_driven
        and auto_detect_browser
        and auth_type != "api-key"
        and target in ("skill", "both")
    ):
        from noui_core.compile.unreplayable import detect_unreplayable

        app_origin = ""
        _first = start_url or next((u.get("to_url", "") for u in urls if u.get("to_url")), "")
        if _first:
            from urllib.parse import urlparse as _urlparse

            _p = _urlparse(_first)
            app_origin = f"{_p.scheme}://{_p.netloc}"
        browser_detection = detect_unreplayable(har, app_origin=app_origin)
        if browser_detection.get("unreplayable"):
            browser_driven = True

    if target in ("mcp", "both"):
        result["mcp"] = compile_workflow(
            session_id=session_id,
            session_name=name,
            app_slug=slug,
            tabby_profile_id="",
            har=har,
            click_events=clicks,
            url_events=urls,
            output_dir=str(root / "mcp_servers" / slug / server_id),
            profile_slug=profile_slug,
            profile_db_id="",
            execution_mode=execution_mode,
            login_credential_headers=login_credential_headers,
            declared_strategy=declared_strategy,
            static_secret_headers=static_secret_headers,
        )
    if target in ("skill", "both"):
        if browser_driven:
            # Browser-driven: read the rendered page via call_web_browser instead
            # of replaying requests. For apps whose bodies/headers are minted
            # in-page and cannot be replayed. Derive the login/app origin from
            # start_url or the first recorded URL so page selection can keep the
            # app's own origin and drop login/third-party pages.
            from noui_core.compile.browser_skill import generate_browser_skill

            login_url = start_url or next(
                (u.get("to_url", "") for u in urls if u.get("to_url")), ""
            )
            result["skill"] = generate_browser_skill(
                app_slug=slug,
                app_name=slug.replace("-", " ").replace("_", " ").title(),
                workflow_name=name,
                profile_slug=profile_slug,
                url_events=urls,
                click_events=clicks,
                login_url=login_url,
                output_dir=str(root / "skills" / slug),
                session_id=session_id,
                start_url=start_url,
            )
        else:
            result["skill"] = compile_workflow_to_skill(
                session_id=session_id,
                session_name=name,
                app_slug=slug,
                tabby_profile_id="",
                har=har,
                click_events=clicks,
                url_events=urls,
                output_dir=str(root / "skills" / slug),
                profile_slug=profile_slug,
                profile_db_id="",
                description_override="",
                execution_mode=execution_mode,
                start_url=start_url,
                login_credential_headers=login_credential_headers,
                declared_strategy=declared_strategy,
                static_secret_headers=static_secret_headers,
                allow_unbound_profile=allow_unbound_profile,
            )

    # Best-effort: if this workflow's auth headers are already dynamically
    # captured by the paired login profile (login_credential_headers non-empty),
    # widen that profile's scope to cover this workflow's hosts too — see
    # `activate/register.py::extend_login_scope_for_workflow` for why this is
    # necessary (target_urls otherwise never covers hosts only visited during
    # a LATER workflow capture, so the declared header never gets captured).
    if login_credential_headers and profile_slug:
        try:
            from noui_core.activate.register import extend_login_scope_for_workflow
            from noui_core.compile.auth_plan import (
                _extract_observed_auth_headers,
                _extract_target_domains,
            )

            observed_headers = [
                h for h in login_credential_headers if h in _extract_observed_auth_headers(har)
            ]
            result["scope_extension"] = extend_login_scope_for_workflow(
                profile_slug,
                target_domains=_extract_target_domains(har),
                required_headers=observed_headers,
            )
        except Exception as exc:  # noqa: BLE001 — never break compilation over this
            result["scope_extension"] = {
                "status": "skipped",
                "reason": f"{type(exc).__name__}: {exc}",
            }

    # Surface the auto-detection so the caller (capture_import / the harness) can
    # tell the user WHY browser mode was chosen, rather than switching silently.
    if browser_detection is not None:
        result["browser_detection"] = browser_detection

    return result
