"""Render an executable CLI operation for a generated Skill.

Each Skill operation is a standalone script invoked from SKILL.md as
`python operations/<name>.py --arg value …`. It exposes:

  - `async def execute(...)`                — the HTTP-calling coroutine
                                              (same shape the MCP output uses)
  - `_build_parser() / main() / __main__`   — argparse wrapper that parses
                                              CLI args, runs execute() under
                                              asyncio.run, and prints the
                                              result as JSON on stdout.

On error: diagnostic on stderr, non-zero exit. On {"error": ...} return
payloads: exits 2 (still prints the JSON). On success: exits 0.
"""

from __future__ import annotations


def render_skill_operation(td: dict, *, auth_plan: dict, execution_mode: str = "tabby") -> str:
    """Render the full Python source for a single Skill operation.

    The rendered file is standalone-runnable: `python operations/<name>.py`
    works from inside the Skill directory, with `noui_runtime/` one level up.

    `execution_mode` matches the MCP compiler:
      - "tabby" (default): execute inside Tabby's browser via the /execute/fetch endpoint
      - "http" (legacy): execute via httpx + resolve_auth()
    """
    if execution_mode == "tabby":
        return _render_skill_operation_tabby(td, auth_plan=auth_plan)
    return _render_skill_operation_http(td, auth_plan=auth_plan)


def _render_skill_operation_tabby(td: dict, *, auth_plan: dict) -> str:
    """Render a skill op that executes inside Tabby's browser via execute/fetch."""
    name = td["name"]
    method = td["method"].upper()
    path_template = td["path"]
    base_url = td.get("base_url", "")
    content_type = td.get("request_content_type", "")
    params: list[dict] = td.get("params", [])
    request_headers: list[dict] = td.get("request_headers", [])
    description = td.get("description", "")

    profile_slug = auth_plan.get("profile_slug", "") if auth_plan else ""

    # A static-secret-header app (Authorization/x-api-key, no login cookies) gets
    # no auth from the browser session's credentials:'include', so the op must
    # inject the secret header itself via resolve_auth() — the same wiring the
    # http mode uses. (tabby_credentials apps ride their session cookies and need
    # no injection here.)
    needs_static_auth = bool(auth_plan and auth_plan.get("strategy") == "static_secret_header")

    static_headers = {
        h["name"]: h["value"] for h in request_headers if h.get("name") and h.get("value")
    }

    body_params = [p for p in params if p.get("source") in ("body", None, "")]
    query_params = [p for p in params if p.get("source") == "query"]
    has_body = bool(body_params) and method in ("POST", "PUT", "PATCH")

    sig_parts = _py_signature(params)
    desc_safe = description.replace('"""', "'''")

    lines: list[str] = [
        "#!/usr/bin/env python3",
        f'"""Auto-generated skill operation: {name}',
        f"Method: {method}",
        f"Path: {path_template}",
        "",
        "Skill-variant entry point. Executes inside Tabby's authenticated browser",
        "via the execute/fetch endpoint. Requires a live Tabby session for the configured profile.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import argparse",
        "import asyncio",
        "import json",
        "import sys",
        "from pathlib import Path",
        "",
    ]
    if query_params:
        lines.append("import urllib.parse")
    lines += [
        "",
        "# Make noui_runtime importable when this file is run as a standalone script",
        "_SKILL_ROOT = Path(__file__).resolve().parent.parent",
        "if str(_SKILL_ROOT) not in sys.path:",
        "    sys.path.insert(0, str(_SKILL_ROOT))",
        "",
        "from noui_runtime.execute import execute_fetch  # noqa: E402",
    ]
    if needs_static_auth:
        lines.append("from noui_runtime.auth import resolve_auth  # noqa: E402")
    lines += [
        "",
        f"BASE_URL = {base_url!r}",
        f"PROFILE_SLUG = {profile_slug!r}",
        "",
        "",
    ]

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
        _ = content_type
        lines.append(f"    body = {{{body_dict}}}")

    if needs_static_auth and static_headers:
        lines.append(f"    _recorded = {static_headers!r}")
        lines.append("    headers = {**_recorded, **await resolve_auth()}")
    elif needs_static_auth:
        lines.append("    headers = await resolve_auth()")
    elif static_headers:
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
    lines.append("")

    lines += _render_cli_wrapper(name, description, params)

    return "\n".join(lines)


def _render_skill_operation_http(td: dict, *, auth_plan: dict) -> str:
    """Render a skill op using httpx + resolve_auth (legacy mode)."""
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

    static_headers = {
        h["name"]: h["value"] for h in request_headers if h.get("name") and h.get("value")
    }

    sig_parts = _py_signature(params)
    desc_safe = description.replace('"""', "'''")

    lines: list[str] = [
        "#!/usr/bin/env python3",
        f'"""Auto-generated skill operation: {name}',
        f"Method: {method.upper()}",
        f"Path: {path_template}",
        "",
        "Skill-variant entry point. Runs from inside the skill directory with",
        "noui_runtime/ as a sibling of operations/. Prints JSON on stdout.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import argparse",
        "import asyncio",
        "import json",
        "import sys",
        "from pathlib import Path",
        "",
        "import httpx",
        "",
        "# Make noui_runtime importable when this file is run as a standalone script",
        "_SKILL_ROOT = Path(__file__).resolve().parent.parent",
        "if str(_SKILL_ROOT) not in sys.path:",
        "    sys.path.insert(0, str(_SKILL_ROOT))",
        "",
    ]

    if needs_auth:
        lines.append("from noui_runtime.auth import resolve_auth  # noqa: E402")
        lines.append("")

    lines += [
        f"BASE_URL = {base_url!r}",
        "",
        "",
    ]

    # execute() coroutine — same shape as MCP operation
    if sig_parts:
        _sep = ",\n    "
        lines.append(f"async def execute(\n    {_sep.join(sig_parts)},\n) -> dict:")
    else:
        lines.append("async def execute() -> dict:")
    lines.append(f'    """{desc_safe}"""')

    body_params = [p for p in params if p.get("source") in ("body", None, "")]
    query_params = [p for p in params if p.get("source") == "query"]

    url_expr = f'f"{base_url}{_path_to_fstring(path_template)}"'
    lines.append(f"    url = {url_expr}")

    if body_params and method in ("post", "put", "patch"):
        body_dict = ", ".join(f"{p['name']!r}: {p['name']}" for p in body_params)
        if "json" in content_type:
            lines.append(f"    body = {{{body_dict}}}")
        else:
            lines.append(f"    data = {{{body_dict}}}")

    if query_params:
        q_dict = ", ".join(f"{p['name']!r}: {p['name']}" for p in query_params)
        lines.append(f"    params = {{{q_dict}}}")

    if needs_auth:
        if static_headers:
            lines.append(f"    _recorded = {static_headers!r}")
            lines.append("    headers = {**_recorded, **await resolve_auth()}")
        else:
            lines.append("    headers = await resolve_auth()")
    elif static_headers:
        lines.append(f"    headers = {static_headers!r}")
    else:
        lines.append("    headers = {}")

    lines.append("    async with httpx.AsyncClient() as client:")
    call_kwargs: list[str] = ["url", "headers=headers"]
    if query_params:
        call_kwargs.append("params=params")
    if body_params and method in ("post", "put", "patch"):
        if "json" in content_type:
            call_kwargs.append("json=body")
        else:
            call_kwargs.append("data=data")
    lines.append(f"        resp = await client.{method}({', '.join(call_kwargs)})")
    lines.append("        resp.raise_for_status()")
    lines.append("        try:")
    lines.append("            return resp.json()")
    lines.append("        except Exception:")
    lines.append('            return {"status": resp.status_code, "text": resp.text}')
    lines.append("")
    lines.append("")

    # argparse wrapper
    lines += _render_cli_wrapper(name, description, params)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI wrapper
# ---------------------------------------------------------------------------


def _render_cli_wrapper(name: str, description: str, params: list[dict]) -> list[str]:
    """Render the argparse parser + main() + __main__ block."""
    prog_description = description.replace('"', "'").splitlines()[0] if description else name

    lines: list[str] = [
        "def _build_parser() -> argparse.ArgumentParser:",
        f"    parser = argparse.ArgumentParser(prog={name!r}, description={prog_description!r})",
    ]

    required = [p for p in params if p.get("required", True)]
    optional = [p for p in params if not p.get("required", True)]

    for p in [*required, *optional]:
        pname = p["name"]
        flag = f"--{pname.replace('_', '-')}"
        ptype = p.get("type", "string").lower()
        help_text = (p.get("description") or "").replace('"', "'") or pname
        req_flag = "required=True" if p.get("required", True) else f"default={_py_default(ptype)}"

        if ptype in ("bool", "boolean"):
            action = "store_true" if not p.get("required", True) else "store_true"
            lines.append(
                f"    parser.add_argument({flag!r}, dest={pname!r}, action={action!r}, "
                f"help={help_text!r})"
            )
        else:
            type_expr = {
                "int": "int",
                "integer": "int",
                "float": "float",
            }.get(ptype, "str")
            if type_expr == "str":
                lines.append(
                    f"    parser.add_argument({flag!r}, dest={pname!r}, {req_flag}, "
                    f"help={help_text!r})"
                )
            else:
                lines.append(
                    f"    parser.add_argument({flag!r}, dest={pname!r}, type={type_expr}, "
                    f"{req_flag}, help={help_text!r})"
                )

    lines += [
        "    return parser",
        "",
        "",
        "def main(argv: list[str] | None = None) -> int:",
        "    args = _build_parser().parse_args(argv)",
        "    try:",
    ]

    if params:
        kwargs = ", ".join(f"{p['name']}=args.{p['name']}" for p in params)
        lines.append(f"        result = asyncio.run(execute({kwargs}))")
    else:
        lines.append("        result = asyncio.run(execute())")

    lines += [
        "    except Exception as exc:",
        f'        print(f"{name} failed: {{exc}}", file=sys.stderr)',
        "        return 1",
        "    print(json.dumps(result, indent=2))",
        '    if isinstance(result, dict) and "error" in result:',
        "        return 2",
        "    return 0",
        "",
        "",
        'if __name__ == "__main__":',
        "    sys.exit(main())",
        "",
    ]

    return lines


# ---------------------------------------------------------------------------
# Private code-generation helpers (duplicated from noui_core.compile.server_generator
# to avoid a cross-compiler private import; small enough to keep in sync by hand)
# ---------------------------------------------------------------------------


def _py_signature(params: list[dict]) -> list[str]:
    parts: list[str] = []
    required = [p for p in params if p.get("required", True)]
    optional = [p for p in params if not p.get("required", True)]
    for p in required:
        parts.append(f"{p['name']}: {_py_type(p.get('type', 'string'))}")
    for p in optional:
        ptype = p.get("type", "string")
        parts.append(f"{p['name']}: {_py_type(ptype)} = {_py_default(ptype)}")
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
    return {
        "int": "0",
        "integer": "0",
        "bool": "False",
        "boolean": "False",
        "float": "0.0",
    }.get(t.lower(), '""')


def _path_to_fstring(path_template: str) -> str:
    """Convert /posts/{id} → /posts/{id} (already valid f-string interpolation)."""
    return path_template
