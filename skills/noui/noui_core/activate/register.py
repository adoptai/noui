"""Pillar 3 — Activate: register a compiled login bundle with Tabby.

Template-first: a login recording becomes a tenant-wide **App Template** (the
per-user auto-provisioning blueprint) — we never create an App/ServiceProfile
directly. When a federated member (platform JWT, owner_user_id set) first
requests the profile slug, Tabby's ``autoProvisionFromTemplate`` clones a
private, owner-scoped App + Profile (straight to ACTIVE) + session for them. A
directly-created App is creator-only / tenant-shared with no per-user isolation.

Creating the template (POST /admin/app-templates) is gated by Tabby at the
**Editor** role — NOT Admin; the /admin/ prefix is a URL namespace, not an
admin-credential gate. In local/self-host mode the bearer is read from
TABBY_ADMIN_TOKEN (any Editor+ token works); in broker mode the sandbox sends its
capability and the broker forwards the user's own federated (Editor) bearer. The
only genuinely Admin-gated bit is the cross-tenant ``tenant_id`` override.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from noui_core import tabby_client
from noui_core.config import settings


def resolve_admin_token() -> str:
    # In broker mode the sandbox holds no Tabby credential — it sends the opaque
    # per-conversation capability token; the broker swaps it for the user's own
    # federated (Editor-role) bearer and forwards. No admin privileges are
    # injected: App Template creation is Editor-gated, and the broker allowlists it.
    if settings.broker_mode():
        if not settings.broker_token:
            raise RuntimeError(
                "NOUI_TABBY_AUTH_MODE=broker but NOUI_BROKER_TOKEN is unset — the harness "
                "must inject the per-conversation capability token into the sandbox env."
            )
        return settings.broker_token
    token = os.environ.get("TABBY_ADMIN_TOKEN", "") or settings.tabby_admin_token
    if not token:
        raise RuntimeError("TABBY_ADMIN_TOKEN must be set to create the App Template in Tabby.")
    return token


def register_login(
    result: dict,
    *,
    token: str = "",
    tenant_id: str = "",
) -> dict:
    """Register a compiled login as a tenant-wide Tabby **App Template**.

    Template-first is the ONLY path — we never create an App/ServiceProfile
    directly. The template is the per-user auto-provisioning blueprint: when a
    federated member (platform JWT, ``owner_user_id`` set) first requests this
    profile slug, Tabby's ``autoProvisionFromTemplate`` clones a PRIVATE,
    owner-scoped App + Profile (straight to ACTIVE) + session for that member. A
    directly-created App would be creator-only / tenant-shared with no per-user
    isolation — exactly what we must avoid.

    Args:
        result: the dict from compile.login (application_draft,
            service_profile_draft, validation).
        token: bearer for POST /admin/app-templates; defaults to
            resolve_admin_token. Editor role suffices (not Admin-only).
        tenant_id: Admin-only override — create the template in this tenant. Pass
            the *agent token's* tenant so the agent can resolve + drive the
            per-user profiles auto-provisioned from it.

    Returns {template_id, profile_id}. ``profile_id`` is the template's
    ``profile_name_pattern`` (the runtime slug generated ops bake in). There is
    no profile to promote — per-user provisioning lands directly in ACTIVE.
    """
    if not tabby_client.is_alive():
        raise RuntimeError(f"Tabby API not reachable at {settings.tabby_api_host}")

    validation = result.get("validation", {})
    if not validation.get("generator_valid", True):
        raise RuntimeError(f"Generated profile is invalid: {validation.get('issues')}")

    profile_id = result.get("service_profile_draft", {}).get("profile_id", "")
    if not profile_id:
        raise RuntimeError("Bundle is missing service_profile_draft.profile_id")

    token = token or resolve_admin_token()

    # Local import avoids a compile↔activate import cycle at module load.
    from noui_core.compile.login_assets import build_app_template_payload

    payload = build_app_template_payload(
        result.get("application_draft", {}),
        result.get("service_profile_draft", {}),
    )
    template = tabby_client.register_app_template(payload, token, tenant_id=tenant_id)
    return {
        "template_id": template.get("id", ""),
        "profile_id": profile_id,
    }


def extend_login_scope_for_workflow(
    profile_slug: str,
    *,
    target_domains: list[str],
    required_headers: list[str],
    token: str = "",
) -> dict:
    """Widen an already-registered login profile's scope to cover a later
    workflow capture, so Tabby's dynamic header capture actually activates.

    A login is compiled/registered before any workflow that reuses it (via
    `--from <login-session>` / `--profile <slug>`), so at login time NoUI has
    no way to know which hosts a later workflow will need — `target_urls` /
    `export_policy.target_urls` only ever cover the login flow's own origin
    and its post-login landing page (see `login_assets.py::generate()`). If
    the workflow's non-cookie auth headers are the SAME ones the login profile
    already declares in `credential_types.headers` (i.e. `auth_plan.py`
    decided `tabby_credentials` via `login_credential_headers`), but the
    workflow's hosts aren't in that scope yet, Tabby's request-header-capture
    listener (`registerRequestHeaderCapture`, matched against `target_urls`)
    will never see a matching request and the declared header stays empty
    forever.

    This closes that gap: it unions the workflow's hosts into BOTH the App
    Template (so future auto-provisioned profiles inherit the wider scope —
    via the existing `merge_template_export_policy`, whose whole purpose is
    exactly this additive merge) AND the already-provisioned App's own
    top-level `target_urls` column (which template-update propagation does
    NOT touch — see `tabby_client.update_app`'s docstring).

    Best-effort by design: any failure (profile/app/template not found, Tabby
    unreachable, insufficient role) is swallowed and reported in the returned
    dict rather than raised — this runs as a post-compile nicety, not a
    required step, and a failure here must never break workflow compilation.

    Args:
        profile_slug: the login profile's slug (== workflow's --profile-slug).
        target_domains: netloc values (e.g. "spendmgmt.api.intuit.com") the
            workflow capture actually hit — typically `auth_plan["target_domains"]`.
        required_headers: header names the workflow requires — typically
            `auth_plan["required_auth"]["headers"]`.
        token: bearer for the admin endpoints touched here (app-templates,
            apps); defaults to `resolve_admin_token()`.

    Returns a dict: `{"status": "extended"|"unchanged"|"skipped", "reason": str}`
    (plus `template_id`/`app_id` when applicable). Never raises.
    """
    if not target_domains and not required_headers:
        return {"status": "skipped", "reason": "nothing to extend"}

    try:
        token = token or resolve_admin_token()
    except RuntimeError as exc:
        return {"status": "skipped", "reason": str(exc)}

    try:
        profile = tabby_client.get_service_profile_by_slug(profile_slug, token)
        if not profile:
            return {"status": "skipped", "reason": f"no profile found for slug {profile_slug!r}"}

        app_id = profile.get("app_id")
        if not app_id:
            return {"status": "skipped", "reason": "profile has no app_id"}

        app = tabby_client.get_app(app_id, token)
        if not app:
            return {"status": "skipped", "reason": f"app {app_id} not found"}

        # "/**" is required, not cosmetic: Tabby's request-header-capture matcher
        # (artifact-extractor.ts's buildUrlMatcher) turns each target_urls entry
        # into a FULLY-ANCHORED regex (`^...$`) — a bare origin only matches that
        # exact string, never a real request path like "/api/v4/graphql". See
        # `login_assets.py::generate()`'s identical fix for the same reason.
        new_target_urls = [f"https://{d}/**" for d in dict.fromkeys(target_domains) if d]

        # ---- Extend the App Template (future auto-provisions) ----
        template_id = app.get("template_id")
        template_result: dict = {}
        if template_id:
            template = tabby_client.get_app_template(template_id, token)
            if template:
                from noui_core.compile.login_assets import merge_template_export_policy

                # Start from the EXISTING export_policy (preserving fields like
                # encryption/ttl_seconds untouched by the additive-merge loop
                # below) and only add the new bits — merge_template_export_policy
                # unions header_allowlist/request_header_allowlist/target_urls/
                # artifact_types with the "old" template, but takes everything
                # else in new_payload as-is, so a bare partial object here would
                # silently drop required fields from the saved template.
                new_export_policy = dict(template.get("export_policy") or {})
                new_export_policy["target_urls"] = new_target_urls
                # export_policy.target_domains feeds the SERVICE PROFILE's own
                # target_domains field when the template update propagates to
                # linked profiles (AppTemplatesService.propagateToLinkedApps
                # reads it from exportPolicy.target_domains, a separate key from
                # target_urls) — must be set explicitly, plain hostnames (no
                # scheme, no wildcard suffix).
                new_export_policy["target_domains"] = list(
                    dict.fromkeys([*new_export_policy.get("target_domains", []), *target_domains])
                )
                if required_headers:
                    new_export_policy["request_header_allowlist"] = list(required_headers)
                    new_export_policy["artifact_types"] = list(
                        dict.fromkeys([*new_export_policy.get("artifact_types", []), "headers"])
                    )
                    new_export_policy["credential_types"] = {
                        **(new_export_policy.get("credential_types") or {}),
                        "headers": list(required_headers),
                    }
                new_payload = {"export_policy": new_export_policy}
                merged = merge_template_export_policy(template, new_payload)
                if merged.get("export_policy") != template.get("export_policy"):
                    updated = tabby_client.update_app_template(
                        template_id, {"export_policy": merged["export_policy"]}, token
                    )
                    template_result = {"template_id": template_id, "template_updated": True}
                    _ = updated
                else:
                    template_result = {"template_id": template_id, "template_updated": False}

        # ---- Extend the already-provisioned App's own target_urls ----
        # Template-update propagation (AppTemplatesService.propagateToLinkedApps)
        # copies export_policy onto linked apps but does NOT touch an App's
        # separate top-level target_urls column — the field the worker's
        # request-header-capture listener actually matches requests against.
        existing_app_urls = app.get("target_urls") or []
        existing_hosts = {urlparse(u).netloc for u in existing_app_urls}
        missing = [u for u in new_target_urls if urlparse(u).netloc not in existing_hosts]
        app_updated = False
        if missing:
            tabby_client.update_app(app_id, {"target_urls": existing_app_urls + missing}, token)
            app_updated = True

        if not app_updated and not template_result.get("template_updated"):
            return {
                "status": "unchanged",
                "reason": "scope already covers this workflow",
                **template_result,
            }

        return {
            "status": "extended",
            "app_id": app_id,
            "app_updated": app_updated,
            **template_result,
        }
    except Exception as exc:  # noqa: BLE001 — best-effort, never break compilation
        return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}
