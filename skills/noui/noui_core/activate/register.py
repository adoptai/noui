"""Pillar 3 — Activate: register a compiled login bundle with Tabby.

Creates an Application + STAGING ServiceProfile from a compiled login result
(the dict produced by noui_core.compile.login). Optionally promotes
STAGING → CANARY → ACTIVE so the runtime resolver (ACTIVE/CANARY only) can
resolve the profile at the first tool call.

Registration of apps/profiles requires an admin token (POST /apps,
POST /admin/profiles). It is read from TABBY_ADMIN_TOKEN.
"""

from __future__ import annotations

import os

from noui_core import tabby_client
from noui_core.config import settings


def resolve_admin_token() -> str:
    token = os.environ.get("TABBY_ADMIN_TOKEN", "") or settings.tabby_admin_token
    if not token:
        raise RuntimeError(
            "TABBY_ADMIN_TOKEN must be set to register apps/profiles with Tabby."
        )
    return token


def register_login(
    result: dict,
    *,
    promote: bool = False,
    as_template: bool = False,
    token: str = "",
) -> dict:
    """Register App + STAGING ServiceProfile from a compiled login result.

    Args:
        result: the dict from compile.login (application_draft, service_profile_draft, validation).
        promote: if True, promote STAGING → CANARY → ACTIVE.
        as_template: if True, also create a tenant-wide App Template for
            per-user auto-provisioning (platform_jwt / federated users).
        token: admin token; defaults to TABBY_ADMIN_TOKEN.

    Returns {app_id, profile_db_id, profile_id, version_state, template_id?}.
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

    app = tabby_client.register_application(result, token)
    app_id = app["app_id"]
    profile = tabby_client.register_service_profile(result, token, app_id)
    profile_db_id = profile["id"]

    version_state = "STAGING"
    if promote:
        # Tabby promotes one step at a time: STAGING → CANARY → ACTIVE.
        tabby_client.promote_profile(profile_db_id, token)
        tabby_client.promote_profile(profile_db_id, token)
        version_state = "ACTIVE"

    out = {
        "app_id": app_id,
        "profile_db_id": profile_db_id,
        "profile_id": profile_id,
        "version_state": version_state,
    }

    if as_template:
        # Local import avoids a compile↔activate import cycle at module load.
        from noui_core.compile.login_assets import build_app_template_payload

        payload = build_app_template_payload(
            result.get("application_draft", {}),
            result.get("service_profile_draft", {}),
        )
        template = tabby_client.register_app_template(payload, token)
        out["template_id"] = template.get("id", "")

    return out
