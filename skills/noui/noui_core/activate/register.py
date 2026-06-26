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
