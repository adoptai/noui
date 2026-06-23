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
) -> dict:
    """Compile a workflow bundle to ``target`` ("mcp" | "skill" | "both").

    Returns {"mcp": manifest?, "skill": manifest?} for whichever were built.
    """
    if target not in ("mcp", "skill", "both"):
        raise ValueError(f"target must be mcp|skill|both, got {target!r}")

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
        )
    return result
