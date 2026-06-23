"""NoUI MCP server generator.

compile_workflow(...) is the single public entry point. It:

1. Converts the captured HAR into tool definitions (via har_to_tools).
2. Detects auth signals and generates auth_plan.json.
3. Writes a complete FastMCP server tree to output_dir:
       server.py
       tools.json
       manifest.json
       API.md
       noui_runtime/auth.py
       auth_plan.json          (when auth is required)
       operations/<tool_name>.py   (one file per tool)
4. Returns the manifest dict.
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

_VALID_EXECUTION_MODES = ("tabby", "http")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compile_workflow(
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
    execution_mode: str = "tabby",
) -> dict:
    """Compile a recorded workflow session into a runnable FastMCP server.

    Args:
        tabby_profile_id: Legacy profile identifier (UUID or slug). Kept for
            backward compatibility. Prefer profile_slug for new integrations.
        profile_slug: Tabby profile slug for runtime credential requests
            (POST /credentials/request). Takes precedence over tabby_profile_id.
        profile_db_id: Tabby profile DB UUID for admin operations only.
            Never used for runtime credential requests.
        execution_mode: "tabby" (default — operations run inside Tabby's browser
            via the POST /execute/fetch endpoint; cookies ride on
            `credentials: 'include'`) or "http" (legacy — operations run in-process
            with httpx and credentials resolved from Tabby's /credentials/request
            endpoint). Pick "http" only when in-browser execution is impossible
            (CORS, server-to-server endpoints, no live Tabby).

    Returns the manifest dict (same content as manifest.json).
    """
    if execution_mode not in _VALID_EXECUTION_MODES:
        raise ValueError(
            f"Invalid execution_mode {execution_mode!r}. Expected one of {_VALID_EXECUTION_MODES}."
        )

    from noui_core.config import settings as _settings

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Resolve effective slug: explicit > legacy > empty
    effective_slug = profile_slug or tabby_profile_id or ""

    # Derive stable identifiers
    server_id = f"{app_slug}-{session_id[:8]}"
    app_name = _slug_to_title(app_slug)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── 1. Convert HAR → tool_defs ────────────────────────────────────────────
    tool_defs = har_to_tool_defs(
        har,
        workflow_name=session_name,
        tabby_profile_id=effective_slug,
    )

    # ── 2. Generate AuthPlan from HAR signals ─────────────────────────────────
    # Build a minimal auth_info dict from what har_to_tool_defs already detected
    # (avoids a second pass through detect_auth_from_har).
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
        )

    # ── 3. Write noui_runtime/auth.py (and execute.py under tabby mode) ───────
    runtime_dir = out_path / "noui_runtime"
    runtime_dir.mkdir(exist_ok=True)
    (runtime_dir / "__init__.py").write_text("", encoding="utf-8")
    (runtime_dir / "auth.py").write_text(
        generate_auth_adapter(_settings.tabby_api_host), encoding="utf-8"
    )
    if execution_mode == "tabby":
        (runtime_dir / "execute.py").write_text(generate_execute_adapter(), encoding="utf-8")

    # ── 4. Write operations/*.py ──────────────────────────────────────────────
    ops_dir = out_path / "operations"
    ops_dir.mkdir(exist_ok=True)
    (ops_dir / "__init__.py").write_text("", encoding="utf-8")

    op_files: list[str] = []
    for td in tool_defs:
        op_src = _render_operation(td, auth_plan=auth_plan, execution_mode=execution_mode)
        op_file = ops_dir / f"{td['name']}.py"
        op_file.write_text(op_src, encoding="utf-8")
        op_files.append(f"operations/{td['name']}.py")

    # ── 5. Write server.py ────────────────────────────────────────────────────
    server_src = _render_server(
        app_name=app_name,
        tool_defs=tool_defs,
    )
    (out_path / "server.py").write_text(server_src, encoding="utf-8")

    # ── 6. Write tools.json ───────────────────────────────────────────────────
    (out_path / "tools.json").write_text(
        json.dumps(tool_defs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # ── 7. Write API.md ───────────────────────────────────────────────────────
    api_md = generate_api_markdown(
        server_id=server_id,
        app_name=app_name,
        app_slug=app_slug,
        workflow_name=session_name,
        tool_defs=tool_defs,
        tabby_profile_id=effective_slug,
        generated_at=generated_at,
    )
    (out_path / "API.md").write_text(api_md, encoding="utf-8")

    # ── 8. Write auth_plan.json (when auth is required) ───────────────────────
    if auth_plan:
        # Ensure profile_slug in auth_plan always matches the manifest value so
        # the two files stay in sync regardless of how generate_auth_plan resolves it.
        if effective_slug:
            auth_plan["profile_slug"] = effective_slug
            if "tabby_export" in auth_plan:
                auth_plan["tabby_export"]["runtime_identifier"] = effective_slug
        (out_path / "auth_plan.json").write_text(
            json.dumps(auth_plan, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ── 9. Build and write manifest ───────────────────────────────────────────
    all_files = [
        "server.py",
        "tools.json",
        "noui_runtime/auth.py",
        *op_files,
        "API.md",
    ]
    if execution_mode == "tabby":
        all_files.append("noui_runtime/execute.py")
    if auth_plan:
        all_files.append("auth_plan.json")

    manifest_tools = [
        {
            "name": td["name"],
            "description": td["description"],
            "method": td["method"],
            "path": td["path"],
            "module": f"operations/{td['name']}.py",
        }
        for td in tool_defs
    ]

    auth_strategy = auth_plan.get("strategy", "") if auth_plan else ""
    resolved_auth_strategy = auth_strategy or ("tabby_credentials" if has_auth else None)
    execution_strategy = (
        "tabby_execute_fetch" if execution_mode == "tabby" else resolved_auth_strategy
    )

    manifest: dict = {
        "schema_version": "2",
        "server_id": server_id,
        "app": {
            "name": app_name,
            "slug": app_slug,
        },
        "workflow": {
            "id": server_id,
            "name": session_name,
            "workflow_session_id": session_id,
        },
        "auth": {
            # Legacy field kept for backward compatibility
            "tabby_profile_id": tabby_profile_id or None,
            "requires_auth": has_auth,
            # v2 fields
            "profile_slug": effective_slug or None,
            "profile_db_id": profile_db_id or None,
            "strategy": resolved_auth_strategy,
            "execution_strategy": execution_strategy,
            "auth_plan_file": "auth_plan.json" if auth_plan else None,
        },
        "runtime": {
            "type": "fastmcp",
            "entrypoint": "server.py",
            "transport": "stdio",
            "direct_execution": True,
        },
        "tools": manifest_tools,
        "artifacts": {
            "server_file": "server.py",
            "tools_file": "tools.json",
            "files": all_files,
            "api_docs_file": "API.md",
        },
        "generation": {
            "generated_at": generated_at,
            "generator": "noui",
            "generator_version": "v2",
        },
    }

    (out_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return manifest


# ---------------------------------------------------------------------------
# Code renderers
# ---------------------------------------------------------------------------


def _render_server(*, app_name: str, tool_defs: list[dict]) -> str:
    lines: list[str] = [
        f'"""Auto-generated FastMCP server: {app_name}',
        "Generated by NoUI v2",
        '"""',
        "from __future__ import annotations",
        "",
        "from mcp.server.fastmcp import FastMCP",
        "",
    ]

    # Imports
    for td in tool_defs:
        n = td["name"]
        lines.append(f"from operations import {n} as _op_{n}")

    lines += [
        "",
        "",
        f'mcp = FastMCP("{app_name}")',
        "",
    ]

    # Tool registrations
    for td in tool_defs:
        n = td["name"]
        params = td.get("params", [])
        sig_parts = _py_signature(params)
        call_parts = ", ".join(f"{p['name']}={p['name']}" for p in params)
        desc = td.get("description", "").replace('"""', "'''")

        lines.append("")
        lines.append("@mcp.tool()")
        if sig_parts:
            _sep = ",\n    "
            sig = f"async def {n}(\n    {_sep.join(sig_parts)},\n) -> dict:"
        else:
            sig = f"async def {n}() -> dict:"
        lines.append(sig)
        lines.append(f'    """{desc}"""')
        if call_parts:
            lines.append(f"    return await _op_{n}.execute({call_parts})")
        else:
            lines.append(f"    return await _op_{n}.execute()")

    lines += [
        "",
        "",
        'if __name__ == "__main__":',
        "    mcp.run()",
        "",
    ]
    return "\n".join(lines)


def _render_operation(td: dict, *, auth_plan: dict, execution_mode: str = "tabby") -> str:
    """Render a single operation module.

    Two execution modes:
      - "tabby" (default): the operation calls Tabby's POST /execute/fetch
        endpoint, which runs fetch() inside the authenticated browser via
        page.evaluate() with `credentials: 'include'`. Cookies and TLS
        fingerprint come from the real authenticated browser. No WebSocket,
        no direct CDP access.
      - "http" (legacy): the operation runs httpx in-process and resolves
        credentials via noui_runtime.auth.resolve_auth(). Strategy is driven by
        auth_plan["strategy"]:
          - "tabby_credentials" or "static_secret_header": call resolve_auth()
          - absent/empty: plain HTTP, recorded non-auth headers only
        Recorded non-auth headers (Accept, Content-Type, etc.) are merged with
        live auth headers so they are not dropped.
    """
    if execution_mode == "tabby":
        return _render_operation_tabby(td, auth_plan=auth_plan)
    return _render_operation_http(td, auth_plan=auth_plan)


def _render_operation_tabby(td: dict, *, auth_plan: dict) -> str:
    """Render an operation that executes inside Tabby's browser via execute/fetch."""
    name = td["name"]
    method = td["method"].upper()
    path_template = td["path"]
    base_url = td.get("base_url", "")
    content_type = td.get("request_content_type", "")
    params: list[dict] = td.get("params", [])
    request_headers: list[dict] = td.get("request_headers", [])
    description = td.get("description", "")

    profile_slug = auth_plan.get("profile_slug", "") if auth_plan else ""

    static_headers = {
        h["name"]: h["value"] for h in request_headers if h.get("name") and h.get("value")
    }

    body_params = [p for p in params if p.get("source") in ("body", None, "")]
    query_params = [p for p in params if p.get("source") == "query"]
    has_body = bool(body_params) and method in ("POST", "PUT", "PATCH")

    lines: list[str] = [
        f'"""Auto-generated operation: {name}',
        f"Method: {method}",
        f"Path: {path_template}",
        "",
        "Executes inside Tabby's authenticated browser via the execute/fetch endpoint.",
        "Requires a live Tabby session for the configured profile.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]
    if query_params:
        lines.append("import urllib.parse")
        lines.append("")
    lines += [
        "from noui_runtime.execute import execute_fetch",
        "",
        f"BASE_URL = {base_url!r}",
        f"PROFILE_SLUG = {profile_slug!r}",
        "",
        "",
    ]

    sig_parts = _py_signature(params)
    desc_safe = description.replace('"""', "'''")

    if sig_parts:
        _sep = ",\n    "
        lines.append(f"async def execute(\n    {_sep.join(sig_parts)},\n) -> dict:")
    else:
        lines.append("async def execute() -> dict:")
    lines.append(f'    """{desc_safe}"""')

    url_expr = f'f"{base_url}{_path_to_fstring(path_template)}"'
    lines.append(f"    url = {url_expr}")

    if query_params:
        q_dict = ", ".join(f"{p['name']!r}: {p['name']}" for p in query_params)
        lines.append(f"    _query = {{{q_dict}}}")
        lines.append("    url = url + ('?' + urllib.parse.urlencode(_query) if _query else '')")

    if has_body:
        body_dict = ", ".join(f"{p['name']!r}: {p['name']}" for p in body_params)
        if "json" in content_type or not content_type:
            lines.append(f"    body = {{{body_dict}}}")
        else:
            lines.append(f"    body = {{{body_dict}}}")

    if static_headers:
        lines.append(f"    headers = {static_headers!r}")
    else:
        lines.append("    headers: dict[str, str] | None = None")

    call_kwargs: list[str] = [
        "PROFILE_SLUG",
        "url",
        f'method="{method}"',
        "headers=headers",
    ]
    if has_body:
        call_kwargs.append("body=body")
    lines.append(f"    return await execute_fetch({', '.join(call_kwargs)})")
    lines.append("")

    return "\n".join(lines)


def _render_operation_http(td: dict, *, auth_plan: dict) -> str:
    """Render an httpx-based operation (legacy, opt-in via --execution-mode http)."""
    name = td["name"]
    method = td["method"].lower()
    path_template = td["path"]
    base_url = td.get("base_url", "")
    content_type = td.get("request_content_type", "")
    params: list[dict] = td.get("params", [])
    request_headers: list[dict] = td.get("request_headers", [])
    description = td.get("description", "")

    needs_auth = bool(
        auth_plan
        and (
            auth_plan.get("required_auth", {}).get("headers")
            or auth_plan.get("required_auth", {}).get("cookies")
            or auth_plan.get("strategy") in ("tabby_credentials", "static_secret_header")
        )
    )

    # Recorded non-auth headers (auth headers were already stripped by har_to_tools)
    static_headers = {
        h["name"]: h["value"] for h in request_headers if h.get("name") and h.get("value")
    }

    lines: list[str] = [
        f'"""Auto-generated operation: {name}',
        f"Method: {method.upper()}",
        f"Path: {path_template}",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import httpx",
        "",
    ]

    if needs_auth:
        lines.append("from noui_runtime.auth import resolve_auth")
        lines.append("")

    lines += [
        f"BASE_URL = {base_url!r}",
        "",
        "",
    ]

    # Build function signature
    sig_parts = _py_signature(params)
    desc_safe = description.replace('"""', "'''")

    if sig_parts:
        _sep = ",\n    "
        lines.append(f"async def execute(\n    {_sep.join(sig_parts)},\n) -> dict:")
    else:
        lines.append("async def execute() -> dict:")
    lines.append(f'    """{desc_safe}"""')

    # Identify param sources
    body_params = [p for p in params if p.get("source") in ("body", None, "")]
    query_params = [p for p in params if p.get("source") == "query"]

    # URL
    url_expr = f'f"{base_url}{_path_to_fstring(path_template)}"'
    lines.append(f"    url = {url_expr}")

    # Body / query
    if body_params and method in ("post", "put", "patch"):
        body_dict = ", ".join(f"{repr(p['name'])}: {p['name']}" for p in body_params)
        if "json" in content_type:
            lines.append(f"    body = {{{body_dict}}}")
        else:
            lines.append(f"    data = {{{body_dict}}}")

    if query_params:
        q_dict = ", ".join(f"{repr(p['name'])}: {p['name']}" for p in query_params)
        lines.append(f"    params = {{{q_dict}}}")

    # Header construction
    if needs_auth:
        if static_headers:
            lines.append(f"    _recorded = {repr(static_headers)}")
            lines.append("    headers = {**_recorded, **await resolve_auth()}")
        else:
            lines.append("    headers = await resolve_auth()")
    elif static_headers:
        lines.append(f"    headers = {repr(static_headers)}")
    else:
        lines.append("    headers = {}")

    # HTTP call
    lines.append("    async with httpx.AsyncClient() as client:")

    call_kwargs: list[str] = ["url", "headers=headers"]
    if query_params:
        call_kwargs.append("params=params")
    if body_params and method in ("post", "put", "patch"):
        if "json" in content_type:
            call_kwargs.append("json=body")
        else:
            call_kwargs.append("data=data")

    call_args = ", ".join(call_kwargs)
    lines.append(f"        resp = await client.{method}({call_args})")
    lines.append("        resp.raise_for_status()")
    lines.append("        try:")
    lines.append("            return resp.json()")
    lines.append("        except Exception:")
    lines.append('            return {"status": resp.status_code, "text": resp.text}')
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Python code helpers
# ---------------------------------------------------------------------------


def _py_signature(params: list[dict]) -> list[str]:
    """Return a list of Python parameter declaration strings."""
    parts: list[str] = []
    required = [p for p in params if p.get("required", True)]
    optional = [p for p in params if not p.get("required", True)]
    for p in required:
        ptype = _py_type(p.get("type", "string"))
        parts.append(f"{p['name']}: {ptype}")
    for p in optional:
        ptype = _py_type(p.get("type", "string"))
        default = _py_default(p.get("type", "string"))
        parts.append(f"{p['name']}: {ptype} = {default}")
    return parts


def _py_type(t: str) -> str:
    return {
        "int": "int",
        "integer": "int",
        "bool": "bool",
        "boolean": "bool",
        "float": "float",
    }.get(t.lower(), "str")


def _py_default(t: str) -> str:
    return {"int": "0", "integer": "0", "bool": "False", "boolean": "False", "float": "0.0"}.get(
        t.lower(), '""'
    )


def _path_to_fstring(path_template: str) -> str:
    """Convert /posts/{id} → /posts/{id} (already valid f-string interpolation)."""
    return path_template


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _slug_to_title(slug: str) -> str:
    """Convert a kebab-case slug to a Title Case name."""
    return " ".join(word.capitalize() for word in re.split(r"[-_]+", slug))
