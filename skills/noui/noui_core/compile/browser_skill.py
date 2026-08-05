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


def _page_key(url: str) -> str:
    """origin + path (query dropped) — the identity of a page."""
    return _url_origin(url) + urlparse(url).path.rstrip("/")


def _is_login_flow_url(url: str) -> bool:
    seg = _first_path_segment(url)
    seg_tokens = [t for t in re.split(r"[^a-z0-9]+", seg) if t]
    return seg in _LOGIN_FLOW_SEGMENTS or any(t in _LOGIN_FLOW_SEGMENTS for t in seg_tokens)


def _nav_click_for(from_url: str, to_ts: str, click_events: list[dict]) -> dict | None:
    """The recorded click that drove an in-app navigation FROM from_url.

    A browser skill must NOT reach a data page with a full-page navigate/goto:
    that is a reload, and refresh-sensitive portals (ICICI) expire the session on
    it — the skill's own read then lands on /session-expire. The recording already
    captured how a human got there: a click on the app's own nav that triggers a
    client-side SPA route change (no reload). This finds the latest click on
    from_url at or before the navigation's timestamp that carries usable text.
    """
    from_key = _page_key(from_url)
    best: dict | None = None
    for c in click_events or []:
        if (c.get("event_type") or "click") != "click":
            continue
        cu = c.get("url") or ""
        if not cu or _page_key(cu) != from_key:
            continue
        text = (c.get("text_content") or "").strip()
        if not text:
            continue
        cts = c.get("timestamp") or ""
        if to_ts and cts and cts > to_ts:  # click happened after the nav — not its cause
            continue
        if best is None or cts >= (best.get("timestamp") or ""):
            best = {"text": text, "selector": c.get("selector") or "", "timestamp": cts}
    return best


def derive_browser_pages(
    url_events: list[dict],
    click_events: list[dict] | None = None,
    *,
    login_url: str,
    max_pages: int = 12,
) -> list[dict]:
    """Pick the readable data pages from a recording's URL history, and — for each
    — the in-app CLICK that reaches it (so the skill navigates without a reload).

    Keeps distinct pages on the app's own origin, in first-seen order, dropping
    login/auth-flow pages and pages whose query string is volatile.

    Each page is {name, url, nav}. ``nav`` is:
      - None for the post-login LANDING page (the session lands there after login;
        the skill just reads it — no navigation, no reload), and
      - {"text": ...} for a page reached by clicking within the app (emit
        click_by_text — a client-side route change that preserves the session).

    Empty list means the recording never left the login flow — the caller must
    treat that as "nothing to compile".
    """
    click_events = click_events or []
    app_origin = _url_origin(login_url) if login_url else ""
    seen: set[str] = set()
    pages: list[dict] = []
    for ev in url_events:
        url = (ev or {}).get("to_url") or ""
        if not url or not url.startswith(("http://", "https://")):
            continue
        # Same-origin as the login page only. Third-party widget/telemetry origins
        # (the DevRev/Dynatrace noise that poisoned the HAR-replay skill's
        # identity) must not be treated as pages to read.
        if app_origin and _url_origin(url) != app_origin:
            continue
        if _is_login_flow_url(url):
            continue
        if _has_volatile_query(url):
            continue
        key = _page_key(url)
        if key in seen:
            continue
        seen.add(key)
        # How did the human reach this page? If the transition came FROM a login
        # page, it's the post-login landing (auto-redirect) — no nav click. If it
        # came from another app page, replay the click that drove the SPA route.
        from_url = (ev or {}).get("from_url") or ""
        to_ts = (ev or {}).get("timestamp") or ""
        nav = None
        if from_url and not _is_login_flow_url(from_url):
            nav = _nav_click_for(from_url, to_ts, click_events)
        pages.append({"name": f"read_{_slug_from_path(url)}", "url": url, "nav": nav})
        if len(pages) >= max_pages:
            break
    return pages


def _steps_for_page(p: dict) -> list[dict]:
    """Recipe to read a page WITHOUT a reload.

    - landing page (nav is None): just get_page_summary — the session already
      lands here after login.
    - in-app page (nav has text): click_by_text (a client-side route change, no
      reload) then get_page_summary.

    A full-page navigate/goto is deliberately never emitted: it reloads the page,
    and refresh-sensitive portals expire the session on it (the ICICI failure —
    the skill's own read landed on /session-expire).
    """
    steps: list[dict] = []
    nav = p.get("nav")
    if nav and nav.get("text"):
        steps.append({"command": "click_by_text", "params": {"text": nav["text"]}})
    steps.append({"command": "get_page_summary"})
    return steps


def render_browser_operations_json(pages: list[dict], *, profile_slug: str) -> str:
    """operations.json for a browser skill — a click+read recipe per page.

    Shape mirrors the harness call_web_api operations.json (schema_version +
    operations[]) so the installer and manifest routing treat it identically.
    """
    operations = []
    for p in pages:
        operations.append(
            {
                "name": p["name"],
                "description": f"Read the rendered contents of {p['url']}",
                "tool": "call_web_browser",
                "profile_slug": profile_slug,
                "steps": _steps_for_page(p),
            }
        )
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

    def _page_line(p: dict) -> str:
        nav = p.get("nav")
        how = (
            f'click "{nav["text"]}"'
            if nav and nav.get("text")
            else "the page you land on after login"
        )
        return f"- **{p['name']}** — `{p['url']}` (reach it via {how})"

    page_lines = (
        "\n".join(_page_line(p) for p in pages)
        or "- (no data pages were captured; re-record reaching the target screen)"
    )

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

Navigate the app the way a human does — **click its own menu items**, never a
full-page navigate. This app expires the session on a page reload, so a
`navigate`/goto to a data page lands on its session-expired screen; an in-app
click is a client-side route change that preserves the session.

For each readable page below:
1. If it lists a click, `call_web_browser` with `command: "click_by_text"`,
   `params: {{ "text": "<the menu item>" }}` to route there in-app.
2. `call_web_browser` with `command: "get_page_summary"` — returns the page's
   headings, links, buttons and inputs (the rendered account/card/transaction
   values live in `headings`).

The landing page needs no click — just read it. If a value you need is not in the
summary, `command: "click_by_text"` on the relevant control (e.g. a "view all"
button) then read again, or `command: "screenshot"` to inspect visually. Do NOT
use `command: "navigate"` on this app.

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
    click_events: list[dict] | None = None,
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

    pages = derive_browser_pages(url_events, click_events or [], login_url=login_url)
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
