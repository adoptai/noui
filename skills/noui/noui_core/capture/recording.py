"""Pillar 1 — Capture (manual VNC recording).

Thin orchestration over noui_core.tabby_client. Tabby's worker captures the
bundle server-side; here we provision a recording session (returns a VNC URL the
human drives) and later drain + validate the bundle. No extension, no daemon.

Autopilot capture (driving the browser via Tabby /execute/browser) lives in
noui_core.capture.autopilot.
"""

from __future__ import annotations

import os
import time
import urllib.parse

from noui_core import tabby_client
from noui_core.capture.bundle import count_sensitive_unredacted, validate_bundle
from noui_core.capture.classify import classify_bundle
from noui_core.config import settings

_MISSING_CREDS = (
    "No complete set of Tabby credentials found — a partially-set pair does not count. "
    "Set either ADOPT_API_URL + ADOPT_CLIENT_ID + ADOPT_CLIENT_SECRET (platform_jwt — a "
    "platform PAT, exchanged for a Tabby bearer carrying your real role) or both "
    "TABBY_CLIENT_ID and TABBY_CLIENT_SECRET (agent_token). Either may live in the env "
    "or a .env file."
)

_UNKNOWN_MODE = (
    "Unknown NOUI_TABBY_AUTH_MODE {mode!r} — expected 'broker', 'platform_jwt', or "
    "'agent_token'. Refusing to guess: a misspelled mode must not silently resolve a "
    "different identity."
)

_MISSING_PLATFORM_CREDS = (
    "NOUI_TABBY_AUTH_MODE=platform_jwt but ADOPT_API_URL / ADOPT_CLIENT_ID / "
    "ADOPT_CLIENT_SECRET are not all set — platform_jwt exchanges those for a Tabby bearer."
)

_MISSING_AGENT_CREDS = (
    "NOUI_TABBY_AUTH_MODE=agent_token but TABBY_CLIENT_ID / TABBY_CLIENT_SECRET are not "
    "both set. Run your Tabby setup first, or use platform_jwt with ADOPT_* credentials."
)

_MISSING_BROKER_TOKEN = (
    "NOUI_TABBY_AUTH_MODE=broker but NOUI_BROKER_TOKEN is unset — the harness must "
    "inject the per-conversation capability token into the sandbox env."
)


def _platform_creds() -> tuple[str, str, str]:
    return (
        os.environ.get("ADOPT_API_URL", "").rstrip("/"),
        os.environ.get("ADOPT_CLIENT_ID", ""),
        os.environ.get("ADOPT_CLIENT_SECRET", ""),
    )


def _agent_creds() -> tuple[str, str]:
    return os.environ.get("TABBY_CLIENT_ID", ""), os.environ.get("TABBY_CLIENT_SECRET", "")


def resolve_agent_token() -> str:
    """Resolve the bearer NoUI sends to ``settings.tabby_api_host``.

    Same preference order as ``activate.register.resolve_admin_token`` — recording
    previously accepted only broker and agent_token, so a runtime holding the
    platform credentials NoUI already uses everywhere else could list app templates
    but could not provision the recording session it needs to create one:

      1. ``broker`` — the opaque per-conversation capability the harness injects;
         the broker swaps it for the user's federated bearer, so no Tabby
         credential is present in the sandbox at all.
      2. ``platform_jwt`` (ADOPT_API_URL + ADOPT_CLIENT_ID + ADOPT_CLIENT_SECRET) —
         exchanged for a Tabby JWT carrying the caller's real IdP-resolved role.
         Preferred over agent_token when the mode is not pinned, because it reuses
         the credentials NoUI already needs rather than a second, separately
         managed pair.
      3. ``agent_token`` (TABBY_CLIENT_ID + TABBY_CLIENT_SECRET) — a minted Tabby
         agent token, for local/self-host setups with no platform integration.

    An explicitly set ``NOUI_TABBY_AUTH_MODE`` is honoured and fails loudly when
    its own credentials are missing — and an unrecognised mode is rejected outright
    — rather than silently falling through to a different identity.
    """
    if settings.broker_mode():
        if not settings.broker_token:
            raise RuntimeError(_MISSING_BROKER_TOKEN)
        return settings.broker_token

    mode = settings.tabby_auth_mode
    if mode and mode not in ("platform_jwt", "agent_token"):
        # ``broker`` never reaches here (handled above). Anything else is a typo or a
        # mode this resolver does not implement — raising beats falling through to a
        # different identity, and matches activate.execute_adapter's behaviour.
        raise RuntimeError(_UNKNOWN_MODE.format(mode=mode))

    adopt_api_url, adopt_client_id, adopt_client_secret = _platform_creds()
    client_id, client_secret = _agent_creds()

    if mode == "platform_jwt":
        if not (adopt_api_url and adopt_client_id and adopt_client_secret):
            raise RuntimeError(_MISSING_PLATFORM_CREDS)
        return tabby_client.get_platform_tabby_token(
            adopt_api_url, adopt_client_id, adopt_client_secret
        )

    if mode == "agent_token":
        if not (client_id and client_secret):
            raise RuntimeError(_MISSING_AGENT_CREDS)
        return tabby_client.get_agent_token(client_id, client_secret)

    # Mode unpinned: prefer the platform credentials, matching resolve_admin_token.
    if adopt_api_url and adopt_client_id and adopt_client_secret:
        return tabby_client.get_platform_tabby_token(
            adopt_api_url, adopt_client_id, adopt_client_secret
        )
    if not (client_id and client_secret):
        raise RuntimeError(_MISSING_CREDS)
    return tabby_client.get_agent_token(client_id, client_secret)


def short_link(session_id: str, mode: str = "recording") -> str:
    """Mint a redaction-safe short VNC URL for a recording session.

    Defaults to ``mode="recording"`` (the *Finish & export* viewer needed to complete
    a recording). The raw ``vnc_url`` embeds a JWT the harness redactor strips; this
    short code (``.../s/<id>``) is safe to show a user in the harness. See
    ``tabby_client.create_short_link``.
    """
    return tabby_client.create_short_link(session_id, resolve_agent_token(), mode=mode)


def start(
    mode: str,
    url: str = "",
    *,
    profile: str = "",
    from_session: str = "",
    residential: bool = False,
) -> dict:
    """Provision a Tabby VNC recording session.

    Args:
        mode: "login" or "workflow".
        url: start/login URL to open in the recorded browser.
        profile: (workflow) record using an EXISTING Tabby profile's auth — the
            recorder browser starts authenticated via this profile, so the login
            recording can be skipped entirely. Use when the App Template/profile
            already exists (e.g. "adopt-bank").
        from_session: (workflow) seed cookies from a prior login recording (its
            session id) just captured in this same flow — session reuse, no
            stored credentials. Use --profile instead when a profile already exists.
        residential: route the recorded browser's egress through Tabby's
            residential proxy (US residential IP) instead of datacenter egress.
            Use for sites that block datacenter IPs (e.g. bank portals).

    Returns the Tabby payload: {session_id, app_id, recording_mode, vnc_url, ...}.
    """
    if mode not in ("login", "workflow"):
        raise ValueError(f"mode must be 'login' or 'workflow', got {mode!r}")
    token = resolve_agent_token()
    return tabby_client.create_recording_session(
        mode,
        url,
        token,
        profile,
        source_session_id=from_session,
        residential_proxy=residential,
    )


def _stream_token(vnc_url: str) -> str:
    """Extract the VNC stream token from a vnc_url's ``#token=`` fragment."""
    frag = urllib.parse.urlparse(vnc_url or "").fragment
    return urllib.parse.parse_qs(frag).get("token", [""])[0]


def _wait_for_pod_ready(
    session_id: str, stream_token: str, *, attempts: int = 30, interval: float = 2.0
) -> str:
    """Block until the recording session's pod leaves ``STARTING``.

    The viewer shows "Disconnected" while the pod boots — noVNC (port 6080) isn't
    listening yet. ``GET /vnc/{id}/panel-state`` reads from the DB (not the pod),
    so it answers even during ``STARTING``; we poll it and return as soon as the
    state is anything else (``HEALTHY`` / ``LOGIN_NEEDED`` / ``FAILED`` /
    ``TERMINATED``). At that point the pod is up, so the link we mint next opens a
    viewer that connects immediately. The ~60s cap (30 × 2s) covers worst-case
    startup; any non-``STARTING`` state — including a dead one — exits the loop,
    so we never hang (a dead session is then caught by minting and refreshed).

    ``panel-state`` is a ``/vnc`` route authed by the stream token (query param),
    not the Tabby bearer. In broker mode (Agent Harness sandbox) that route may
    not be reachable if the caller omits the capability bearer — so every poll
    would throw. Rather than burn the whole ~60s budget probing a dead endpoint,
    bail after a few consecutive errors: the login link is still valid and the
    viewer auto-reconnects once the pod is up.

    Returns the last observed state ("" if it never became readable).
    """
    state = ""
    consecutive_errors = 0
    for _ in range(attempts):
        try:
            state = tabby_client.get_recording_panel_state(session_id, stream_token).get(
                "state", ""
            )
            consecutive_errors = 0
        except Exception:
            state = ""
            consecutive_errors += 1
            # panel-state unreachable (e.g. /vnc not proxied in broker mode) —
            # stop polling a dead endpoint and hand back the link.
            if consecutive_errors >= 3:
                return state
        if state and state != "STARTING":
            return state
        time.sleep(interval)
    return state


def provision_live_link(
    mode: str,
    url: str = "",
    *,
    profile: str = "",
    from_session: str = "",
    residential: bool = False,
) -> dict:
    """Provision a recording session and return its payload with a ``login_url``
    that is **verified live** — never a stale/dead link, and never handed over
    before the pod can actually serve the viewer.

    First we wait out pod startup: a freshly-provisioned session is ``STARTING``
    for ~20-40s while the browser pod boots and noVNC starts listening, and the
    viewer shows "Disconnected" the whole time. ``_wait_for_pod_ready`` polls
    ``panel-state`` (a DB read, safe during ``STARTING``) until the state flips,
    so the link we surface opens a viewer that connects right away.

    Then we guard against handing over a *dead* session. Minting the short link
    is the liveness check — Tabby 400s ("Cannot open stream") on a TERMINATED
    session. If that happens we refresh rather than fall back to the
    (also-dead) raw ``vnc_url``:

      1. ``restart`` the session in place (keeps the same ``session_id``), then
         re-mint the link; failing that,
      2. re-provision a fresh session and mint its link.

    Returns the session payload with ``login_url`` set (and ``refreshed`` =
    ``"restart"`` | ``"reprovision"`` when a refresh was needed).
    """
    token = resolve_agent_token()
    result = start(mode, url, profile=profile, from_session=from_session, residential=residential)

    stream_token = _stream_token(result.get("vnc_url", ""))
    sid = result.get("session_id", "")
    # A warm-pool claim is already HEALTHY — the server only returns warm=true
    # after atomically claiming a HEALTHY, pod-backed spare — so there is nothing
    # to wait for. Skip the pod-startup poll entirely; it's only meaningful for a
    # cold start (and in broker mode the /vnc panel-state probe it uses can't be
    # reached, so waiting would just burn the full ~60s budget for nothing).
    if not result.get("warm") and stream_token and sid:
        _wait_for_pod_ready(sid, stream_token)

    try:
        result["login_url"] = tabby_client.create_short_link(sid, token, mode="recording")
        return result
    except RuntimeError:
        pass  # session can't serve a viewer → stale; refresh below.

    if stream_token and tabby_client.restart_recording_session(sid, stream_token):
        try:
            result["login_url"] = tabby_client.create_short_link(sid, token, mode="recording")
            result["refreshed"] = "restart"
            return result
        except RuntimeError:
            pass  # restart didn't revive it → fall through to a fresh session.

    # Restart unavailable or ineffective → provision a brand-new session.
    result = start(mode, url, profile=profile, from_session=from_session, residential=residential)
    result["login_url"] = tabby_client.create_short_link(
        result.get("session_id", ""), token, mode="recording"
    )
    result["refreshed"] = "reprovision"
    return result


def fetch_bundle(session_id: str) -> tuple[str, dict]:
    """Drain and validate a recording bundle from Tabby.

    Returns ``(classification, bundle)`` where classification is "login",
    "workflow" or "combined", derived from the capture's **content** by
    ``noui_core.capture.classify`` — never from ``bundle["recording_mode"]``,
    which Tabby stamps unreliably (warm-pool sessions always report "login").
    The classification is advisory: callers should prefer an explicit ``--mode``
    or the provision ledger when either is available.

    Raises ValueError on an invalid bundle, RuntimeError on unredacted secrets.
    """
    token = resolve_agent_token()
    bundle = tabby_client.get_recording_bundle(session_id, token)
    validate_bundle(bundle)  # raises ValueError on bad shape
    leaks = count_sensitive_unredacted(bundle)
    if leaks:
        raise RuntimeError(
            f"Refusing to import: {leaks} password/OTP value(s) were not redacted in the bundle."
        )
    return classify_bundle(bundle), bundle
