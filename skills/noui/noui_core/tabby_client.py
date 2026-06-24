"""
Tabby API client for the NoUI compiler.

Provides synchronous functions to register, validate, and promote
Tabby Application + ServiceProfile records via urllib.request.

Configuration is read from noui_core.config.settings:
    settings.tabby_api_host   — e.g. "http://localhost:8080"
    settings.tabby_admin_token — bearer token for /admin/* endpoints
"""

from __future__ import annotations

import json
import time
import urllib.error
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


def register_application(bundle: dict, token: str, *, tenant_id: str = "") -> dict:
    """
    POST /apps with the application_draft from bundle.

    Patches http://localhost target_urls to https://localhost so Tabby
    validation does not reject local test origins.

    tenant_id: optional Admin-only override — create the app in that tenant
    (so an admin token can register into the agent token's tenant).

    Returns the created application dict (includes app_id).
    Raises RuntimeError on failure.
    """
    app_draft = bundle.get("application_draft", {})

    # Rewrite http://localhost target_urls to https://localhost for Tabby
    patched_urls = [
        u.replace("http://localhost", "https://localhost", 1)
        if u.startswith("http://localhost")
        else u
        for u in (app_draft.get("target_urls") or [])
    ]
    patched_draft: dict[str, Any] = {**app_draft}
    if patched_urls:
        patched_draft["target_urls"] = patched_urls
    if tenant_id:
        patched_draft["tenant_id"] = tenant_id

    resp = _tabby_http("POST", "/apps", patched_draft, token=token)
    if not isinstance(resp, dict):
        raise RuntimeError(f"Unexpected response type from POST /apps: {type(resp)}")
    return resp


def register_service_profile(bundle: dict, token: str, app_id: str, *, tenant_id: str = "") -> dict:
    """
    POST /admin/profiles with the service_profile_draft from bundle,
    injecting the given app_id and a freshly computed version string.

    tenant_id: optional Admin-only override — create the profile in that tenant.

    Returns the created profile dict (includes id as the DB primary key).
    Raises RuntimeError on failure.
    """
    profile_draft = bundle.get("service_profile_draft", {})

    t = time.localtime()
    version = f"{t.tm_year % 100}.{t.tm_mon}.{t.tm_mday}"

    profile_payload: dict[str, Any] = {
        **profile_draft,
        "app_id": app_id,
        "version": version,
    }
    if tenant_id:
        profile_payload["tenant_id"] = tenant_id

    resp = _tabby_http("POST", "/admin/profiles", profile_payload, token=token)
    if not isinstance(resp, dict):
        raise RuntimeError(f"Unexpected response type from POST /admin/profiles: {type(resp)}")
    return resp


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


def promote_profile(profile_db_id: str, token: str) -> dict:
    """
    POST /admin/profiles/{id}/promote.

    In the standard Tabby flow this must be called twice to move
    STAGING → CANARY → ACTIVE. This function calls it once and
    returns the updated profile dict.

    Raises RuntimeError on failure.
    """
    resp = _tabby_http("POST", f"/admin/profiles/{profile_db_id}/promote", token=token)
    if not isinstance(resp, dict):
        raise RuntimeError(
            f"Unexpected response type from POST /admin/profiles/{profile_db_id}/promote: "
            f"{type(resp)}"
        )
    return resp


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
