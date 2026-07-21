"""Split a merged (login + workflow) recording bundle into two slices.

A single Tabby recording session can capture BOTH the login and the subsequent
authenticated workflow (see
`plans/noui/noui-single-session-login-workflow-capture-investigation.md`). Tabby
stamps such a session `recording_mode='login'` — the mode is behaviorally inert,
so NoUI always provisions combined sessions as 'login' and does the login/workflow
differentiation here, on the NoUI side, with zero Tabby changes.

The boundary is the moment the login completes: the first *stable* navigation
after the last credential-field interaction. Everything up to and including that
landing navigation is the login (so `compile_login_bundle` derives the right
`post_login_url` and credential types); everything after is the authenticated
workflow (fed to `compile_workflow_bundle`). Splitting BEFORE tool generation is
what keeps the login form-submit request out of the workflow's tool set — that
request carries the login body and must never become an operation.

Both returned slices are ordinary bundles that the existing compilers consume
unchanged (`{session_id, recording_mode, started_at, stopped_at, har,
click_events, url_events, cookies?}`).
"""

from __future__ import annotations

from typing import Any

from noui_core.compile.login_assets import _is_redirect_hop

# Field roles that mark a credential-entry interaction (login-only signal).
CREDENTIAL_FIELD_ROLES = frozenset({"username", "password", "otp", "unknown_sensitive"})


def find_login_boundary(bundle: dict[str, Any]) -> str | None:
    """Return the ISO timestamp separating login from workflow, or None.

    None means the bundle has no login segment (no credential-field interactions)
    — e.g. a workflow recorded against an already-authenticated profile. The
    caller then treats the whole bundle as workflow-only.

    The boundary is the timestamp of the first stable (non-redirect) URL
    transition that occurs after the last credential-field interaction; if the
    login never navigates afterwards, it falls back to that last interaction.
    """
    clicks = bundle.get("click_events") or []
    cred_ts = [
        c["timestamp"]
        for c in clicks
        if isinstance(c, dict)
        and c.get("field_role") in CREDENTIAL_FIELD_ROLES
        and c.get("timestamp")
    ]
    if not cred_ts:
        return None
    last_cred = max(cred_ts)

    url_events = bundle.get("url_events") or []
    post_login_navs = [
        u["timestamp"]
        for u in url_events
        if isinstance(u, dict)
        and u.get("timestamp")
        and u["timestamp"] > last_cred
        and u.get("to_url")
        and not _is_redirect_hop(u.get("from_url", "") or "", u.get("to_url", ""))
    ]
    return min(post_login_navs) if post_login_navs else last_cred


def split_bundle(bundle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Split a merged bundle into (login_bundle, workflow_bundle) at the boundary.

    Returns None when there is no login segment (see `find_login_boundary`), so
    the caller can fall back to compiling the bundle as workflow-only.

    Slicing rule (timestamps are ISO-8601 UTC, lexicographically comparable):
    the login slice keeps events at or before the boundary (and any event missing
    a timestamp, so a login request never leaks into the workflow); the workflow
    slice keeps only events strictly after the boundary with a real timestamp.
    Top-level `cookies` (captured at drain, representing the authenticated state)
    go to the login slice, which is the only compiler that reads them.
    """
    boundary = find_login_boundary(bundle)
    if boundary is None:
        return None
    return _slice(bundle, boundary, "login"), _slice(bundle, boundary, "workflow")


def _keep(ts: str | None, boundary: str, side: str) -> bool:
    if side == "login":
        return ts is None or ts <= boundary
    return ts is not None and ts > boundary


def _slice(bundle: dict[str, Any], boundary: str, side: str) -> dict[str, Any]:
    passthrough = {
        k: v for k, v in bundle.items() if k not in ("click_events", "url_events", "har", "cookies")
    }
    passthrough["click_events"] = [
        c
        for c in (bundle.get("click_events") or [])
        if isinstance(c, dict) and _keep(c.get("timestamp"), boundary, side)
    ]
    passthrough["url_events"] = [
        u
        for u in (bundle.get("url_events") or [])
        if isinstance(u, dict) and _keep(u.get("timestamp"), boundary, side)
    ]

    har = bundle.get("har") or {}
    log = dict(har.get("log") or {})
    entries = log.get("entries") or []
    log["entries"] = [
        e
        for e in entries
        if isinstance(e, dict) and _keep(e.get("startedDateTime"), boundary, side)
    ]
    passthrough["har"] = {**har, "log": log}

    if side == "login":
        passthrough["cookies"] = bundle.get("cookies") or []
    return passthrough
