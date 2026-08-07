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
recording — each compiled as: reach the page via the recorded in-app
`click_by_text` chain (NEVER a full-page `navigate`/goto — that reloads the SPA
and expires the session), then `get_page_summary`. There is no request replay,
so no encryption or frozen-header problem.
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
    _url_origin,
)


def _slug_from_path(url: str) -> str:
    """A stable, readable operation-name stem from a URL's path.

    /credit-card -> credit_card ; /accounts/summary -> accounts_summary ;
    the bare origin -> home.
    """
    # Include the hash route: on a hash-router SPA every screen shares one path,
    # so a path-only slug names them all the same (read_landing, read_landing, …).
    path = (urlparse(url).path + _route_fragment(url)).strip("/")
    if not path:
        return "home"
    slug = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")
    return slug or "home"


def _route_fragment(url: str) -> str:
    """The hash-ROUTE part of a URL (``#/accounts`` -> ``/accounts``), or "".

    Only ``#/…`` counts. A bare ``#section`` anchor scrolls within one page and is
    not a route, so treating it as one would split a single page into many.
    """
    fragment = urlparse(url).fragment
    return fragment.rstrip("/") if fragment.startswith("/") else ""


def _page_key(url: str) -> str:
    """origin + path + hash route (query dropped) — the identity of a page.

    The fragment matters because hash-router SPAs put EVERY screen on one path:
    HSBCnet serves /uims/portal/HSBCnet/Landing#/accounts, #/statements, … so a
    path-only key collapsed the whole portal into a single page. That compiled
    without error (generate_browser_skill only raises on an EMPTY page list) and
    shipped a skill that could read nothing but the landing screen. Playwright
    does fire framenavigated on hash changes, so the routes are in the recording —
    they were simply discarded here.
    """
    return _url_origin(url) + urlparse(url).path.rstrip("/") + _route_fragment(url)


def _is_login_flow_url(url: str) -> bool:
    seg = _first_path_segment(url)
    seg_tokens = [t for t in re.split(r"[^a-z0-9]+", seg) if t]
    return seg in _LOGIN_FLOW_SEGMENTS or any(t in _LOGIN_FLOW_SEGMENTS for t in seg_tokens)


# A human reaches a submenu item by a short burst of clicks (expand the parent
# group, then click the child that routes). Clicks this long before the route
# change are treated as part of the same navigation gesture; earlier clicks are
# unrelated. The chain is capped so a noisy recording can't emit a long run.
_NAV_GESTURE_WINDOW_S = 20.0
_NAV_MAX_CLICKS = 3


def _parse_ts(ts: str) -> datetime | None:
    """Parse a recording timestamp (ISO 8601, trailing 'Z' allowed). None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _nav_clicks_for(from_url: str, to_ts: str, click_events: list[dict]) -> list[dict]:
    """The recorded click CHAIN that drove an in-app navigation FROM from_url.

    A browser skill must NOT reach a data page with a full-page navigate/goto:
    that is a reload, and refresh-sensitive portals (ICICI) expire the session on
    it — the skill's own read then lands on /session-expire. The recording already
    captured how a human got there.

    A single click is often not enough: portal navs are accordions. A human clicks
    the parent group ("Cards") to expand the submenu, THEN the child ("Credit
    Card") that actually routes — and only the child click changes the URL. Keying
    off the route-change timestamp alone captures just the child, so the compiled
    skill can never open the menu (the observed ICICI failure: the model clicked
    "Cards" → strict-mode chaos, never reaching the statement). This returns the
    full ordered gesture — parent-expand … child-navigate — so the skill can
    reproduce the whole path. Falls back to the single navigating click when the
    recording lacks reliable timestamps.
    """
    from_key = _page_key(from_url)
    nav_dt = _parse_ts(to_ts)
    cands: list[dict] = []
    for c in click_events or []:
        if (c.get("event_type") or "click") != "click":
            continue
        cu = c.get("url") or ""
        if not cu or _page_key(cu) != from_key:
            continue
        text = (c.get("text_content") or "").strip()
        if not text:
            continue
        cdt = _parse_ts(c.get("timestamp") or "")
        if nav_dt and cdt and cdt > nav_dt:  # click happened after the nav — not its cause
            continue
        cands.append({"text": text, "selector": c.get("selector") or "", "dt": cdt})
    if not cands:
        return []

    # nav_dt is None (a bundle whose url_events carry no timestamp) means the
    # causality filter above could not run, so the "clicks before the nav" set may
    # actually be clicks made AFTER it — a recorded "Log out" would then be
    # compiled into the read recipe. Fall back to the documented single-click
    # behaviour rather than trusting an unbounded window.
    if nav_dt is not None and all(c["dt"] is not None for c in cands):
        cands.sort(key=lambda c: c["dt"])
        anchor = cands[-1]["dt"]  # the navigating click
        chain = [c for c in cands if (anchor - c["dt"]).total_seconds() <= _NAV_GESTURE_WINDOW_S]
    else:
        # Timestamps unreliable — can't bound the gesture, so keep only the
        # navigating click (the recording-order last), i.e. the old behaviour.
        chain = cands[-1:]

    # Collapse consecutive identical labels (double-clicks, re-renders) and cap
    # to the trailing N so only the immediate expand→navigate steps are emitted.
    out: list[dict] = []
    for c in chain:
        if out and out[-1]["text"] == c["text"]:
            continue
        out.append({"text": c["text"], "selector": c["selector"]})
    if len(out) <= _NAV_MAX_CLICKS:
        return out
    # Keep the FIRST click plus the most recent ones. A plain trailing slice drops
    # the entry-nav gesture: "Menu -> Banking -> Cards -> Credit Card" became
    # ["Banking","Cards","Credit Card"], so the first click targeted an element
    # still hidden behind the un-opened hamburger. Three-deep accordions behind a
    # menu toggle are normal on HSBCnet/ICICI.
    return [out[0]] + out[-(_NAV_MAX_CLICKS - 1) :]


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
      - a non-empty list of {"text": ..., "selector": ...} clicks for a page
        reached by clicking within the app — the full gesture (e.g. expand
        "Cards" → click "Credit Card"), each emitted as click_by_text (client-side
        route changes that preserve the session).

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
        # NOT filtered on _has_volatile_query. A one-time token in the recorded
        # URL makes it unreplayable by NAVIGATION — but a browser skill never
        # navigates: it reaches the page by replaying the in-app click chain, and
        # _page_key ignores the query anyway. Discarding here meant a portal whose
        # data screens all carry e.g. ?ticket= compiled to zero readable pages.

        key = _page_key(url)
        if key in seen:
            continue
        seen.add(key)
        # How did the human reach this page? If the transition came FROM a login
        # page, it's the post-login landing (auto-redirect) — no nav click. If it
        # came from another app page, replay the click that drove the SPA route.
        from_url = (ev or {}).get("from_url") or ""
        to_ts = (ev or {}).get("timestamp") or ""
        # Three distinct states, kept apart deliberately:
        #   None  — the post-login LANDING page; the session already lands here.
        #   [...] — the click chain ON from_url that routed here.
        #   []    — an in-app hop whose driving click could not be recovered
        #           (icon/SVG button with no text — common in bank navs).
        # Collapsing [] into None (the old ``or None``) made an unreachable page
        # look like the landing page: its recipe became a bare get_page_summary,
        # so the operation claimed to read /accounts and actually returned the
        # landing DOM. Confidently wrong data is worse than a missing operation.
        is_landing = not from_url or _is_login_flow_url(from_url)
        nav: list[dict] | None = None
        if not is_landing:
            nav = _nav_clicks_for(from_url, to_ts, click_events)
        pages.append(
            {
                "name": f"read_{_slug_from_path(url)}",
                "url": url,
                "nav": nav,
                "_from_key": None if is_landing else _page_key(from_url),
            }
        )
        if len(pages) >= max_pages:
            break
    return _resolve_nav_chains(pages)


def _resolve_nav_chains(pages: list[dict]) -> list[dict]:
    """Rewrite each page's ``nav`` to the FULL click chain from the landing page,
    and drop pages whose chain cannot be resolved.

    ``_nav_clicks_for`` only recovers the clicks made ON the immediately preceding
    page, so a two-hop path (landing → accounts → statements) compiled to just
    ["Statements"]. At run time the session lands on the landing page, where that
    control does not exist — the operation failed, or worse clicked something else
    with the same label. Walking the from-page graph back to the landing page
    yields the whole gesture.
    """
    by_key = {_page_key(p["url"]): p for p in pages}
    resolved: list[dict] = []
    for page in pages:
        chain: list[dict] = []
        cur: dict | None = page
        visited: set[str] = set()
        ok = True
        while cur is not None:
            own = cur.get("nav")
            if own is None:  # reached the landing page — chain is complete
                break
            if not own:  # an undeterminable hop: the whole path is unreliable
                ok = False
                break
            chain = list(own) + chain
            parent_key = cur.get("_from_key")
            if not parent_key or parent_key in visited:
                # No recorded parent (or a cycle): treat what we have as reached
                # from the landing page rather than inventing more hops.
                break
            visited.add(parent_key)
            # A parent that isn't itself a readable page (login flow, filtered
            # out) means we are already at the start of the in-app path.
            cur = by_key.get(parent_key)
        out = {k: v for k, v in page.items() if not k.startswith("_")}
        if not ok:
            continue
        out["nav"] = chain if chain else None
        resolved.append(out)
    return resolved


def _steps_for_page(p: dict) -> list[dict]:
    """Recipe to read a page WITHOUT a reload.

    - landing page (nav is None): just get_page_summary — the session already
      lands here after login.
    - in-app page (nav is a click chain): one click_by_text per click in the
      gesture (e.g. expand "Cards" → click "Credit Card") — each a client-side
      route change, no reload — then get_page_summary.

    A full-page navigate/goto is deliberately never emitted: it reloads the page,
    and refresh-sensitive portals expire the session on it (the ICICI failure —
    the skill's own read landed on /session-expire).
    """
    steps: list[dict] = []
    for click in p.get("nav") or []:
        if click.get("text"):
            steps.append({"command": "click_by_text", "params": {"text": click["text"]}})
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
        if nav:
            clicks = " then ".join(f'click "{c["text"]}"' for c in nav if c.get("text"))
            how = clicks or "the page you land on after login"
        else:
            how = "the page you land on after login"
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
