"""Render SKILL.md for a generated skill.

SKILL.md has two jobs:

1. The `description` field in the YAML frontmatter decides whether the skill
   is ever loaded (Claude indexes skills by description and only loads when
   intent matches). A bad description means the skill is invisible.
2. The body instructs Claude how to actually invoke the skill's operations
   once loaded.

The description is synthesized from the recorded workflow — session name,
site hostname, and tool names — and can be overridden at generation time
via `description_override`. Skills are generated and iterated on by agents,
so we trust the heuristic and let users regenerate if the description
proves unreliable.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


def render_skill_md(
    *,
    skill_id: str,
    app_name: str,
    app_slug: str,
    workflow_name: str,
    tool_defs: list[dict],
    auth_plan: dict,
    profile_slug: str,
    description_override: str = "",
    python_executable: str = ".venv/bin/python",
    existing: str | None = None,
) -> str:
    """Render full SKILL.md source (frontmatter + body).

    Args:
        python_executable: Path to the Python interpreter used in the rendered
            command examples. Relative paths are resolved against the skill's
            root at runtime. Defaults to the per-skill venv layout emitted by
            D1/D2 (`.venv/bin/python`).
        existing: If provided, custom-fenced regions from this string are
            preserved in the output via `merge_custom_sections`.
    """
    description = description_override or _synthesize_description(
        app_name=app_name,
        workflow_name=workflow_name,
        tool_defs=tool_defs,
        profile_slug=profile_slug,
        requires_auth=bool(auth_plan),
    )

    body = _render_body(
        skill_id=skill_id,
        app_name=app_name,
        app_slug=app_slug,
        tool_defs=tool_defs,
        auth_plan=auth_plan,
        profile_slug=profile_slug,
        python_executable=python_executable,
    )

    # Wrap description in YAML block-scalar form if it contains characters that
    # would break a single-line YAML string (quotes, colons inside, etc.).
    # Simpler: escape any embedded double quotes and wrap in >- for folded block.
    frontmatter = f"""---
name: {skill_id}
description: {_escape_yaml_scalar(description)}
---
"""

    rendered = frontmatter + "\n" + body
    if existing:
        from noui_core.compile.markdown_sections import merge_custom_sections

        rendered = merge_custom_sections(existing, rendered)
    return rendered


# ---------------------------------------------------------------------------
# Description synthesis
# ---------------------------------------------------------------------------


def _synthesize_description(
    *,
    app_name: str,
    workflow_name: str,
    tool_defs: list[dict],
    profile_slug: str,
    requires_auth: bool,
) -> str:
    """Draft a description + trigger phrases from the recording metadata."""
    actions = [_tool_to_action(td) for td in tool_defs if _tool_to_action(td)]
    actions = _dedupe(actions)[:3] or [workflow_name.lower()]
    action_phrase = _join_natural(actions)

    hostname = _primary_hostname(tool_defs) or app_name
    triggers = _dedupe(
        [
            f"{actions[0]} on {app_name.lower()}" if actions else app_name.lower(),
            workflow_name.lower(),
            f"{app_name.lower()}",
            *[f"{a}" for a in actions[:2]],
        ]
    )[:4]
    trigger_str = ", ".join(f'"{t}"' for t in triggers)

    auth_caveat = (
        f" Requires an authenticated Tabby session for the `{profile_slug or app_name.lower()}` "
        f"profile; not usable as a generic {hostname} tool."
        if requires_auth
        else ""
    )

    return (
        f"Use this skill when the user wants to {action_phrase} on {app_name}. "
        f"Triggers on {trigger_str}, or any request that implies a {workflow_name.lower()} task."
        f"{auth_caveat}"
    )


def _tool_to_action(td: dict) -> str:
    """Convert a tool name like `search_hotels` into an action phrase like 'search hotels'."""
    name = td.get("name", "")
    if not name:
        return ""
    # Drop common CRUD prefixes that carry no user intent.
    bare = re.sub(r"^(create_|get_|list_|update_|delete_)", "", name)
    bare = bare.replace("_", " ")
    bare = re.sub(r"\s+", " ", bare).strip()
    return bare


def _primary_hostname(tool_defs: list[dict]) -> str:
    for td in tool_defs:
        base_url = td.get("base_url", "")
        if base_url:
            host = urlparse(base_url).hostname or ""
            if host:
                return host[4:] if host.startswith("www.") else host
    return ""


def _join_natural(items: list[str]) -> str:
    if not items:
        return "perform the workflow"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + f", or {items[-1]}"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def _escape_yaml_scalar(s: str) -> str:
    """Emit a safe single-line YAML scalar for the description field.

    Strategy: if the string has no characters that would break a plain scalar
    (no leading special chars, no embedded colons followed by space, no
    quotes), return it verbatim. Otherwise wrap in double quotes and escape
    embedded double quotes / backslashes.
    """
    s = s.strip().replace("\n", " ")
    if (
        any(c in s for c in ('"', "\\", "\t"))
        or ": " in s
        or s.startswith(("-", "?", ":", "[", "{", "!"))
    ):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------


def _render_body(
    *,
    skill_id: str,
    app_name: str,
    app_slug: str,  # noqa: ARG001 – kept for signature stability
    tool_defs: list[dict],
    auth_plan: dict,
    profile_slug: str,
    python_executable: str = ".venv/bin/python",
) -> str:
    sections: list[str] = []

    sections.append(f"# {app_name}")
    sections.append("")

    intro = (
        f"Auto-generated skill for {app_name}. "
        f"Each operation under `operations/` is a standalone CLI script that "
        f"prints a JSON response to stdout."
    )
    sections.append(intro)
    sections.append("")

    # Environment setup
    sections.append("## Environment")
    sections.append("")
    sections.append(
        f"Operations run under `{python_executable}` — a virtualenv that lives **inside this "
        f"skill's directory**, sibling to `manifest.json`. Provision it once after install by "
        f"running the commands below **from this skill's own folder** (not from the workspace "
        f"root), so `uv sync` / `python -m venv` create `.venv/` in the right place:"
    )
    sections.append("")
    sections.append("```bash")
    sections.append("cd path/to/this/skill                # wherever manifest.json lives")
    sections.append("uv sync                              # preferred — lands .venv/ in this dir")
    sections.append("# — or —")
    sections.append("python -m venv .venv && .venv/bin/pip install -e .")
    sections.append("```")
    sections.append("")
    sections.append(
        f"After provisioning, the examples below use `{python_executable}` — a path that "
        f"resolves relative to this skill directory when Claude Code invokes the operation. "
        f"To use a different interpreter, edit `runtime.python_executable` in `manifest.json` "
        f"and re-run `noui skill docs {skill_id}` to refresh the examples."
    )
    sections.append("")
    sections.append(
        f"*Tip: `noui skill install {skill_id} <agent> --project --with-env auto` "
        f"provisions the venv for you at install time.*"
    )
    sections.append("")

    # Prerequisites
    sections.append("## Prerequisites")
    sections.append("")
    if auth_plan:
        slug = profile_slug or app_slug or "<profile_slug>"
        sections.append(f"1. **Tabby is running with the `{slug}` profile.** Verify with:")
        sections.append("   ```bash")
        sections.append(f"   .venv/bin/python cli/main.py tabby session ensure --profile {slug}")
        sections.append("   ```")
        sections.append(
            f"   If that command fails, re-run `/noui-record-login` for the `{slug}` profile."
        )
        sections.append(
            "2. **`TABBY_CLIENT_ID` / `TABBY_CLIENT_SECRET`** available in noui/.env, "
            "~/.config/noui/.env, or exported in the shell — the runtime uses them to exchange "
            "an agent token."
        )
        sections.append(
            f"3. **The `{slug}` profile is ACTIVE.** A STAGING profile returns empty "
            f"credentials and the operation fails with "
            f"\"Tabby returned empty credentials for profile '{slug}'\"."
        )
    else:
        sections.append(
            "No authentication required — the recorded workflow hit public APIs. "
            "Each operation calls directly via httpx with no Tabby session."
        )
    sections.append("")
    sections.append("<!-- custom:start:prerequisites -->")
    sections.append(
        "<!-- Add skill-specific prereqs here; this region survives `noui skill docs`. -->"
    )
    sections.append("<!-- custom:end:prerequisites -->")
    sections.append("")

    # Operations
    sections.append("## Operations")
    sections.append("")
    sections.append(
        "> **Generalize this list before relying on it.** These operations are captured "
        "verbatim from a single recording, so alongside the real workflow API they may "
        "include incidental requests the page happened to fire — third-party / cross-domain "
        "calls (analytics, maps, ad & tracking pixels, CDN or static assets) and telemetry "
        "beacons (e.g. `gen_204`, `/tr`, `get-data-layer-variables`, feature-flag fetches). "
        "During the generalization phase (`/noui-generalize`), review each operation and "
        "**prune the ones that aren't part of the intended task**. Do NOT blanket-drop by "
        "domain: a workflow can legitimately span multiple hosts (e.g. an auth domain plus "
        "an API domain), so keep cross-domain operations that are actually used. Aim for the "
        "smallest set of operations that performs the workflow."
    )
    sections.append("")
    for td in tool_defs:
        sections.extend(_render_operation_section(td, python_executable=python_executable))

    # Troubleshooting
    sections.append("## Troubleshooting")
    sections.append("")
    if auth_plan:
        sections.append(
            "- **`Tabby returned empty credentials for profile '<slug>'`** — the profile is "
            "STAGING, not ACTIVE; or the Tabby session worker is not running. "
            "Run `tabby session ensure --profile <slug>`."
        )
        sections.append(
            "- **`Missing TABBY_CLIENT_ID or TABBY_CLIENT_SECRET`** — set these in "
            "`noui/.env` or `~/.config/noui/.env`, or run `noui tabby setup`."
        )
    sections.append(
        "- **HTTP 401 / 403** — the Tabby session expired or the profile's credentials "
        "became invalid; re-run `tabby session ensure --profile <slug>` and retry."
    )
    sections.append(
        "- **HTTP 429 / Akamai / bot detection** — the site is blocking the Python HTTP "
        "client. Regenerate the skill after running `/noui-generalize` to rewrite affected "
        "operations to use CDP browser-side `fetch()`."
    )
    sections.append("")
    sections.append("<!-- custom:start:troubleshooting -->")
    sections.append(
        "<!-- Add hand-written troubleshooting notes here; this region survives `noui skill docs`. -->"
    )
    sections.append("<!-- custom:end:troubleshooting -->")
    sections.append("")

    # Notes
    sections.append("## Notes")
    sections.append("")
    sections.append(
        f"- Generated by the NoUI skill compiler. Skill id: `{skill_id}`. "
        f"Re-export with `noui workflow export --as skill` to regenerate after changes, "
        f"or `noui skill docs {skill_id}` to regenerate the docs alone while preserving "
        f"custom-fenced regions."
    )
    sections.append(
        "- Operations are standalone Python scripts — you can run them directly without "
        "loading the skill in Claude (useful for debugging)."
    )
    sections.append("")
    sections.append("<!-- custom:start:notes -->")
    sections.append("<!-- Additional notes survive `noui skill docs`. -->")
    sections.append("<!-- custom:end:notes -->")
    sections.append("")

    return "\n".join(sections)


def _render_operation_section(
    td: dict, *, python_executable: str = ".venv/bin/python"
) -> list[str]:
    name = td["name"]
    description = td.get("description", name)
    params: list[dict] = td.get("params", [])

    lines: list[str] = []
    lines.append(f"### `{name}`")
    lines.append("")
    lines.append(description)
    lines.append("")
    lines.append("**Command:**")
    lines.append("")
    lines.append("```bash")
    example_parts = [f"{python_executable} operations/{name}.py"]
    for p in params:
        flag = f"--{p['name'].replace('_', '-')}"
        example_parts.append(f"  {flag} {_example_value(p)}")
    if len(example_parts) > 1:
        lines.append(" \\\n".join(example_parts))
    else:
        lines.append(example_parts[0])
    lines.append("```")
    lines.append("")

    if params:
        lines.append("**Arguments:**")
        lines.append("")
        lines.append("| Name | Type | Required | Default | Description |")
        lines.append("|---|---|---|---|---|")
        for p in params:
            ptype = p.get("type", "string")
            required = "yes" if p.get("required", True) else "no"
            default = "—" if p.get("required", True) else _py_default_display(ptype)
            pdesc = (p.get("description") or "").replace("|", "\\|") or p["name"]
            lines.append(
                f"| `--{p['name'].replace('_', '-')}` | {ptype} | {required} | {default} | {pdesc} |"
            )
        lines.append("")

    return lines


def _example_value(p: dict) -> str:
    ptype = p.get("type", "string").lower()
    if ptype in ("int", "integer"):
        return "1"
    if ptype == "float":
        return "1.0"
    if ptype in ("bool", "boolean"):
        return ""
    return f"<{p['name']}>"


def _py_default_display(t: str) -> str:
    return {
        "int": "`0`",
        "integer": "`0`",
        "bool": "`False`",
        "boolean": "`False`",
        "float": "`0.0`",
    }.get(t.lower(), '`""`')
