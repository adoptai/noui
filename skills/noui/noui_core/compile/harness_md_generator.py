"""Render SKILL.md + operation recipes for execution_mode="harness".

Skills targeting the Adopt Agent Harness cannot ship transport code: the
harness sandbox has no env vars, no Tabby credentials, and code running
inside it must never call Tabby directly. Authenticated requests are made
by the *agent* (the LLM) through the harness-side `call_web_api` tool,
which mints a per-member Tabby token worker-side and routes the request
through the member's authenticated browser session.

So instead of `operations/*.py` scripts, a harness skill carries:

  - SKILL.md operation cards — per-operation `call_web_api` invocations the
    agent copies (or `bash` curl commands for unauthenticated endpoints).
  - operations.json — the same request recipes, machine-readable.

The decision rule mirrors the integration plan: a workflow bound to a Tabby
profile renders `call_web_api` cards; an unauthenticated workflow renders
sandbox `curl` cards (the sandbox has open egress), with a note to rebind a
profile and re-export if the site turns out to be bot-protected.
"""

from __future__ import annotations

import json
import re

from noui_core.compile.skill_md_generator import (
    _escape_yaml_scalar,
    _synthesize_description,
)

# Mirrors the harness default (AGENT_HARNESS_TABBY_RESULT_CAP_CHARS); only
# used in doc text, never enforced here.
_RESULT_CAP_CHARS_DEFAULT = 20_000

# Headers the executing browser manages itself. call_web_api runs the request
# as fetch() inside a real browser page, so recorded fingerprint/transport
# headers are at best noise and at worst conflict with the live session.
_BROWSER_MANAGED_HEADER_PREFIXES = ("sec-ch-", "sec-fetch-")
_BROWSER_MANAGED_HEADERS = frozenset(
    {
        "user-agent",
        "referer",
        "origin",
        "host",
        "cookie",
        "content-length",
        "accept-encoding",
        "accept-language",
        "connection",
        "priority",
        "pragma",
        "cache-control",
        "upgrade-insecure-requests",
    }
)


def _is_browser_managed(header_name: str) -> bool:
    lowered = header_name.lower()
    return lowered in _BROWSER_MANAGED_HEADERS or lowered.startswith(
        _BROWSER_MANAGED_HEADER_PREFIXES
    )


def _secret_placeholder_headers(auth_plan: dict | None) -> dict[str, str]:
    """For a static_secret_header workflow, emit placeholder headers the
    harness resolves server-side (gap G1).

    The recorded API key is never emitted — only a ``${SECRET:name}`` token.
    The harness substitutes the real value from its secret store just before
    the request leaves for Tabby, so the key never enters the model context.
    Maps the auth_plan fallback's ``value_template`` (e.g.
    ``"Bearer ${ADOPT_BANK_API_KEY}"``) to ``"Bearer ${SECRET:adopt_bank_api_key}"``.
    """
    if not auth_plan or auth_plan.get("strategy") != "static_secret_header":
        return {}
    out: dict[str, str] = {}
    for fb in auth_plan.get("fallbacks", []):
        if fb.get("type") != "static_secret_header":
            continue
        header = fb.get("header")
        if not header:
            continue
        env_var = fb.get("secret_env_var", "") or header.upper().replace("-", "_")
        name = env_var.lower()
        template = fb.get("value_template") or ""
        if template and env_var and ("${" + env_var + "}") in template:
            out[header] = template.replace("${" + env_var + "}", "${SECRET:" + name + "}")
        else:
            out[header] = "${SECRET:" + name + "}"
    return out


_SECRET_NAME_RE = re.compile(r"\$\{SECRET:([A-Za-z0-9_.\-]+)\}")


def secret_names(auth_plan: dict | None) -> list[str]:
    """Names of the ${SECRET:name} placeholders this skill expects configured."""
    names: list[str] = []
    for value in _secret_placeholder_headers(auth_plan).values():
        for name in _SECRET_NAME_RE.findall(value):
            if name not in names:
                names.append(name)
    return names


def build_operation_recipe(td: dict, *, profile_slug: str, auth_plan: dict | None = None) -> dict:
    """Build the machine-readable request recipe for one recorded operation."""
    method = td["method"].upper()
    base_url = td.get("base_url", "")
    params: list[dict] = td.get("params", [])
    request_headers: list[dict] = td.get("request_headers", [])

    static_headers = {
        h["name"]: h["value"]
        for h in request_headers
        if h.get("name") and h.get("value") and not _is_browser_managed(h["name"])
    }
    # G1: inject server-resolved secret placeholders (e.g. Authorization).
    static_headers.update(_secret_placeholder_headers(auth_plan))

    def _params_in(source: str) -> list[dict]:
        if source == "body":
            matched = [p for p in params if p.get("source") in ("body", None, "")]
        else:
            matched = [p for p in params if p.get("source") == source]
        return [
            {
                "name": p["name"],
                "type": p.get("type", "string"),
                "required": bool(p.get("required", True)),
                **({"description": p["description"]} if p.get("description") else {}),
            }
            for p in matched
        ]

    recipe: dict = {
        "name": td["name"],
        "description": td.get("description", td["name"]),
        "tool": "call_web_api" if profile_slug else "bash",
        "method": method,
        "url_template": f"{base_url}{td['path']}",
        "path_params": _params_in("path"),
        "query_params": _params_in("query"),
        "body_params": _params_in("body") if method in ("POST", "PUT", "PATCH") else [],
    }
    if profile_slug:
        recipe["app"] = profile_slug
        # Explicit per-op auth binding: the harness call_web_api broker resolves
        # the signed-in user's federated Tabby bearer (per-user, owner_user_id =
        # the real user) for this profile. No secret travels with the recipe.
        recipe["auth"] = {"type": "tabby_per_user", "profile": profile_slug}
    if static_headers:
        recipe["headers"] = static_headers
    if td.get("request_content_type"):
        recipe["content_type"] = td["request_content_type"]
    return recipe


def render_operations_json(
    tool_defs: list[dict], *, profile_slug: str, auth_plan: dict | None = None
) -> str:
    """Render operations.json — every operation's recipe, machine-readable."""
    recipes = [
        build_operation_recipe(td, profile_slug=profile_slug, auth_plan=auth_plan)
        for td in tool_defs
    ]
    return json.dumps({"schema_version": "1", "operations": recipes}, indent=2, ensure_ascii=False)


def render_harness_skill_md(
    *,
    skill_id: str,
    app_name: str,
    workflow_name: str,
    tool_defs: list[dict],
    auth_plan: dict,
    profile_slug: str,
    description_override: str = "",
    existing: str | None = None,
) -> str:
    """Render SKILL.md for a harness-targeted skill (frontmatter + body)."""
    description = description_override or _synthesize_description(
        app_name=app_name,
        workflow_name=workflow_name,
        tool_defs=tool_defs,
        profile_slug=profile_slug,
        requires_auth=bool(auth_plan),
    )

    frontmatter = f"""---
name: {skill_id}
description: {_escape_yaml_scalar(description)}
---
"""
    body = _render_body(
        skill_id=skill_id,
        app_name=app_name,
        tool_defs=tool_defs,
        auth_plan=auth_plan,
        profile_slug=profile_slug,
    )

    rendered = frontmatter + "\n" + body
    if existing:
        from noui_core.compile.markdown_sections import merge_custom_sections

        rendered = merge_custom_sections(existing, rendered)
    return rendered


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------


def _render_body(
    *,
    skill_id: str,
    app_name: str,
    tool_defs: list[dict],
    auth_plan: dict,
    profile_slug: str,
) -> str:
    authed = bool(profile_slug)
    secrets = secret_names(auth_plan)
    sections: list[str] = []

    sections.append(f"# {app_name}")
    sections.append("")
    if authed:
        sections.append(
            f"Skill for {app_name}, targeting the **Adopt Agent Harness**. Operations are "
            f"executed by calling the harness `call_web_api` tool — there are no scripts to "
            f"run and nothing to install. `call_web_api` routes each request through the "
            f"signed-in member's Tabby browser session; authentication, token minting, and "
            f"login recovery are handled by the harness, never by this skill."
        )
    else:
        sections.append(
            f"Skill for {app_name}, targeting the **Adopt Agent Harness**. The recorded "
            f"endpoints are unauthenticated, so operations run as plain `curl` commands via "
            f"the `bash` tool (the sandbox has open network egress). Nothing to install."
        )
    sections.append("")

    # Prerequisites
    sections.append("## Prerequisites")
    sections.append("")
    if authed:
        sections.append(
            f"1. The Tabby profile **`{profile_slug}`** is **ACTIVE** and listed in the "
            f"harness agent client's `allowed_profiles` (a `forbidden` result means it is "
            f"not — ask an admin to add it)."
        )
        sections.append(
            "2. No env vars, no venv, no credentials in the skill: the harness mints a "
            "per-member Tabby token for every call. If the member has no live session yet, "
            "`call_web_api` returns `login_required` with a login link — show it to the "
            "user, then retry the same call with `wait_for_login: true`."
        )
        if secrets:
            names = ", ".join(f"`{n}`" for n in secrets)
            sections.append(
                f"3. This API needs a static secret (an API key). The operation cards below "
                f"carry it as a `${{SECRET:name}}` placeholder — the harness substitutes the "
                f"real value server-side, so **pass the placeholder verbatim and never a real "
                f"key**. An admin must configure the secret(s) {names} in the harness secret "
                f"store (`AGENT_HARNESS_WEB_API_SECRETS`); an unconfigured secret returns an "
                f"actionable error naming it."
            )
    else:
        sections.append(
            "None — the recorded endpoints are public. If calls start failing with 429s or "
            "bot-detection challenges, the site needs a real browser: re-export this skill "
            "with a Tabby profile bound so operations route through `call_web_api` instead."
        )
    sections.append("")
    sections.append("<!-- custom:start:prerequisites -->")
    sections.append(
        "<!-- Add skill-specific prereqs here; this region survives `noui skill docs`. -->"
    )
    sections.append("<!-- custom:end:prerequisites -->")
    sections.append("")

    # How to run
    sections.append("## Running operations")
    sections.append("")
    if authed:
        sections.append(
            "For each operation below, call the `call_web_api` tool with the shown payload, "
            "substituting `<param>` placeholders (and passing any `${SECRET:...}` headers "
            "verbatim). Text/JSON responses come back inline, truncated past "
            f"~{_RESULT_CAP_CHARS_DEFAULT:,} characters — prefer narrow or paginated queries "
            "over one huge fetch."
        )
        sections.append("")
        sections.append(
            "Binary or large responses (e.g. a PDF download) are written into the sandbox "
            'instead of returned inline: the result is `{status: "saved_to_sandbox", path, '
            "bytes, content_type}`. Read or process that path with the `bash` tool. To "
            "post-process an inline JSON response, save it to `/workspace/<op>.json` with a "
            "bash heredoc and shape it with Python."
        )
    else:
        sections.append(
            "Run each operation with the `bash` tool, substituting `<param>` placeholders. "
            "Responses print to stdout as JSON."
        )
    sections.append("")
    sections.append("The same recipes are machine-readable in `operations.json`.")
    sections.append("")

    # Operations
    sections.append("## Operations")
    sections.append("")
    for td in tool_defs:
        sections.extend(_render_operation_card(td, profile_slug=profile_slug, auth_plan=auth_plan))

    # Troubleshooting
    sections.append("## Troubleshooting")
    sections.append("")
    if authed:
        sections.append(
            "- **`forbidden`** — the profile is not in the harness agent client's "
            "`allowed_profiles`, or is not ACTIVE. An admin must fix the profile; this is "
            "not retryable from the conversation."
        )
        sections.append(
            "- **`login_required`** — expected on a member's first use. Show the returned "
            "login link to the user, then retry the identical call with "
            "`wait_for_login: true`."
        )
        sections.append(
            "- **`login_timeout`** — the user didn't finish logging in within the window. "
            "Ask them to complete the login and retry."
        )
        sections.append(
            "- **Truncated response** — the result hit the harness text cap. Narrow the "
            "query (filters, pagination params) instead of re-fetching the same URL."
        )
        sections.append(
            "- **502 / `Failed to fetch`** — usually a page-origin (CORS) issue: the "
            "profile's browser session is parked on a different origin than the API. The "
            "profile's login flow must end on the target site's origin; re-record the "
            "login if needed."
        )
    else:
        sections.append(
            "- **HTTP 429 / bot detection / Akamai challenges** — the site blocks plain "
            "HTTP clients. Re-export this skill with a Tabby profile bound so operations "
            "route through `call_web_api` (a real browser) instead of curl."
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
        f'- Generated by the NoUI skill compiler (`execution_mode="harness"`). Skill id: '
        f"`{skill_id}`. Re-export with `noui workflow export --as skill "
        f"--execution-mode harness` to regenerate."
    )
    sections.append(
        "- This skill intentionally ships no transport code: the harness sandbox must "
        "never hold Tabby credentials. Do not add scripts that call Tabby directly."
    )
    sections.append("")
    sections.append("<!-- custom:start:notes -->")
    sections.append("<!-- Additional notes survive `noui skill docs`. -->")
    sections.append("<!-- custom:end:notes -->")
    sections.append("")

    return "\n".join(sections)


def _render_operation_card(
    td: dict, *, profile_slug: str, auth_plan: dict | None = None
) -> list[str]:
    recipe = build_operation_recipe(td, profile_slug=profile_slug, auth_plan=auth_plan)
    params: list[dict] = td.get("params", [])

    lines: list[str] = []
    lines.append(f"### `{recipe['name']}`")
    lines.append("")
    lines.append(recipe["description"])
    lines.append("")

    if profile_slug:
        lines.append("**`call_web_api` invocation:**")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(_example_invocation(recipe), indent=2, ensure_ascii=False))
        lines.append("```")
    else:
        lines.append("**Command (`bash` tool):**")
        lines.append("")
        lines.append("```bash")
        lines.append(f"curl -sS '{_example_url(recipe)}'" + _curl_extras(recipe))
        lines.append("```")
    lines.append("")

    if params:
        lines.append("**Parameters:**")
        lines.append("")
        lines.append("| Name | In | Type | Required | Description |")
        lines.append("|---|---|---|---|---|")
        for group, label in (
            (recipe["path_params"], "path"),
            (recipe["query_params"], "query"),
            (recipe["body_params"], "body"),
        ):
            for p in group:
                pdesc = (p.get("description") or p["name"]).replace("|", "\\|")
                required = "yes" if p["required"] else "no"
                lines.append(f"| `{p['name']}` | {label} | {p['type']} | {required} | {pdesc} |")
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Example rendering helpers
# ---------------------------------------------------------------------------


def _example_url(recipe: dict) -> str:
    url = recipe["url_template"]
    for p in recipe["path_params"]:
        url = url.replace("{" + p["name"] + "}", f"<{p['name']}>")
    if recipe["query_params"]:
        q = "&".join(f"{p['name']}=<{p['name']}>" for p in recipe["query_params"])
        url = f"{url}?{q}"
    return url


def _example_invocation(recipe: dict) -> dict:
    invocation: dict = {
        "app": recipe.get("app", ""),
        "url": _example_url(recipe),
        "method": recipe["method"],
    }
    if recipe.get("headers"):
        invocation["headers"] = recipe["headers"]
    if recipe["body_params"]:
        invocation["body"] = json.dumps(
            {p["name"]: f"<{p['name']}>" for p in recipe["body_params"]}
        )
    return invocation


def _curl_extras(recipe: dict) -> str:
    parts = ""
    if recipe["method"] != "GET":
        parts += f" \\\n  -X {recipe['method']}"
    for name, value in (recipe.get("headers") or {}).items():
        parts += f" \\\n  -H '{name}: {value}'"
    if recipe["body_params"]:
        body = json.dumps({p["name"]: f"<{p['name']}>" for p in recipe["body_params"]})
        parts += f" \\\n  -H 'Content-Type: application/json' \\\n  -d '{body}'"
    return parts
