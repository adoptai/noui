"""NoUI runtime auth adapter — resolves live credentials from Tabby or static secrets.

This is the Skill-output variant of the runtime. It differs from the MCP-server variant
only in how it locates `.env`: skills can be installed at `~/.claude/skills/<skill_id>/`,
far away from the `noui/` checkout, so the parents-index trick used by the MCP variant
does not apply.

Lookup order for `.env`:
  1. `$NOUI_ENV_FILE` — explicit override.
  2. Walk up from this file up to 6 parents looking for any file named `.env`
     (covers both `skills/<skill>/noui_runtime/auth.py` in-repo and a generated tree).
  3. `~/.config/noui/.env`, then `~/.noui/.env`.
  4. Skip dotenv load and rely on already-exported env vars.
"""

from __future__ import annotations

import json
import os
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
    for parent in [here, *here.parents][:7]:
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

TABBY_API_HOST = os.environ.get(
    "TABBY_API_URL", os.environ.get("TABBY_API_HOST", "http://localhost:8080")
)
TABBY_CLIENT_ID = os.environ.get("TABBY_CLIENT_ID", "")
TABBY_CLIENT_SECRET = os.environ.get("TABBY_CLIENT_SECRET", "")

_AUTH_PLAN_PATH = Path(__file__).resolve().parent.parent / "auth_plan.json"


def _load_auth_plan() -> dict:
    try:
        return json.loads(_AUTH_PLAN_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise RuntimeError(f"Failed to read auth_plan.json: {exc}") from exc


async def _get_agent_token() -> str:
    if not TABBY_CLIENT_ID or not TABBY_CLIENT_SECRET:
        raise RuntimeError(
            "Missing TABBY_CLIENT_ID or TABBY_CLIENT_SECRET.\n"
            "Set them in noui/.env, ~/.config/noui/.env, or export them in your shell."
        )
    url = f"{TABBY_API_HOST}/auth/agent-token"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={
                "client_id": TABBY_CLIENT_ID,
                "client_secret": TABBY_CLIENT_SECRET,
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return data.get("access_token") or data.get("token", "")


async def _tabby_credentials(profile_slug: str) -> dict:
    agent_token = await _get_agent_token()
    url = f"{TABBY_API_HOST}/credentials/request"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={"profile_id": profile_slug},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    credentials = data.get("credentials", data)
    headers: dict[str, str] = {}
    for h in credentials.get("headers", []):
        if h.get("name") and h.get("value"):
            headers[h["name"]] = h["value"]
    for cookie in credentials.get("cookies", []):
        existing = headers.get("Cookie", "")
        cname = cookie.get("name", "")
        cval = cookie.get("value", "")
        if cname:
            headers["Cookie"] = f"{existing}; {cname}={cval}".lstrip("; ")
    return headers


def _static_secret_headers(plan: dict) -> dict:
    headers: dict[str, str] = {}
    for fallback in plan.get("fallbacks", []):
        if fallback.get("type") != "static_secret_header":
            continue
        header_name = fallback["header"]
        env_var = fallback["secret_env_var"]
        value_template = fallback["value_template"]
        secret = os.environ.get(env_var, "")
        if not secret:
            raise RuntimeError(
                f"Missing required secret {env_var} for {header_name} header.\n"
                f"Set {env_var} in noui/.env or export it in your shell."
            )
        placeholder = "${" + env_var + "}"
        headers[header_name] = value_template.replace(placeholder, secret)
    return headers


async def resolve_auth(profile_slug_override: str | None = None) -> dict:
    """Resolve auth headers/cookies for this skill based on auth_plan.json.

    Returns a dict of {header_name: header_value} ready to pass to httpx.
    Raises RuntimeError with a human-readable diagnostic on failure.

    `profile_slug_override` lets a CLI `--profile-slug` flag take precedence over the
    value baked into auth_plan.json — useful for testing against a non-default profile.
    """
    plan = _load_auth_plan()
    strategy = plan.get("strategy", "tabby_credentials")

    if strategy == "tabby_credentials":
        profile_slug = (
            profile_slug_override or os.environ.get("PROFILE_SLUG") or plan.get("profile_slug", "")
        )
        if not profile_slug:
            raise RuntimeError(
                "No profile_slug available. Set PROFILE_SLUG in the environment, "
                "pass --profile-slug, or regenerate the skill with "
                "`noui workflow export --as skill --profile-slug <slug>`."
            )
        creds = await _tabby_credentials(profile_slug)
        required = plan.get("required_auth", {}).get("headers", [])
        if required and not creds:
            raise RuntimeError(
                f"Tabby returned empty credentials for profile {profile_slug!r}.\n"
                f"Required headers: {required}\n"
                f"Verify the profile is ACTIVE and the Tabby session is live."
            )
        return creds

    elif strategy == "static_secret_header":
        return _static_secret_headers(plan)

    else:
        raise RuntimeError(
            f"Unknown auth strategy {strategy!r} in auth_plan.json.\n"
            f"Regenerate this skill with `noui workflow export --as skill`."
        )
