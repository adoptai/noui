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
from noui_core.capture.classify import COMBINED, WORKFLOW, classify_bundle
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
    browser_driven: bool = False,
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
        browser_driven: this recording will be compiled into a browser-driven
            skill, so Tabby reduces the HAR to metadata. Independent of ``mode``
            — a workflow recording of an ordinary REST app still compiles by HAR
            replay and needs the full HAR. Leave False unless the kind is already
            known; ``detect_unreplayable`` decides it at compile time otherwise.
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
        browser_driven=browser_driven,
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
    browser_driven: bool = False,
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
    result = start(
        mode,
        url,
        profile=profile,
        from_session=from_session,
        residential=residential,
        browser_driven=browser_driven,
    )

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
    classification = classify_bundle(bundle)
    warn_if_capture_was_downgraded(bundle, classification)
    return classification, bundle


def warn_if_capture_was_downgraded(bundle: dict, classification: str) -> None:
    """Warn when a workflow capture came back with login-shaped capture.

    Tabby gates its workflow-only capture on the pod's recording mode: locator
    candidates with match counts, element state, interaction outcomes, downloads
    and popup attachment. A pooled spare boots as a login recording and adopts
    its real mode at bind — if that ever regresses, the recording still succeeds
    and still compiles, just from far poorer evidence, and the resulting skill
    misbehaves in ways that look like a bad recording rather than a bug.

    Checked from CONTENT, not from the mode stamp: the classifier already decided
    the human kept driving after signing in, so a bundle that then carries no
    workflow-shaped capture is the signature of that regression.

    A warning, never an error. A poor bundle is still worth compiling, and this
    also fires harmlessly for bundles recorded before the rich capture existed.
    """
    if classification not in (WORKFLOW, COMBINED):
        return
    version = bundle.get("schema_version")
    if not isinstance(version, int) or version < 4:
        return  # recorded before rich capture existed — nothing to expect
    if "download_events" in bundle:
        return  # workflow-shaped: the pod knew what it was
    print(
        "WARNING: this looks like a workflow capture, but the bundle carries no "
        "workflow-only capture (no download_events). The recording pod most likely "
        "ran in 'login' mode, so locator candidates, element state and interaction "
        "outcomes were never recorded. The skill will still compile, from weaker "
        "evidence. Check that the recording session was provisioned with "
        "recording_mode=workflow and that Tabby applied it at bind."
    )
