"""Pillar 2 — compile a *login* capture bundle into Tabby App + ServiceProfile drafts.

Orchestration over login_assets.generate. The result dict carries
``application_draft`` and ``service_profile_draft`` consumed by
noui_core.activate.register.
"""

from __future__ import annotations

from typing import Any

from noui_core.compile.login_assets import generate


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
    return generate(
        session,
        bundle.get("click_events", []),
        bundle.get("url_events", []),
        har=bundle.get("har"),
        auth_mode=auth_mode,
    )
