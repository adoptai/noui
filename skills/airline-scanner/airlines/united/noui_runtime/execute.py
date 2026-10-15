"""NoUI runtime execute adapter — fetch and browser commands via Tabby HTTP API.

Calls Tabby's POST /execute/fetch and POST /execute/browser endpoints instead of
connecting to the browser via CDP WebSocket. Cookies and TLS fingerprint come from
the real authenticated browser session.

Requires:
  - A running Tabby session for the target profile.
  - TABBY_API_HOST / TABBY_API_URL env var (or .env) pointing to the Tabby API.
  - Agent credentials (TABBY_CLIENT_ID / TABBY_CLIENT_SECRET) for token exchange.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx


def _find_env_file() -> str | None:
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
    return (
        os.environ.get("TABBY_API_HOST")
        or os.environ.get("TABBY_API_URL")
        or "http://localhost:8000"
    )


async def _get_agent_token() -> str:
    client_id = os.environ.get("TABBY_CLIENT_ID", "")
    client_secret = os.environ.get("TABBY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "TABBY_CLIENT_ID and TABBY_CLIENT_SECRET must be set. "
            "Create an agent client in the Tabby admin UI."
        )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_tabby_api_host()}/auth/agent-token",
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["access_token"]


async def execute_fetch(
    profile_id: str,
    url: str,
    *,
    method: str = "GET",
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout_ms: int = 30_000,
) -> Any:
    """Run fetch() inside the authenticated Tabby browser session.

    Returns parsed JSON body on 2xx; raises RuntimeError otherwise.
    """
    token = await _get_agent_token()

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

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_tabby_api_host()}/execute/fetch",
            json=request_body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_ms / 1000 + 5,
        )

    if resp.status_code == 409:
        raise RuntimeError(
            f'No healthy Tabby session for profile "{profile_id}". '
            "Run `tabby session ensure --profile <slug>` or check the admin UI."
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Tabby execute/fetch failed ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    status = data.get("status", 0)
    raw_body = data.get("body", "")

    if not (200 <= status < 300):
        raise RuntimeError(f"{method.upper()} {url} -> {status}: {(raw_body or '')[:500]}")

    try:
        return json.loads(raw_body) if raw_body else {}
    except (ValueError, TypeError):
        return {"status": status, "text": raw_body}


async def execute_browser(
    profile_id: str,
    command: str,
    params: dict[str, Any] | None = None,
    *,
    timeout_ms: int = 30_000,
) -> Any:
    """Run a browser command inside the authenticated Tabby browser session.

    Returns the command result on success; raises RuntimeError on failure.
    """
    token = await _get_agent_token()

    request_body: dict[str, Any] = {
        "profile_id": profile_id,
        "command": command,
        "params": params or {},
        "timeout_ms": timeout_ms,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_tabby_api_host()}/execute/browser",
            json=request_body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_ms / 1000 + 5,
        )

    if resp.status_code == 409:
        raise RuntimeError(
            f'No healthy Tabby session for profile "{profile_id}". '
            "Run `tabby session ensure --profile <slug>` or check the admin UI."
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Tabby execute/browser failed ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    if not data.get("success", False):
        raise RuntimeError(f"Browser command '{command}' failed: {data.get('error', 'unknown')}")

    return data.get("data")
