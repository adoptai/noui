"""NoUI Skill generator.

`compile_workflow_to_skill(...)` is the single public entry point. Mirrors
`noui_core.compile.server_generator.compile_workflow` so the backend export endpoint
can branch on --as mcp|skill|both without reshaping its inputs.

Output tree:

    <output_dir>/
        SKILL.md
        manifest.json
        API.md
        auth_plan.json          (when auth is required)
        noui_runtime/
            __init__.py
            auth.py             (identical to the MCP-server runtime)
        operations/
            __init__.py
            <op>.py             (one standalone CLI script per tool)

With execution_mode="harness" the tree carries no transport code at all
(the Agent Harness sandbox must never call Tabby directly):

    <output_dir>/
        SKILL.md                (call_web_api operation cards)
        manifest.json
        API.md
        operations.json         (machine-readable request recipes)
        auth_plan.json          (when auth is required)
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from noui_core.activate.auth_adapter import generate_auth_adapter
from noui_core.activate.execute_adapter import generate_execute_adapter
from noui_core.compile.api_doc_generator import generate_api_markdown
from noui_core.compile.auth_plan import generate_auth_plan
from noui_core.compile.har_to_tools import har_to_tool_defs
from noui_core.compile.harness_md_generator import (
    render_harness_skill_md,
    render_operations_json,
    secret_names,
)
from noui_core.compile.operation_generator import render_skill_operation
from noui_core.compile.skill_md_generator import render_skill_md

_VALID_EXECUTION_MODES = ("tabby", "http", "harness")


def compile_workflow_to_skill(
    *,
    session_id: str,
    session_name: str,
    app_slug: str,
    tabby_profile_id: str,
    har: dict,
    click_events: list[dict],  # noqa: ARG001 – reserved for future ranking
    url_events: list[dict],  # noqa: ARG001 – reserved for future ranking
    output_dir: str,
    profile_slug: str = "",
    profile_db_id: str = "",
    description_override: str = "",
    execution_mode: str = "tabby",
    start_url: str = "",
    login_credential_headers: list[str] | None = None,
    declared_strategy: str | None = None,
    static_secret_headers: list[str] | None = None,
) -> dict:
    """Compile a recorded workflow session into an installable Claude Code skill.

    See `compile_workflow` in noui_core.compile.server_generator for `execution_mode`,
    `login_credential_headers`, `declared_strategy`, and `static_secret_headers`
    semantics — the two compilers stay in lockstep.

    Returns the manifest dict (same content as manifest.json).
    """
    if execution_mode not in _VALID_EXECUTION_MODES:
        raise ValueError(
            f"Invalid execution_mode {execution_mode!r}. Expected one of {_VALID_EXECUTION_MODES}."
        )

    from noui_core.config import settings as _settings

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    effective_slug = profile_slug or tabby_profile_id or ""
    skill_id = app_slug  # skills use app_slug directly; re-export overwrites silently
    app_name = _slug_to_title(app_slug)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Tool definitions (shared with MCP compiler)
    tool_defs = har_to_tool_defs(
        har,
        workflow_name=session_name,
        tabby_profile_id=effective_slug,
    )

    # Record the workflow's start URL in the manifest so downstream tooling
    # (e.g. `noui tabby session ensure --skill <id>`) can navigate the browser
    # to the right page without the user specifying it again. Prefer the
    # explicit value passed by the caller (the workflow session's stored
    # `start_url`); fall back to the first HTTP entry in the HAR for callers
    # that don't have one handy.
    if not start_url:
        for entry in har.get("log", {}).get("entries", []) or []:
            url = (entry.get("request", {}) or {}).get("url", "")
            if url.startswith("http://") or url.startswith("https://"):
                start_url = url.split("?")[0]
                break

    # 2. Auth plan (shared)
    auth_headers_seen: list[str] = []
    auth_cookies_seen: list[str] = []
    for td in tool_defs:
        for h in td.get("auth_headers", []):
            if h not in auth_headers_seen:
                auth_headers_seen.append(h)
        for c in td.get("auth_cookies", []):
            if c not in auth_cookies_seen:
                auth_cookies_seen.append(c)

    has_auth = bool(effective_slug or auth_headers_seen or auth_cookies_seen)

    auth_plan: dict = {}
    if has_auth:
        auth_info = {
            "has_auth_headers": bool(auth_headers_seen),
            "has_cookies": bool(auth_cookies_seen),
            "has_csrf": any("csrf" in h.lower() or "xsrf" in h.lower() for h in auth_headers_seen),
            "auth_header_names": auth_headers_seen,
            "csrf_header_names": [
                h for h in auth_headers_seen if "csrf" in h.lower() or "xsrf" in h.lower()
            ],
            "set_cookie_names": auth_cookies_seen,
            "auth_domains": [],
        }
        auth_plan = generate_auth_plan(
            har=har,
            auth_info=auth_info,
            profile_slug=effective_slug,
            profile_db_id=profile_db_id,
            app_slug=app_slug,
            login_credential_headers=login_credential_headers,
            declared_strategy=declared_strategy,
            static_secret_headers=static_secret_headers,
        )

    # Harness mode ships no transport code and never holds the secret value: a
    # static-secret-header workflow emits ${SECRET:name} placeholders that the
    # harness resolves server-side (gap G1). Record which secrets an admin must
    # configure in the harness secret store.
    harness_secrets_required = secret_names(auth_plan) if execution_mode == "harness" else []

    # 3. noui_runtime/auth.py (shared template, identical bytes for both outputs).
    # Harness mode emits no runtime: operations execute via the harness
    # `call_web_api` tool (or plain curl), not via shipped Python transport.
    if execution_mode != "harness":
        runtime_dir = out_path / "noui_runtime"
        runtime_dir.mkdir(exist_ok=True)
        (runtime_dir / "__init__.py").write_text("", encoding="utf-8")
        (runtime_dir / "auth.py").write_text(
            generate_auth_adapter(_settings.tabby_api_host), encoding="utf-8"
        )
        if execution_mode == "tabby":
            (runtime_dir / "execute.py").write_text(generate_execute_adapter(), encoding="utf-8")

    # 4. pyproject.toml + .python-version — per-skill Python environment (retro D1).
    # Needs httpx for any execution mode (execute endpoint or direct HTTP).
    # Harness skills have no Python environment at all (nothing to run).
    if execution_mode != "harness":
        pyproject_deps = ['"httpx>=0.27"']
        pyproject_toml = (
            f"[project]\n"
            f'name = "{skill_id}"\n'
            f'version = "0.1.0"\n'
            f'description = "NoUI-generated skill for {app_name}."\n'
            f'requires-python = ">=3.11"\n'
            f"dependencies = [\n" + "".join(f"    {d},\n" for d in pyproject_deps) + "]\n"
            "\n"
            "[tool.uv]\n"
            "package = false\n"
        )
        (out_path / "pyproject.toml").write_text(pyproject_toml, encoding="utf-8")
        (out_path / ".python-version").write_text("3.11\n", encoding="utf-8")

    # 5. operations/*.py (skill-specific rendering with CLI wrapper) — or, in
    # harness mode, operations.json (machine-readable call_web_api recipes).
    op_files: list[str] = []
    op_entries: list[dict] = []
    if execution_mode == "harness":
        (out_path / "operations.json").write_text(
            render_operations_json(tool_defs, profile_slug=effective_slug, auth_plan=auth_plan),
            encoding="utf-8",
        )
        op_files.append("operations.json")
    else:
        ops_dir = out_path / "operations"
        ops_dir.mkdir(exist_ok=True)
        (ops_dir / "__init__.py").write_text("", encoding="utf-8")

    for td in tool_defs:
        if execution_mode != "harness":
            op_src = render_skill_operation(td, auth_plan=auth_plan, execution_mode=execution_mode)
            op_file = ops_dir / f"{td['name']}.py"
            op_file.write_text(op_src, encoding="utf-8")
            op_files.append(f"operations/{td['name']}.py")
        op_entries.append(
            {
                "name": td["name"],
                "description": td.get("description", td["name"]),
                **(
                    {"module": f"operations/{td['name']}.py", "entry": "execute"}
                    if execution_mode != "harness"
                    else {
                        "recipe": "operations.json",
                        "tool": "call_web_api" if effective_slug else "bash",
                    }
                ),
                "method": td["method"],
                "path": td["path"],
                "args": [
                    {
                        "name": p["name"],
                        "type": p.get("type", "string"),
                        "required": bool(p.get("required", True)),
                        **(
                            {"default": p["default"]}
                            if not p.get("required", True) and "default" in p
                            else {}
                        ),
                    }
                    for p in td.get("params", [])
                ],
            }
        )

    # 6. SKILL.md (frontmatter + body — what Claude loads when intent matches)
    if execution_mode == "harness":
        skill_md = render_harness_skill_md(
            skill_id=skill_id,
            app_name=app_name,
            workflow_name=session_name,
            tool_defs=tool_defs,
            auth_plan=auth_plan,
            profile_slug=effective_slug,
            description_override=description_override,
        )
    else:
        skill_md = render_skill_md(
            skill_id=skill_id,
            app_name=app_name,
            app_slug=app_slug,
            workflow_name=session_name,
            tool_defs=tool_defs,
            auth_plan=auth_plan,
            profile_slug=effective_slug,
            description_override=description_override,
            python_executable=".venv/bin/python",
        )
    (out_path / "SKILL.md").write_text(skill_md, encoding="utf-8")

    # 6. API.md (reuse MCP generator — format-identical)
    api_md = generate_api_markdown(
        server_id=skill_id,
        app_name=app_name,
        app_slug=app_slug,
        workflow_name=session_name,
        tool_defs=tool_defs,
        tabby_profile_id=effective_slug,
        generated_at=generated_at,
    )
    (out_path / "API.md").write_text(api_md, encoding="utf-8")

    # 7. auth_plan.json (when auth is required)
    if auth_plan:
        if effective_slug:
            auth_plan["profile_slug"] = effective_slug
            if "tabby_export" in auth_plan:
                auth_plan["tabby_export"]["runtime_identifier"] = effective_slug
        (out_path / "auth_plan.json").write_text(
            json.dumps(auth_plan, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # 9. manifest.json
    if execution_mode == "harness":
        all_files = ["SKILL.md", "API.md", *op_files]
    else:
        all_files = [
            "SKILL.md",
            "API.md",
            "pyproject.toml",
            ".python-version",
            "noui_runtime/__init__.py",
            "noui_runtime/auth.py",
            "operations/__init__.py",
            *op_files,
        ]
        if execution_mode == "tabby":
            all_files.append("noui_runtime/execute.py")
    if auth_plan:
        all_files.append("auth_plan.json")

    auth_strategy = auth_plan.get("strategy", "") if auth_plan else ""
    resolved_auth_strategy = auth_strategy or ("tabby_credentials" if has_auth else None)
    execution_strategy: str | None
    if execution_mode == "tabby":
        execution_strategy = "tabby_execute_fetch"
    elif execution_mode == "harness":
        execution_strategy = "harness_call_web_api"
    else:
        execution_strategy = resolved_auth_strategy

    manifest: dict = {
        "schema_version": "1",
        "skill_id": skill_id,
        "app": {
            "name": app_name,
            "slug": app_slug,
        },
        "workflow": {
            "id": skill_id,
            "name": session_name,
            "workflow_session_id": session_id,
            "start_url": start_url,
        },
        "auth": {
            "requires_auth": has_auth,
            "profile_slug": effective_slug or None,
            "profile_db_id": profile_db_id or None,
            "strategy": resolved_auth_strategy,
            "execution_strategy": execution_strategy,
            "auth_plan_file": "auth_plan.json" if auth_plan else None,
        },
        "runtime": (
            {
                "type": "agent-harness-skill",
                "entrypoint": "SKILL.md",
                "operation_style": "call_web_api",
            }
            if execution_mode == "harness"
            else {
                "type": "claude-code-skill",
                "entrypoint": "SKILL.md",
                "operation_style": "subprocess-cli",
                "python": ">=3.11",
                "python_executable": ".venv/bin/python",
            }
        ),
        "operations": op_entries,
        "artifacts": {
            "skill_file": "SKILL.md",
            "files": all_files,
            "api_docs_file": "API.md",
        },
        "generation": {
            "generated_at": generated_at,
            "generator": "noui",
            "generator_version": "v1-skill",
        },
        **({"secrets_required": harness_secrets_required} if harness_secrets_required else {}),
    }

    (out_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return manifest


def _slug_to_title(slug: str) -> str:
    return " ".join(word.capitalize() for word in re.split(r"[-_]+", slug))
