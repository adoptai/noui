"""Generate the noui_runtime/execute.py source code for a compiled MCP server or Skill.

Pure function — no web framework or DB dependencies.

The generated module lets operations execute HTTP calls from *inside* Tabby's
authenticated browser via the `POST /execute/fetch` HTTP endpoint on the Tabby
API. Cookies ride on `credentials: 'include'`; the real browser's TLS
fingerprint is preserved.

Requests run over plain HTTP via httpx — there is no client-side CDP or
WebSocket connection. The Tabby worker runs the actual fetch inside the
authenticated browser server-side.

Two auth modes are supported (mirroring noui_runtime/auth.py):
  - agent_token  : POST /auth/agent-token with TABBY_CLIENT_ID/SECRET (default,
                   local/self-host). No owner_user_id — shared (NULL-owner) reach.
  - platform_jwt : POST /v1/users/api-token (Adopt) → POST /auth/token-exchange
                   (Tabby). Carries owner_user_id, so /execute/fetch resolves the
                   per-user profile and can trigger template auto-provisioning.
The mode is resolved from NOUI_TABBY_AUTH_MODE (explicit) or auto-detected from
the presence of ADOPT_* credentials. Bearer tokens are cached per mode.
"""

from __future__ import annotations


def generate_execute_adapter() -> str:
    """Generate noui_runtime/execute.py source code as a string.

    The generated module provides:
      - execute_fetch(profile_id, url, method, body, headers) — fetch inside the browser

    Returns:
        Python source code string for noui_runtime/execute.py.
    """
    return '''\
"""NoUI runtime execute adapter — fetch inside Tabby's authenticated browser session.

Shared across MCP-server and Skill output formats. Calls the Tabby API's
POST /execute/fetch endpoint, which routes to the worker pod and runs
fetch() inside the real authenticated browser via page.evaluate().

Why this module exists:
  - Cookies and TLS fingerprint come from the real authenticated browser.
  - No credential extraction on the Python side.
  - Bypasses Akamai / Cloudflare false positives that fire on httpx requests.
  - No WebSocket or CDP access needed — plain HTTP to the Tabby API.

Auth modes (resolved from NOUI_TABBY_AUTH_MODE, else auto-detected):
  - agent_token  : TABBY_CLIENT_ID / TABBY_CLIENT_SECRET via /auth/agent-token
                   (local/self-host default; shared NULL-owner profile reach).
  - platform_jwt : ADOPT_* platform credentials → platform JWT → Tabby JWT via
                   /auth/token-exchange (cloud; carries owner_user_id, so the
                   server resolves the caller's own profile and can auto-provision
                   it from an App Template).

Requires:
  - A running Tabby session for the target profile.
  - TABBY_API_URL env var (or .env) pointing to the Tabby API.
  - agent_token mode: TABBY_CLIENT_ID / TABBY_CLIENT_SECRET.
  - platform_jwt mode: ADOPT_API_URL / ADOPT_CLIENT_ID / ADOPT_CLIENT_SECRET.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx


def _find_env_file() -> str | None:
    """Walk up from this file to find .env, matching the auth adapter pattern."""
    if os.environ.get("NOUI_ENV_FILE"):
        return os.environ["NOUI_ENV_FILE"]
    here = Path(__file__).resolve().parent
    for _ in range(7):
        candidate = here / ".env"
        if candidate.exists():
            return str(candidate)
        here = here.parent
    for fallback in (
        Path.home() / ".config" / "noui" / ".env",
        Path.home() / ".noui" / ".env",
    ):
        if fallback.exists():
            return str(fallback)
    return None


def _load_env() -> None:
    """Best-effort dotenv load."""
    env_file = _find_env_file()
    if not env_file:
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    except ImportError:
        pass


_load_env()


def _tabby_api_host() -> str:
    """Resolve the Tabby API base URL from environment."""
    return os.environ.get("TABBY_API_URL") or "http://localhost:8000"


def _adopt_api_url() -> str:
    return os.environ.get("ADOPT_API_URL", "").rstrip("/")


def _resolve_auth_mode() -> str:
    """Pick the Tabby auth flow: explicit NOUI_TABBY_AUTH_MODE wins, else auto-detect.

    Auto-detect chooses platform_jwt when all three ADOPT_* credentials are
    present, otherwise agent_token (the local/self-host default).
    """
    explicit = os.environ.get("NOUI_TABBY_AUTH_MODE", "").strip().lower()
    if explicit:
        return explicit
    if _adopt_api_url() and os.environ.get("ADOPT_CLIENT_ID") and os.environ.get("ADOPT_CLIENT_SECRET"):
        return "platform_jwt"
    return "agent_token"


async def _get_agent_token() -> tuple[str, int]:
    """agent_token mode: exchange client credentials for an agent bearer token.

    Reads TABBY_CLIENT_ID and TABBY_CLIENT_SECRET from env. Returns the token and
    its TTL in seconds. This token has no owner_user_id, so the server resolves
    only shared (NULL-owner) profiles.
    """
    client_id = os.environ.get("TABBY_CLIENT_ID", "")
    client_secret = os.environ.get("TABBY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "TABBY_CLIENT_ID and TABBY_CLIENT_SECRET must be set for agent_token mode. "
            "Run `noui tabby setup`, set them in noui/.env, or switch to platform_jwt "
            "mode (set ADOPT_* / NOUI_TABBY_AUTH_MODE=platform_jwt, or run "
            "`noui tabby setup --cloud`)."
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_tabby_api_host()}/auth/agent-token",
            json={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    token = data.get("access_token") or data.get("token", "")
    if not token:
        raise RuntimeError(f"POST /auth/agent-token returned no token: {data}")
    return token, int(data.get("expires_in", 3600))


async def _get_platform_jwt() -> str:
    """platform_jwt mode step 1: exchange platform client credentials for a platform JWT.

    Calls the Adopt platform proxy (POST /v1/users/api-token), which returns a
    Frontegg-signed JWT that Tabby validates via its registered IdP.
    """
    adopt_api_url = _adopt_api_url()
    if not adopt_api_url:
        raise RuntimeError(
            "Missing ADOPT_API_URL for platform_jwt auth mode.\\n"
            "Set ADOPT_API_URL (e.g. https://api.adopt.ai) in noui/.env "
            "or run `noui tabby setup --cloud`."
        )
    client_id = os.environ.get("ADOPT_CLIENT_ID", "")
    client_secret = os.environ.get("ADOPT_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing ADOPT_CLIENT_ID or ADOPT_CLIENT_SECRET for platform_jwt auth mode.\\n"
            "Set these in noui/.env or run `noui tabby setup --cloud`."
        )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{adopt_api_url}/v1/users/api-token",
            json={"client_id": client_id, "secret": client_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"Platform /v1/users/api-token returned no access_token: {data}")
    return token


async def _get_platform_tabby_token() -> tuple[str, int]:
    """platform_jwt mode step 2: exchange the platform JWT for a Tabby JWT.

    The exchanged Tabby JWT carries owner_user_id, so /execute/fetch resolves the
    caller's own profile (and can trigger template auto-provisioning).
    """
    platform_jwt = await _get_platform_jwt()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_tabby_api_host()}/auth/token-exchange",
            json={"subject_token": platform_jwt, "subject_token_type": "oidc_jwt"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"Tabby /auth/token-exchange returned no access_token: {data}")
    return token, int(data.get("expires_in", 3600))


_TOKEN_REFRESH_MARGIN_SECONDS = 60
_token_cache: dict[str, tuple[str, float]] = {}
_token_lock = asyncio.Lock()


async def _get_tabby_bearer() -> str:
    """Return a valid Tabby bearer token for the active auth mode, cached in-process.

    Mode is resolved from NOUI_TABBY_AUTH_MODE (explicit) or auto-detected from the
    presence of ADOPT_* platform credentials. The token is cached per mode until
    shortly before it expires, so repeated execute_fetch() calls reuse it instead
    of re-running the exchange every time.
    """
    mode = _resolve_auth_mode()
    async with _token_lock:
        cached = _token_cache.get(mode)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]
        if mode == "platform_jwt":
            token, ttl = await _get_platform_tabby_token()
        elif mode == "agent_token":
            token, ttl = await _get_agent_token()
        else:
            raise RuntimeError(
                f"Unknown NOUI_TABBY_AUTH_MODE {mode!r} — "
                f"expected 'agent_token' or 'platform_jwt'."
            )
        _token_cache[mode] = (token, time.monotonic() + max(0, ttl - _TOKEN_REFRESH_MARGIN_SECONDS))
        return token


def _is_no_session(resp: httpx.Response) -> bool:
    """True when the execute response means "there is no live session to run in".

    Both 409 (session has no pod_name) and 404 (no ACTIVE/CANARY profile, or no
    HEALTHY session for the app) mean "there is no live session". 404 is in fact
    the *more common* case (a cold/never-started profile). Tabby phrases the 404
    body as "No healthy session" / "No active profile" (credentials.service.ts) —
    match on either so we don't mistake an upstream 404 from the target site
    (which arrives wrapped in a 200 {status: 404} body) for a Tabby session gap.
    """
    if resp.status_code == 409:
        return True
    if resp.status_code == 404:
        text = resp.text.lower()
        return "no healthy session" in text or "no active profile" in text
    return False


async def _warm_up_session(profile_id: str, token: str) -> bool:
    """Best-effort single warm-up to recover a cold/idle-shut profile.

    `/execute/*` does not rescale an idle-shut app or trigger auto-provisioning;
    `/credentials/request` does both (it catches "no healthy session", re-scales
    the idle app to 1, and triggers autoProvisionFromTemplate). We issue exactly
    one such request to nudge Tabby into bringing a session up, then let the
    caller retry execute once. No polling, no loops — if the session is not ready
    on the single retry, the normal actionable error surfaces.

    Returns True if the warm-up request was accepted (so a retry is worthwhile),
    False otherwise. Never raises: a failed warm-up must not mask the original
    execute error.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_tabby_api_host()}/credentials/request",
                json={"profile_id": profile_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
    except (httpx.HTTPError, OSError):
        return False
    # 2xx (creds returned) or 202/409 (rescale kicked off, not ready yet) all
    # indicate the warm-up reached Tabby and a retry may now succeed.
    return resp.status_code < 500


async def execute_fetch(
    profile_id: str,
    url: str,
    *,
    method: str = "GET",
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout_ms: int = 30000,
) -> Any:
    """Execute fetch() inside the authenticated Tabby browser session.

    The Tabby API resolves the profile to a healthy session, routes to the
    worker pod, and runs page.evaluate(fetch(url, {credentials: \'include\'}))
    inside the real browser.

    Args:
        profile_id: Tabby profile slug (known at compile time from the workflow export).
        url: Absolute URL to fetch.
        method: HTTP method; defaults to GET.
        body: Optional JSON-serializable body.
        headers: Optional extra request headers.
        timeout_ms: Timeout in milliseconds (default 30000, max 60000).

    Returns:
        Parsed JSON body on 2xx; raises RuntimeError on non-2xx or errors.

    Set NOUI_EXECUTE_WARMUP=1 to opt into a single warm-up retry: on a
    "no healthy session" 404/409 the runtime issues one POST /credentials/request
    (which rescales an idle-shut app and triggers auto-provisioning) and retries
    execute exactly once before surfacing the actionable error.
    """
    token = await _get_tabby_bearer()

    request_body: dict[str, Any] = {
        "profile_id": profile_id,
        "url": url,
        "method": method.upper(),
        "timeout_ms": timeout_ms,
    }
    if headers:
        request_body["headers"] = headers
    if body is not None:
        request_body["body"] = json.dumps(body) if not isinstance(body, str) else body

    async def _post_execute() -> httpx.Response:
        async with httpx.AsyncClient() as client:
            return await client.post(
                f"{_tabby_api_host()}/execute/fetch",
                json=request_body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout_ms / 1000 + 5,
            )

    resp = await _post_execute()

    # B5: optional one-shot warm-up retry for a cold/idle-shut profile. Gated on
    # an opt-in env var so the default path keeps its single-request latency.
    warmup_enabled = os.environ.get("NOUI_EXECUTE_WARMUP", "").lower() in ("1", "true", "yes")
    if warmup_enabled and _is_no_session(resp) and await _warm_up_session(profile_id, token):
        resp = await _post_execute()

    if resp.status_code == 429:
        raise RuntimeError(f"Rate limited by Tabby API: {resp.text}")
    if _is_no_session(resp):
        raise RuntimeError(
            f"No healthy Tabby session for profile \\"{profile_id}\\". "
            "Run `tabby session ensure --profile <slug>` or check the admin UI."
        )
    if resp.status_code >= 400:
        snippet = resp.text[:500]
        raise RuntimeError(
            f"Tabby execute/fetch failed ({resp.status_code}): {snippet}"
        )

    data = resp.json()

    # The worker returns {status, headers, body} — unwrap the response body
    status = data.get("status", 0)
    raw_body = data.get("body", "")

    if not (200 <= status < 300):
        snippet = (raw_body or "")[:500]
        raise RuntimeError(f"{method.upper()} {url} -> {status}: {snippet}")

    try:
        return json.loads(raw_body) if raw_body else {}
    except (ValueError, TypeError):
        return {"status": status, "text": raw_body}
'''
