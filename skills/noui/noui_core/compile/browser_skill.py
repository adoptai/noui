"""Browser-driven skill compilation.

A HAR-replay skill (the default) fails for apps that mint per-request encryption
or per-session headers in the page's JavaScript: the recorded request bodies are
opaque `{data, key}` blobs that only the live page can produce, and the recorded
`session`/`req-id` headers are dead the moment the recording ends. ICICI is the
canonical case — 40 replayed operations that all 403.

For those apps the workable path is to drive the PAGE: navigate to where the
data renders, then read the DOM the page already fetched and decrypted. Tabby
exposes this via `/execute/browser`, and the harness surfaces it to the model as
the `call_web_browser` tool. This module compiles a recording into a skill that
uses that tool instead of `call_web_api`.

The "operations" of a browser skill are the readable data PAGES observed in the
recording — each compiled as: navigate to the page, then `get_page_summary`.
There is no request replay, so no encryption or frozen-header problem.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from noui_core.compile.login_assets import (
    _LOGIN_FLOW_SEGMENTS,
    _first_path_segment,
    _has_volatile_query,
    _url_origin,
)


def _slug_from_path(url: str) -> str:
    """A stable, readable operation-name stem from a URL's path.

    /credit-card -> credit_card ; /accounts/summary -> accounts_summary ;
    the bare origin -> home.
    """
    path = urlparse(url).path.strip("/")
    if not path:
        return "home"
    slug = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")
    return slug or "home"


def derive_browser_pages(
    url_events: list[dict],
    *,
    login_url: str,
    max_pages: int = 12,
) -> list[dict]:
    """Pick the readable data pages from a recording's URL history.

    Keeps distinct pages on the app's own origin, in first-seen order, dropping:
      - the login/auth flow pages (a browser skill reads DATA, not the login UI;
        the login itself is handled by the Tabby session before any command);
      - pages whose query string is volatile (a one-shot token that will not
        replay — navigating there later lands on an error, exactly the Finacle
        keepalive trap documented in login_assets).

    Returns [{name, url, title_hint}] — the operations the skill will expose.
    Empty list means the recording never left the login flow, which the caller
    must treat as "nothing to compile" rather than emit a skill with no reads.
    """
    app_origin = _url_origin(login_url) if login_url else ""
    seen: set[str] = set()
    pages: list[dict] = []
    for ev in url_events:
        url = (ev or {}).get("to_url") or ""
        if not url or not url.startswith(("http://", "https://")):
            continue
        # Same-origin as the login page only. A workflow can legitimately span
        # hosts, but the readable-data host is the app's own; third-party widget
        # and telemetry origins (the DevRev/Dynatrace noise that poisoned the
        # HAR-replay skill's identity) are exactly what we must not treat as
        # pages to read.
        if app_origin and _url_origin(url) != app_origin:
            continue
        seg = _first_path_segment(url)
        # Match login-flow routes even when the segment carries a suffix, e.g.
        # "login-page" / "signin_v2": split on non-alphanumerics and reject if
        # any leading token is a login word. A bare `seg in set` misses these,
        # and ICICI's post-login pages hang off /login-page's sibling routes.
        seg_tokens = [t for t in re.split(r"[^a-z0-9]+", seg) if t]
        if seg in _LOGIN_FLOW_SEGMENTS or any(
            t in _LOGIN_FLOW_SEGMENTS for t in seg_tokens
        ):
            continue
        if _has_volatile_query(url):
            continue
        # Dedupe on origin+path (ignore query): the same page with different
        # query params is one readable page.
        key = _url_origin(url) + urlparse(url).path.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        pages.append({"name": f"read_{_slug_from_path(url)}", "url": url})
        if len(pages) >= max_pages:
            break
    return pages


def render_browser_operations_json(pages: list[dict], *, profile_slug: str) -> str:
    """operations.json for a browser skill — one navigate+read recipe per page.

    Shape mirrors the harness call_web_api operations.json (schema_version +
    operations[]) so the installer and manifest routing treat it identically;
    only the tool and step shape differ.
    """
    operations = []
    for p in pages:
        operations.append({
            "name": p["name"],
            "description": f"Read the rendered contents of {p['url']}",
            "tool": "call_web_browser",
            "profile_slug": profile_slug,
            # Two-step recipe: go to the page, then read what it rendered.
            "steps": [
                {"command": "navigate", "params": {"url": p["url"]}},
                {"command": "get_page_summary"},
            ],
        })
    return json.dumps(
        {"schema_version": "1", "style": "browser", "operations": operations},
        indent=2,
        ensure_ascii=False,
    )


def render_browser_skill_md(
    *,
    skill_id: str,
    app_name: str,
    workflow_name: str,
    pages: list[dict],
    profile_slug: str,
    description_override: str = "",
) -> str:
    """SKILL.md for a browser-driven skill (harness frontmatter + body)."""
    description = description_override or (
        f"Use this skill to read {workflow_name.lower()} data from {app_name} by "
        f"driving the signed-in browser session (Tabby profile `{profile_slug}`). "
        f"This app renders its data in a single-page app whose requests cannot be "
        f"replayed, so the skill reads what the page displays via the "
        f"`call_web_browser` tool rather than calling APIs directly."
    )

    page_lines = "\n".join(
        f"- **{p['name']}** — `{p['url']}`" for p in pages
    ) or "- (no data pages were captured; re-record reaching the target screen)"

    frontmatter = (
        "---\n"
        f"name: {skill_id}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "auth: browser\n"
        f"profile: {profile_slug}\n"
        "---\n"
    )

    body = f"""
# {app_name}

Browser-driven skill for **{app_name}**, targeting the **Adopt Agent Harness**.

This app cannot be automated by replaying HTTP requests — it encrypts request
bodies (or mints per-session headers) in the page's JavaScript, so a recorded
request is not reusable. Instead, this skill drives the live page with the
`call_web_browser` tool and reads what it renders.

## Prerequisites

An authenticated Tabby session for the **`{profile_slug}`** profile. If the
member has no live session, `call_web_browser` returns `login_required` with a
sign-in link — the harness shows the card automatically; ask the user to sign
in, then retry the SAME call with `wait_for_login: true`.

Many portals idle out within a few minutes, so read the data promptly after the
user signs in rather than exploring first.

## Reading data

Each readable page below is a two-step recipe: navigate to it, then read it.

1. `call_web_browser` with `command: "navigate"`, `params: {{ "url": "<page url>" }}`
2. `call_web_browser` with `command: "get_page_summary"` — returns the page's
   headings, links, buttons and inputs (the rendered account/card/transaction
   values live in `headings`).

If a value you need is not in the summary, `command: "click_by_text"` (e.g. a
"view all" button) then read again, or `command: "screenshot"` to inspect
visually.

## Readable pages

{page_lines}

The same recipes are machine-readable in `operations.json`.

<!-- custom:start:notes -->
<!-- Add skill-specific notes here; this region survives re-compilation. -->
<!-- custom:end:notes -->
"""
    return frontmatter + body


def generate_browser_skill(
    *,
    app_slug: str,
    app_name: str,
    workflow_name: str,
    profile_slug: str,
    url_events: list[dict],
    login_url: str,
    output_dir: str,
    session_id: str = "",
    start_url: str = "",
    description_override: str = "",
) -> dict:
    """Compile a recording into an installable browser-driven skill directory.

    Writes SKILL.md, operations.json and manifest.json in the same shape as a
    harness call_web_api skill, so install_skill and the manifest→operations
    routing treat it identically; only runtime.operation_style ("browser") and
    the tool the operations name ("call_web_browser") differ.

    Raises ValueError when the recording yields no readable data page — a
    browser skill with nothing to read is never worth installing, and emitting
    it silently is exactly the "40 operations that all 403" failure in a new
    disguise.

    Returns the manifest dict.
    """
    if not profile_slug:
        raise ValueError(
            "generate_browser_skill requires a profile_slug: a browser skill "
            "drives an authenticated Tabby session, which cannot exist without a "
            "bound profile."
        )

    pages = derive_browser_pages(url_events, login_url=login_url)
    if not pages:
        raise ValueError(
            "No readable data page was captured for this browser skill — the "
            "recording never left the login/auth flow (every page was a login, "
            "OTP, or one-shot-token URL). Re-record reaching the screen whose "
            "data you want to read, then compile again."
        )

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    skill_md = render_browser_skill_md(
        skill_id=app_slug,
        app_name=app_name,
        workflow_name=workflow_name,
        pages=pages,
        profile_slug=profile_slug,
        description_override=description_override,
    )
    (out_path / "SKILL.md").write_text(skill_md, encoding="utf-8")

    operations_json = render_browser_operations_json(pages, profile_slug=profile_slug)
    (out_path / "operations.json").write_text(operations_json, encoding="utf-8")

    op_entries = [
        {
            "name": p["name"],
            "description": f"Read the rendered contents of {p['url']}",
            "recipe": "operations.json",
            "tool": "call_web_browser",
            "url": p["url"],
        }
        for p in pages
    ]

    manifest: dict = {
        "schema_version": "1",
        "skill_id": app_slug,
        "app": {"name": app_name, "slug": app_slug},
        "workflow": {
            "id": app_slug,
            "name": workflow_name,
            "workflow_session_id": session_id,
            "start_url": start_url,
        },
        "auth": {
            "requires_auth": True,
            "profile_slug": profile_slug,
            "strategy": "tabby_browser",
            "execution_strategy": "harness_call_web_browser",
        },
        "runtime": {
            "type": "agent-harness-skill",
            "entrypoint": "SKILL.md",
            "operation_style": "browser",
        },
        "operations": op_entries,
        "artifacts": {
            "skill_file": "SKILL.md",
            "files": ["SKILL.md", "operations.json", "manifest.json"],
        },
        "generation": {
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": "noui",
            "generator_version": "v1-browser-skill",
        },
    }
    (out_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest
