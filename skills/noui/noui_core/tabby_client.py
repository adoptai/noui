"""
Tabby API client for the NoUI compiler.

Provides synchronous functions to register, validate, and promote
Tabby Application + ServiceProfile records via urllib.request.

Configuration is read from noui_core.config.settings:
    settings.tabby_api_host   — e.g. "http://localhost:8080"
    settings.tabby_admin_token — bearer for register/promote in local/self-host
                                 mode. Those endpoints are Editor-gated, NOT
                                 Admin-only, despite the /admin/ path prefix (the
                                 name is conventional); broker mode forwards the
                                 user's own federated bearer instead.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from noui_core.config import settings

# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------


def _tabby_http(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: int = 15,
) -> dict[str, Any] | list[Any]:
    """
    Make an HTTP request to the Tabby API.

    Raises RuntimeError on non-2xx responses.
    """
    url = settings.tabby_api_host.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else b""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {method} {path}: {body_text}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_alive() -> bool:
    """Return True if the Tabby API is reachable and reports healthy."""
    try:
        with urllib.request.urlopen(
            settings.tabby_api_host.rstrip("/") + "/health/live", timeout=3
        ) as resp:
            return json.loads(resp.read().decode()).get("status") == "ok"
    except Exception:
        return False


def validate_profile(profile_id: str, token: str, timeout_seconds: int = 60) -> dict:
    """
    Poll GET /admin/service-profiles/{id} until the profile's version_state
    is HEALTHY (or health_result_type == PASS), or until timeout_seconds elapses.

    Returns the final profile dict.
    Raises RuntimeError if the profile reaches a terminal failure state or
    the timeout is exceeded.
    """
    deadline = time.monotonic() + timeout_seconds
    interval = 5
    last_resp: dict = {}

    while time.monotonic() < deadline:
        try:
            resp = _tabby_http("GET", f"/admin/service-profiles/{profile_id}", token=token)
            if isinstance(resp, dict):
                last_resp = resp
                state = resp.get("version_state") or resp.get("state", "")
                health = resp.get("health_result_type", "")
                if state == "HEALTHY" or health == "PASS":
                    return resp
                if state in ("FAILED", "TERMINATED") or health == "AUTH_FAIL":
                    raise RuntimeError(
                        f"Profile {profile_id} reached terminal state "
                        f"(state={state}, health={health})"
                    )
        except RuntimeError:
            raise
        except Exception:
            pass
        time.sleep(interval)

    raise RuntimeError(
        f"Profile {profile_id} did not reach HEALTHY within {timeout_seconds}s. "
        f"Last response: {last_resp}"
    )


def get_agent_token(client_id: str, client_secret: str) -> str:
    """
    POST /auth/agent-token with client credentials.

    Returns the short-lived agent JWT string.
    Raises RuntimeError if credentials are missing or rejected.
    """
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing TABBY_CLIENT_ID or TABBY_CLIENT_SECRET — "
            "run `noui tabby setup` to provision agent credentials"
        )
    resp = _tabby_http(
        "POST",
        "/auth/agent-token",
        body={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    if not isinstance(resp, dict):
        raise RuntimeError(f"Unexpected response from POST /auth/agent-token: {type(resp)}")
    token = resp.get("access_token") or resp.get("token", "")
    if not token:
        raise RuntimeError(f"POST /auth/agent-token returned no token: {resp}")
    return token


def get_platform_jwt(adopt_api_url: str, client_id: str, client_secret: str) -> str:
    """
    POST {adopt_api_url}/v1/users/api-token — exchange an Adopt platform PAT
    for a Frontegg-signed platform JWT. This talks to the Adopt platform, not
    Tabby, so it can't use _tabby_http (different host, no bearer needed).

    Returns the platform JWT string. Raises RuntimeError on failure.
    """
    if not adopt_api_url or not client_id or not client_secret:
        raise RuntimeError(
            "Missing ADOPT_API_URL, ADOPT_CLIENT_ID, or ADOPT_CLIENT_SECRET for platform_jwt mode."
        )
    url = adopt_api_url.rstrip("/") + "/v1/users/api-token"
    data = json.dumps({"client_id": client_id, "secret": client_secret}).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from POST {url}: {body_text}") from exc
    token = payload.get("access_token", "")
    if not token:
        raise RuntimeError(f"Platform /v1/users/api-token returned no access_token: {payload}")
    return token


def get_platform_tabby_token(adopt_api_url: str, client_id: str, client_secret: str) -> str:
    """
    Exchange a platform JWT for a Tabby JWT via POST /auth/token-exchange.

    The resulting token carries owner_user_id (per-user profile resolution)
    and a role resolved from Tabby's IdP config for this user (see
    resolveRoleFromIdp — typically Editor or Admin for a real human account,
    which is enough for the Editor-gated admin endpoints register.py and
    activate/register.py's extend_login_scope_for_workflow() use, without
    needing a separately-configured TABBY_ADMIN_TOKEN).

    Returns the Tabby bearer token string. Raises RuntimeError on failure.
    """
    platform_jwt = get_platform_jwt(adopt_api_url, client_id, client_secret)
    resp = _tabby_http(
        "POST",
        "/auth/token-exchange",
        body={"subject_token": platform_jwt, "subject_token_type": "oidc_jwt"},
    )
    if not isinstance(resp, dict):
        raise RuntimeError(f"Unexpected response from POST /auth/token-exchange: {type(resp)}")
    token = resp.get("access_token", "")
    if not token:
        raise RuntimeError(f"Tabby /auth/token-exchange returned no access_token: {resp}")
    return token


def create_recording_session(
    recording_mode: str,
    start_url: str,
    agent_token: str,
    profile_id: str = "",
    source_session_id: str = "",
) -> dict:
    """
    POST /recording/sessions (agent bearer) — provision a recording-shell
    session and get back an authenticated VNC URL.

    ``source_session_id`` seeds the recording browser with the cookies captured
    by a prior login recording (session reuse), so the human starts already
    authenticated without stored credentials.

    Returns {session_id, app_id, recording_mode, vnc_url, expires_at}.
    Raises RuntimeError on failure.
    """
    body: dict[str, Any] = {"recording_mode": recording_mode, "start_url": start_url}
    if profile_id:
        body["profile_id"] = profile_id
    if source_session_id:
        body["source_session_id"] = source_session_id
    # Provisioning blocks server-side until the worker session row exists (worker
    # scheduling can take >15s under load), so allow a generous client timeout.
    resp = _tabby_http("POST", "/recording/sessions", body=body, token=agent_token, timeout=90)
    if not isinstance(resp, dict) or "vnc_url" not in resp:
        raise RuntimeError(f"POST /recording/sessions returned an unexpected payload: {resp}")
    return resp


def get_recording_bundle(session_id: str, agent_token: str) -> dict:
    """
    GET /recording/sessions/{session_id}/bundle (agent bearer).

    Returns the drained VNC recording bundle (HAR + click_events + url_events)
    that the worker captured and the API persisted on "Finish & export".
    Raises RuntimeError if the bundle is missing or the response is malformed.
    """
    resp = _tabby_http(
        "GET",
        f"/recording/sessions/{session_id}/bundle",
        token=agent_token,
    )
    if not isinstance(resp, dict) or "recording_mode" not in resp:
        raise RuntimeError(
            f"GET /recording/sessions/{session_id}/bundle returned an unexpected payload: {resp}"
        )
    return resp


def request_credentials(profile_slug: str, agent_token: str) -> dict:
    """
    POST /credentials/request using the profile slug (not the DB UUID).

    Returns the credentials dict with "headers" and "cookies" lists.
    Raises RuntimeError on failure.
    """
    resp = _tabby_http(
        "POST",
        "/credentials/request",
        body={"profile_id": profile_slug},
        token=agent_token,
    )
    if not isinstance(resp, dict):
        raise RuntimeError(f"Unexpected response from POST /credentials/request: {type(resp)}")
    # Normalize: credentials may be nested under "credentials" key
    return resp.get("credentials", resp)


def get_service_profile_by_slug(profile_slug: str, token: str) -> dict | None:
    """
    Search admin profiles for one matching the given profile_id slug.

    Returns the profile dict if found, None if not found.
    Raises RuntimeError on API errors.
    """
    try:
        resp = _tabby_http("GET", "/admin/profiles", token=token)
    except RuntimeError:
        return None
    if isinstance(resp, list):
        profiles = resp
    elif isinstance(resp, dict):
        profiles = resp.get("profiles", [])
    else:
        profiles = []
    for p in profiles:
        if p.get("profile_id") == profile_slug or p.get("slug") == profile_slug:
            return p
    return None


def update_service_profile_credential_types(
    profile_db_id: str,
    credential_types: dict,
    token: str,
) -> dict:
    """
    PATCH /admin/profiles/{id} to update credential_types.

    credential_types should be {"headers": [...], "cookies": [...]}.
    Returns the updated profile dict.
    Raises RuntimeError on failure.
    """
    resp = _tabby_http(
        "PATCH",
        f"/admin/profiles/{profile_db_id}",
        body={"credential_types": credential_types},
        token=token,
    )
    if not isinstance(resp, dict):
        raise RuntimeError(
            f"Unexpected response from PATCH /admin/profiles/{profile_db_id}: {type(resp)}"
        )
    return resp


def execute_browser(
    profile_slug: str,
    command: str,
    params: dict | None = None,
    *,
    token: str,
    timeout_ms: int | None = None,
) -> dict:
    """
    POST /execute/browser — run a Playwright command in the profile's authenticated
    Tabby browser session (Autopilot). The API resolves the profile's healthy
    session and proxies to the worker. One active consumer per session.

    Commands: navigate, click_element, click_by_text, click_at, type_text,
    type_into_label, press_key, get_page_summary, get_page_info, screenshot,
    wait_for_selector, scroll_page, har_start, har_stop, har_status.

    Returns the worker response: {"success": bool, "data"|"error": ...}.
    Raises RuntimeError on HTTP error or a worker-reported failure.
    """
    body: dict[str, Any] = {"profile_id": profile_slug, "command": command, "params": params or {}}
    if timeout_ms is not None:
        body["timeout_ms"] = timeout_ms
    # Browser commands (navigation) can be slow; give the HTTP call generous headroom.
    http_timeout = max(30, int((timeout_ms or 30000) / 1000) + 10)
    resp = _tabby_http("POST", "/execute/browser", body=body, token=token, timeout=http_timeout)
    if not isinstance(resp, dict):
        raise RuntimeError(f"Unexpected response from POST /execute/browser: {type(resp)}")
    if resp.get("success") is False:
        raise RuntimeError(
            f"execute/browser '{command}' failed: {resp.get('error', 'unknown error')}"
        )
    return resp


def register_app_template(payload: dict, token: str, *, tenant_id: str = "") -> dict:
    """
    POST /admin/app-templates with a template payload from
    noui_core.compile.login_assets.build_app_template_payload.

    tenant_id: optional Admin-only override — create the template in that tenant.

    A template is the tenant-wide, per-user auto-provisioning blueprint.
    Returns the created template dict (includes id). Raises RuntimeError on failure.
    """
    if tenant_id:
        payload = {**payload, "tenant_id": tenant_id}
    resp = _tabby_http("POST", "/admin/app-templates", body=payload, token=token)
    if not isinstance(resp, dict):
        raise RuntimeError(f"Unexpected response from POST /admin/app-templates: {type(resp)}")
    return resp


def get_app_template(template_id: str, token: str) -> dict | None:
    """
    GET /admin/app-templates/{id}.

    Returns the template dict, or None if not found (404). Raises RuntimeError
    on other API errors.
    """
    try:
        resp = _tabby_http("GET", f"/admin/app-templates/{template_id}", token=token)
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise
    if not isinstance(resp, dict):
        raise RuntimeError(
            f"Unexpected response from GET /admin/app-templates/{template_id}: {type(resp)}"
        )
    return resp


def update_app_template(template_id: str, payload: dict, token: str) -> dict:
    """
    PATCH /admin/app-templates/{id} — partial update. Tabby propagates the
    updated export_policy onto every App already cloned from this template
    (see AppTemplatesService.propagateToLinkedApps), and onto every App
    freshly auto-provisioned from it afterward. We deliberately never PUT an
    App directly (see get_app_template_by_profile_slug's docstring) — Apps
    are provisioned FROM templates, so keeping the template as the single
    source of truth is both simpler and Editor-token-friendly. An
    already-provisioned App's own copy of a field the propagation write
    doesn't cover may lag until it's next (re-)provisioned; this is an
    accepted tradeoff, not a bug to route around here.

    Returns the updated template dict. Raises RuntimeError on failure.
    """
    resp = _tabby_http("PATCH", f"/admin/app-templates/{template_id}", body=payload, token=token)
    if not isinstance(resp, dict):
        raise RuntimeError(
            f"Unexpected response from PATCH /admin/app-templates/{template_id}: {type(resp)}"
        )
    return resp


def get_app_template_by_profile_slug(profile_slug: str, token: str) -> dict | None:
    """
    GET /admin/app-templates (list), filtered client-side by
    profile_name_pattern == profile_slug (the auto-provisioning match key —
    see build_app_template_payload's invariant 1).

    Deliberately goes template-first rather than profile -> app -> template_id
    -> template: /apps/{id} is gated to Admin/Operator/Viewer roles (NOT
    Editor — an asymmetry with PUT /apps/{id}, which does allow Editor), while
    this list endpoint has no @Roles restriction at all. A platform-JWT-
    exchanged token (typically Editor role for a real human account) can
    resolve the template this way without ever touching the Apps API, so
    TABBY_ADMIN_TOKEN isn't required for this lookup in local dev.

    Returns the template dict, or None if not found. Raises RuntimeError on
    other API errors.
    """
    resp = _tabby_http("GET", "/admin/app-templates", token=token)
    if isinstance(resp, list):
        templates = resp
    elif isinstance(resp, dict):
        templates = resp.get("data") or resp.get("templates") or []
    else:
        templates = []
    for t in templates:
        if t.get("profile_name_pattern") == profile_slug:
            return t
    return None


def scale_sessions(app_id: str, desired: int, token: str) -> dict:
    """
    POST /apps/{app_id}/sessions/scale — set the desired worker session count.

    The controller reconcile loop creates/terminates sessions to match. Needs an
    Admin/Operator token. Returns {desired_sessions, app_id}.
    """
    resp = _tabby_http(
        "POST", f"/apps/{app_id}/sessions/scale", body={"desired_sessions": desired}, token=token
    )
    if not isinstance(resp, dict):
        raise RuntimeError(
            f"Unexpected response from POST /apps/{app_id}/sessions/scale: {type(resp)}"
        )
    return resp


def get_session_status(profile_slug: str, token: str) -> dict:
    """
    GET /agent/session-status/{profile_slug} — most recent session status for a profile.

    Agent-accessible. When the session needs login (state LOGIN_NEEDED /
    LOGIN_IN_PROGRESS), the response carries ``hitl_active: true`` and
    ``vnc_stream: {url, expires_at}`` — the URL a human opens to complete login.
    Returns the status dict (session_id, state, hitl_active, vnc_stream, ...).
    """
    resp = _tabby_http("GET", f"/agent/session-status/{profile_slug}", token=token)
    if not isinstance(resp, dict):
        raise RuntimeError(
            f"Unexpected response from GET /agent/session-status/{profile_slug}: {type(resp)}"
        )
    return resp


def create_short_link(session_id: str, token: str, mode: str = "") -> str:
    """POST /sessions/{session_id}/short-link — a short (10-min) redirect URL to the
    session's VNC viewer (``.../s/<id>``).

    ``mode="recording"`` produces the **recording** viewer (``?mode=recording`` — the
    one with the *Finish & export* toolbar); omitting ``mode`` gives the default MCP
    resolve panel (``?from=mcp`` — *Mark as Resolved*).

    Always prefer this over the raw ``vnc_url`` when surfacing a login link inside the
    Agent Harness: the raw URL embeds a JWT in its ``#token=`` fragment that the harness
    secret-redactor strips (breaking the link), whereas the short code is redaction-safe.
    """
    body = {"mode": mode} if mode else None
    resp = _tabby_http("POST", f"/sessions/{session_id}/short-link", body=body, token=token)
    if not isinstance(resp, dict) or "short_url" not in resp:
        raise RuntimeError(
            f"POST /sessions/{session_id}/short-link returned an unexpected payload: {resp}"
        )
    return resp["short_url"]


def _vnc_http(method: str, session_id: str, sub: str, stream_token: str, timeout: int = 15) -> dict:
    """Call a ``/vnc/{session_id}/{sub}`` endpoint.

    These are authenticated by the VNC **stream token** (the JWT from the
    ``vnc_url`` ``#token=`` fragment) passed as a ``token`` query param — NOT the
    agent bearer. Raises ``urllib.error`` on non-2xx.
    """
    qs = urllib.parse.urlencode({"token": stream_token})
    url = f"{settings.tabby_api_host.rstrip('/')}/vnc/{session_id}/{sub}?{qs}"
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def get_recording_panel_state(session_id: str, stream_token: str) -> dict:
    """GET /vnc/{id}/panel-state — recording session runtime state.

    Returns ``{state, health_result_type, ...}``. ``state`` is ``STARTING`` while
    the browser pod spins up (the viewer shows "Disconnected" until it leaves
    STARTING), then ``LOGIN_NEEDED`` / ``LOGIN_IN_PROGRESS`` / ``HEALTHY`` once
    connectable, or ``TERMINATED`` / ``FAILED`` once dead.
    """
    return _vnc_http("GET", session_id, "panel-state", stream_token)


def restart_recording_session(session_id: str, stream_token: str) -> bool:
    """POST /vnc/{id}/restart — revive a dead/stuck recording session in place
    (keeps the same ``session_id``). Returns True on success, False on any
    failure (caller should then re-provision a fresh session)."""
    try:
        _vnc_http("POST", session_id, "restart", stream_token)
        return True
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False
