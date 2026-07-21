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
) -> dict:
    """Compile a workflow bundle to ``target`` ("mcp" | "skill" | "both").

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

    return result
