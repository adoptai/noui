"""Generate the noui_runtime/auth.py source code for a compiled MCP server or Skill.

Pure functions — no web framework or DB dependencies.

The same generated module is written into both output formats:

  - MCP server:  workbench/mcp_servers/<app>/<server>/noui_runtime/auth.py
  - Skill:       workbench/skills/<app>/<skill>/noui_runtime/auth.py
                 (and, after install, ~/.claude/skills/<skill>/noui_runtime/auth.py)

To work in all three locations without per-output special-casing, the generated
auth.py discovers `.env` via a portable walk-up from its own file location,
with env-var override and sensible home-dir fallbacks.
"""

from __future__ import annotations


def generate_auth_adapter(tabby_api_host: str) -> str:
    """Generate noui_runtime/auth.py source code as a string.

    The generated module provides:
      - resolve_auth()           — primary entry point, reads auth_plan.json
      - get_auth_headers(slug)   — legacy shim for backward compatibility

    Strategy is determined at runtime from auth_plan.json:
      - "tabby_credentials"    : POST /auth/agent-token + POST /credentials/request
      - "static_secret_header" : read secret from env var, construct header

    Args:
        tabby_api_host: Default Tabby API base URL baked in as a fallback
            (overridable via the TABBY_API_URL env var).

    Returns:
        Python source code string for noui_runtime/auth.py.
    """
    return f'''\
"""NoUI runtime auth adapter — resolves live credentials from Tabby or static secrets.

Shared across MCP-server and Skill output formats. Locates `.env` via:
  1. $NOUI_ENV_FILE — explicit override
  2. Walk up to 7 parents from this file looking for a `.env` (works both for
     in-repo layouts like workbench/mcp_servers/<app>/<server>/noui_runtime/
     and workbench/skills/<app>/<skill>/noui_runtime/, where noui/.env is
     a few parents up)
  3. ~/.config/noui/.env, then ~/.noui/.env (for skills installed outside
     the noui checkout, e.g. ~/.claude/skills/<skill>/)
  4. Skip dotenv load — rely on already-exported env vars.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv


def _find_env_file() -> Path | None:
    override = os.environ.get("NOUI_ENV_FILE")
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            return p

    here = Path(__file__).resolve()
    for parent in [here, *here.parents][:8]:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate

    for fallback in (Path.home() / ".config" / "noui" / ".env", Path.home() / ".noui" / ".env"):
        if fallback.is_file():
            return fallback

    return None


_env_file = _find_env_file()
if _env_file is not None:
    load_dotenv(_env_file)

TABBY_API_HOST = os.environ.get("TABBY_API_URL", "{tabby_api_host}")
TABBY_CLIENT_ID = os.environ.get("TABBY_CLIENT_ID", "")
TABBY_CLIENT_SECRET = os.environ.get("TABBY_CLIENT_SECRET", "")

# Platform (Adopt) credentials for the cloud token-exchange flow. When these are
# present (or NOUI_TABBY_AUTH_MODE=platform_jwt), the runtime exchanges platform
# client credentials for a platform JWT, then exchanges that JWT for a Tabby JWT
# via Tabby's /auth/token-exchange. Otherwise it uses the local/self-host flow:
# Tabby agent-client credentials via /auth/agent-token.
ADOPT_API_URL = os.environ.get("ADOPT_API_URL", "").rstrip("/")
ADOPT_CLIENT_ID = os.environ.get("ADOPT_CLIENT_ID", "")
ADOPT_CLIENT_SECRET = os.environ.get("ADOPT_CLIENT_SECRET", "")


def _resolve_auth_mode() -> str:
    """Pick the Tabby auth flow: explicit NOUI_TABBY_AUTH_MODE wins, else auto-detect."""
    explicit = os.environ.get("NOUI_TABBY_AUTH_MODE", "").strip().lower()
    if explicit:
        return explicit
    if ADOPT_API_URL and ADOPT_CLIENT_ID and ADOPT_CLIENT_SECRET:
        return "platform_jwt"
    return "agent_token"


# auth_plan.json lives at the output root (one level up from noui_runtime/)
_AUTH_PLAN_PATH = Path(__file__).resolve().parent.parent / "auth_plan.json"


def _load_auth_plan() -> dict:
    try:
        return json.loads(_AUTH_PLAN_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {{}}
    except Exception as exc:
        raise RuntimeError(f"Failed to read auth_plan.json: {{exc}}") from exc


_TOKEN_REFRESH_MARGIN_SECONDS = 60
_token_cache: dict[str, tuple[str, float]] = {{}}
_token_lock = asyncio.Lock()


async def _get_agent_token() -> tuple[str, int]:
    """Local/self-host flow: exchange Tabby agent-client credentials for an agent JWT."""
    if not TABBY_CLIENT_ID or not TABBY_CLIENT_SECRET:
        raise RuntimeError(
            "Missing TABBY_CLIENT_ID or TABBY_CLIENT_SECRET.\\n"
            "Run `noui tabby setup` or set these vars in noui/.env "
            "(or ~/.config/noui/.env for skills installed outside the noui checkout)."
        )
    url = f"{{TABBY_API_HOST}}/auth/agent-token"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={{
                "client_id": TABBY_CLIENT_ID,
                "client_secret": TABBY_CLIENT_SECRET,
                "grant_type": "client_credentials",
            }},
        )
        resp.raise_for_status()
        data = resp.json()
    token = data.get("access_token") or data.get("token", "")
    if not token:
        raise RuntimeError(f"POST /auth/agent-token returned no token: {{data}}")
    return token, int(data.get("expires_in", 3600))


async def _get_platform_jwt() -> str:
    """Cloud flow step 1: exchange platform client credentials for a platform JWT.

    Calls the Adopt platform proxy (POST /v1/users/api-token), which returns a
    Frontegg-signed JWT that Tabby validates via its registered IdP.
    """
    if not ADOPT_API_URL:
        raise RuntimeError(
            "Missing ADOPT_API_URL for platform_jwt auth mode.\\n"
            "Set ADOPT_API_URL (e.g. https://api.adopt.ai) in noui/.env "
            "or run `noui tabby setup --cloud`."
        )
    if not ADOPT_CLIENT_ID or not ADOPT_CLIENT_SECRET:
        raise RuntimeError(
            "Missing ADOPT_CLIENT_ID or ADOPT_CLIENT_SECRET for platform_jwt auth mode.\\n"
            "Set these in noui/.env or run `noui tabby setup --cloud`."
        )
    url = f"{{ADOPT_API_URL}}/v1/users/api-token"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={{"client_id": ADOPT_CLIENT_ID, "secret": ADOPT_CLIENT_SECRET}},
        )
        resp.raise_for_status()
        data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"Platform /v1/users/api-token returned no access_token: {{data}}")
    return token


async def _get_platform_tabby_token() -> tuple[str, int]:
    """Cloud flow step 2: exchange the platform JWT for a Tabby JWT via token-exchange."""
    platform_jwt = await _get_platform_jwt()
    url = f"{{TABBY_API_HOST}}/auth/token-exchange"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={{
                "subject_token": platform_jwt,
                "subject_token_type": "oidc_jwt",
            }},
        )
        resp.raise_for_status()
        data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"Tabby /auth/token-exchange returned no access_token: {{data}}")
    return token, int(data.get("expires_in", 3600))


async def _get_tabby_bearer() -> str:
    """Return a valid Tabby bearer token for the active auth mode, cached in-process.

    Mode is resolved from NOUI_TABBY_AUTH_MODE (explicit) or auto-detected from the
    presence of ADOPT_* platform credentials. The token is cached until shortly
    before it expires to avoid re-running the exchange on every credential fetch.
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
                f"Unknown NOUI_TABBY_AUTH_MODE {{mode!r}} — "
                f"expected 'agent_token' or 'platform_jwt'."
            )
        _token_cache[mode] = (token, time.monotonic() + max(0, ttl - _TOKEN_REFRESH_MARGIN_SECONDS))
        return token


async def _tabby_credentials(profile_slug: str) -> dict:
    """Fetch live auth headers/cookies from Tabby using the 2-step credential flow.

    Uses profile_slug (not the DB UUID) for the credentials/request call.
    """
    bearer = await _get_tabby_bearer()
    url = f"{{TABBY_API_HOST}}/credentials/request"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={{"profile_id": profile_slug}},
            headers={{"Authorization": f"Bearer {{bearer}}"}},
        )
        resp.raise_for_status()
        data = resp.json()
    credentials = data.get("credentials", data)
    headers: dict[str, str] = {{}}
    for h in credentials.get("headers", []):
        if h.get("name") and h.get("value"):
            headers[h["name"]] = h["value"]
    for cookie in credentials.get("cookies", []):
        existing = headers.get("Cookie", "")
        cname = cookie.get("name", "")
        cval = cookie.get("value", "")
        if cname:
            headers["Cookie"] = f"{{existing}}; {{cname}}={{cval}}".lstrip("; ")
    return headers


def _static_secret_headers(plan: dict) -> dict:
    """Resolve static secret header(s) from environment variables."""
    headers: dict[str, str] = {{}}
    for fallback in plan.get("fallbacks", []):
        if fallback.get("type") != "static_secret_header":
            continue
        header_name = fallback["header"]
        env_var = fallback["secret_env_var"]
        value_template = fallback["value_template"]
        secret = os.environ.get(env_var, "")
        if not secret:
            raise RuntimeError(
                f"Missing required secret {{env_var}} for {{header_name}} header.\\n"
                f"Set {{env_var}} in noui/.env or export it in your shell."
            )
        placeholder = "${{" + env_var + "}}"
        headers[header_name] = value_template.replace(placeholder, secret)
    return headers


async def resolve_auth() -> dict:
    """Resolve auth headers/cookies for this server or skill based on auth_plan.json.

    Returns a dict of {{header_name: header_value}} ready to pass to httpx.
    Raises RuntimeError with a human-readable diagnostic on failure.
    """
    plan = _load_auth_plan()
    strategy = plan.get("strategy", "tabby_credentials")

    if strategy == "tabby_credentials":
        profile_slug = plan.get("profile_slug", "")
        if not profile_slug:
            raise RuntimeError(
                "auth_plan.json missing profile_slug — "
                "regenerate with `noui workflow export --as mcp --profile-slug <slug>` "
                "(or `--as skill`, `--as both`)."
            )
        creds = await _tabby_credentials(profile_slug)
        required = plan.get("required_auth", {{}}).get("headers", [])
        if required and not creds:
            raise RuntimeError(
                f"Tabby returned empty credentials for profile {{profile_slug!r}}.\\n"
                f"Required headers: {{required}}\\n"
                f"Run `noui mcp diagnose-auth <server_id>` for repair guidance "
                f"(MCP servers) or verify the Tabby profile is ACTIVE (skills)."
            )
        return creds

    elif strategy == "static_secret_header":
        return _static_secret_headers(plan)

    else:
        raise RuntimeError(
            f"Unknown auth strategy {{strategy!r}} in auth_plan.json.\\n"
            f"Regenerate this output with `noui workflow export --as mcp|skill|both`."
        )


# ---------------------------------------------------------------------------
# Legacy compatibility — servers generated before auth_plan.json was introduced
# ---------------------------------------------------------------------------


async def get_auth_headers(profile_id: str) -> dict:
    """Deprecated: prefer resolve_auth().

    Fetches credentials from Tabby using the given profile slug directly,
    bypassing auth_plan.json strategy selection.
    """
    return await _tabby_credentials(profile_id)
'''
