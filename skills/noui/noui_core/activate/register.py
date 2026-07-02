"""Pillar 3 — Activate: register a compiled login bundle with Tabby.

Template-first: a login recording becomes a tenant-wide **App Template** (the
per-user auto-provisioning blueprint) — we never create an App/ServiceProfile
directly. When a federated member (platform JWT, owner_user_id set) first
requests the profile slug, Tabby's ``autoProvisionFromTemplate`` clones a
private, owner-scoped App + Profile (straight to ACTIVE) + session for them. A
directly-created App is creator-only / tenant-shared with no per-user isolation.

Creating the template (POST /admin/app-templates) is gated by Tabby at the
**Editor** role — NOT Admin; the /admin/ prefix is a URL namespace, not an
admin-credential gate. `resolve_admin_token()` prefers a platform-JWT-exchanged
token (ADOPT_API_URL/ADOPT_CLIENT_ID/ADOPT_CLIENT_SECRET) when configured — a
real human account's token-exchanged role is typically Editor or above, which
is enough for every endpoint this module touches — falling back to a raw
TABBY_ADMIN_TOKEN, then to the broker's forwarded bearer. This means local dev
doesn't need a separately-minted admin token if platform_jwt is already set up
(the same credentials execution already uses at runtime).
"""

from __future__ import annotations

import os

from noui_core import tabby_client
from noui_core.config import settings


def resolve_admin_token() -> str:
    """Resolve an Editor+-role bearer for the Editor-gated admin endpoints
    this module calls (POST /admin/app-templates, GET/PATCH .../{id}).

    Preference order:
      1. broker mode — the harness forwards the user's own federated bearer;
         no local credential resolution happens at all.
      2. platform_jwt (ADOPT_API_URL/ADOPT_CLIENT_ID/ADOPT_CLIENT_SECRET set)
         — exchanges for a Tabby JWT carrying the caller's real IdP-resolved
         role (see tabby_client.get_platform_tabby_token). Preferred because
         it reuses the same credentials NoUI already needs for runtime
         execution, so local dev doesn't need a second, separately-managed
         TABBY_ADMIN_TOKEN.
      3. TABBY_ADMIN_TOKEN — a directly-configured bearer, for local/self-host
         setups with no platform_jwt integration.
    """
    if settings.broker_mode():
        if not settings.broker_token:
            raise RuntimeError(
                "NOUI_TABBY_AUTH_MODE=broker but NOUI_BROKER_TOKEN is unset — the harness "
                "must inject the per-conversation capability token into the sandbox env."
            )
        return settings.broker_token

    adopt_api_url = os.environ.get("ADOPT_API_URL", "").rstrip("/")
    adopt_client_id = os.environ.get("ADOPT_CLIENT_ID", "")
    adopt_client_secret = os.environ.get("ADOPT_CLIENT_SECRET", "")
    if adopt_api_url and adopt_client_id and adopt_client_secret:
        return tabby_client.get_platform_tabby_token(
            adopt_api_url, adopt_client_id, adopt_client_secret
        )

    token = os.environ.get("TABBY_ADMIN_TOKEN", "") or settings.tabby_admin_token
    if not token:
        raise RuntimeError(
            "No Editor+ credential available: set ADOPT_API_URL/ADOPT_CLIENT_ID/"
            "ADOPT_CLIENT_SECRET (preferred) or TABBY_ADMIN_TOKEN to create the "
            "App Template in Tabby."
        )
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

    This closes that gap by updating the App **Template only** (via the
    existing `merge_template_export_policy`, whose whole purpose is exactly
    this additive merge) — never an individual App directly. Apps are
    provisioned FROM templates (see module docstring); keeping the template
    as the single source of truth means: (a) every future auto-provisioned
    profile inherits the wider scope automatically, (b) this needs only an
    Editor-role token (a platform-JWT-exchanged one works fine — see
    `resolve_admin_token()`), since the template list/patch endpoints have no
    Admin-only gate, unlike `/apps/{id}` (GET there is Admin/Operator/Viewer
    only — Editor is excluded, asymmetrically with `PUT /apps/{id}` which
    does allow it). An already-provisioned App may not see the widened scope
    until it's next (re-)provisioned; that's an accepted tradeoff for staying
    off the Apps API entirely, not something this function works around.

    Best-effort by design: any failure (profile/template not found, Tabby
    unreachable, insufficient role) is swallowed and reported in the returned
    dict rather than raised — this runs as a post-compile nicety, not a
    required step, and a failure here must never break workflow compilation.

    Args:
        profile_slug: the login profile's slug (== workflow's --profile-slug;
            also the template's `profile_name_pattern`).
        target_domains: netloc values (e.g. "spendmgmt.api.intuit.com") the
            workflow capture actually hit — typically `auth_plan["target_domains"]`.
        required_headers: header names the workflow requires — typically
            `auth_plan["required_auth"]["headers"]`.
        token: bearer for the admin-app-templates endpoints touched here;
            defaults to `resolve_admin_token()`.

    Returns a dict: `{"status": "extended"|"unchanged"|"skipped", "reason": str}`
    (plus `template_id` when applicable). Never raises.
    """
    if not target_domains and not required_headers:
        return {"status": "skipped", "reason": "nothing to extend"}

    try:
        token = token or resolve_admin_token()
    except RuntimeError as exc:
        return {"status": "skipped", "reason": str(exc)}

    try:
        template = tabby_client.get_app_template_by_profile_slug(profile_slug, token)
        if not template:
            return {
                "status": "skipped",
                "reason": f"no App Template found with profile_name_pattern {profile_slug!r}",
            }
        template_id = template.get("id", "")

        # "/**" is required, not cosmetic: Tabby's request-header-capture matcher
        # (artifact-extractor.ts's buildUrlMatcher) turns each target_urls entry
        # into a FULLY-ANCHORED regex (`^...$`) — a bare origin only matches that
        # exact string, never a real request path like "/api/v4/graphql". See
        # `login_assets.py::generate()`'s identical fix for the same reason.
        new_target_urls = [f"https://{d}/**" for d in dict.fromkeys(target_domains) if d]

        from noui_core.compile.login_assets import merge_template_export_policy

        # Start from the EXISTING export_policy (preserving fields like
        # encryption/ttl_seconds untouched by the additive-merge loop below)
        # and only add the new bits — merge_template_export_policy unions
        # header_allowlist/request_header_allowlist/target_urls/artifact_types
        # with the "old" template, but takes everything else in new_payload
        # as-is, so a bare partial object here would silently drop required
        # fields from the saved template.
        new_export_policy = dict(template.get("export_policy") or {})
        new_export_policy["target_urls"] = new_target_urls
        # export_policy.target_domains feeds the SERVICE PROFILE's own
        # target_domains field when the template update propagates to linked
        # profiles (AppTemplatesService.propagateToLinkedApps reads it from
        # exportPolicy.target_domains, a separate key from target_urls) — must
        # be set explicitly, plain hostnames (no scheme, no wildcard suffix).
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

        if merged.get("export_policy") == template.get("export_policy"):
            return {
                "status": "unchanged",
                "reason": "scope already covers this workflow",
                "template_id": template_id,
            }

        tabby_client.update_app_template(
            template_id, {"export_policy": merged["export_policy"]}, token
        )
        return {"status": "extended", "template_id": template_id}
    except Exception as exc:  # noqa: BLE001 — best-effort, never break compilation
        return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}
