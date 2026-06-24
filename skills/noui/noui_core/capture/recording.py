"""Pillar 1 — Capture (manual VNC recording).

Thin orchestration over noui_core.tabby_client. Tabby's worker captures the
bundle server-side; here we provision a recording session (returns a VNC URL the
human drives) and later drain + validate the bundle. No extension, no daemon.

Autopilot capture (driving the browser via Tabby /execute/browser) lives in
noui_core.capture.autopilot.
"""

from __future__ import annotations

import os

from noui_core import tabby_client
from noui_core.capture.bundle import count_sensitive_unredacted, validate_bundle
from noui_core.config import settings

_MISSING_CREDS = (
    "TABBY_CLIENT_ID and TABBY_CLIENT_SECRET must be set (or present in the env / .env) "
    "to authenticate against Tabby. Run your Tabby setup first."
)

_MISSING_BROKER_TOKEN = (
    "NOUI_TABBY_AUTH_MODE=broker but NOUI_BROKER_TOKEN is unset — the harness must "
    "inject the per-conversation capability token into the sandbox env."
)


def resolve_agent_token() -> str:
    """Resolve the bearer NoUI sends to ``settings.tabby_api_host``.

    In ``broker`` mode this is the opaque per-conversation capability token (the
    broker swaps it for the real per-user Tabby bearer); no Tabby client creds are
    needed or present in the sandbox. Otherwise it is a minted Tabby agent token.
    """
    if settings.broker_mode():
        if not settings.broker_token:
            raise RuntimeError(_MISSING_BROKER_TOKEN)
        return settings.broker_token
    client_id = os.environ.get("TABBY_CLIENT_ID", "")
    client_secret = os.environ.get("TABBY_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        raise RuntimeError(_MISSING_CREDS)
    return tabby_client.get_agent_token(client_id, client_secret)


def short_link(session_id: str) -> str:
    """Mint a redaction-safe short VNC URL for a recording session.

    The raw ``vnc_url`` embeds a JWT the harness redactor strips; this short code
    (``.../s/<id>``) is safe to show a user in the harness. See
    ``tabby_client.create_short_link``.
    """
    return tabby_client.create_short_link(session_id, resolve_agent_token())


def start(
    mode: str,
    url: str = "",
    *,
    profile: str = "",
    from_session: str = "",
) -> dict:
    """Provision a Tabby VNC recording session.

    Args:
        mode: "login" or "workflow".
        url: start/login URL to open in the recorded browser.
        profile: (reserved) existing Tabby profile id for workflow auth.
        from_session: seed cookies from a prior login recording (its session id),
            so a workflow recording starts already authenticated — session reuse,
            no stored credentials.

    Returns the Tabby payload: {session_id, app_id, recording_mode, vnc_url, ...}.
    """
    if mode not in ("login", "workflow"):
        raise ValueError(f"mode must be 'login' or 'workflow', got {mode!r}")
    token = resolve_agent_token()
    return tabby_client.create_recording_session(
        mode, url, token, profile, source_session_id=from_session
    )


def fetch_bundle(session_id: str) -> tuple[str, dict]:
    """Drain and validate a recording bundle from Tabby.

    Returns (session_type, bundle) where session_type is "login" or "workflow".
    Raises ValueError on an invalid bundle, RuntimeError on unredacted secrets.
    """
    token = resolve_agent_token()
    bundle = tabby_client.get_recording_bundle(session_id, token)
    session_type = validate_bundle(bundle)  # raises ValueError on bad shape
    leaks = count_sensitive_unredacted(bundle)
    if leaks:
        raise RuntimeError(
            f"Refusing to import: {leaks} password/OTP value(s) were not redacted in the bundle."
        )
    return session_type, bundle
