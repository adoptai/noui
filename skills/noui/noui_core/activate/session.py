"""Bring a profile's own Tabby session up, and surface the sign-in if it needs one.

**Three sessions are involved in authoring a skill**, and confusing them is the
single most surprising thing about the flow:

1. the **login recording** session (a recording-shell app the human drives),
2. the **workflow recording** session (a second recording-shell app, usually
   cookie-seeded from #1),
3. the **profile's own session** — the one `call_web_api` / `/execute/fetch`
   resolve at runtime. Tabby auto-provisions it per user from the tenant-wide App
   Template, on first use.

Session #3 is a different browser from the two the human just drove, and the App
Template registers `credential_ref: manual:` — nothing stored — so it starts
`LOGIN_NEEDED` and needs one interactive sign-in before the first live call.

That sign-in is **correct and stays**. The alternative would be seeding the
recording's cookies into the template, but the template is tenant-wide: that
would hand the recorder's live session to every member of the org. Per-user auth
with nothing stored costs one sign-in.

What was wrong is only that nothing announced it — a harness session finished
compiling, went straight into generalize-testing, and got `login_required` at the
worst possible moment. So: provoke the provisioning *at import time*, find out
whether a sign-in is needed, and hand over the link before anyone starts testing.
"""

from __future__ import annotations

import time
from typing import Any

from noui_core import tabby_client
from noui_core.capture.recording import resolve_agent_token

# States that will never become healthy on their own.
_TERMINAL_STATES = frozenset({"FAILED", "TERMINATED"})


def _login_link(session_id: str, token: str, status: dict[str, Any]) -> str:
    """A link the human can open to complete the sign-in.

    Prefers the short code (``.../s/<id>``): the raw ``vnc_url`` embeds a JWT in
    its ``#token=`` fragment that the harness secret-redactor strips, which breaks
    the link. Mode is deliberately the default resolve panel ("Mark as Resolved"),
    NOT ``recording`` — this is a HITL login, not a capture.
    """
    if session_id:
        try:
            return tabby_client.create_short_link(session_id, token)
        except RuntimeError:
            pass
    return ((status.get("vnc_stream") or {}) or {}).get("url", "") or ""


def ensure_session(
    profile_slug: str, *, wait_seconds: int = 45, poll_interval: float = 3.0
) -> dict[str, Any]:
    """Make sure the profile has a usable session; report what a human must do.

    Returns ``{profile_slug, state, session_id, needs_login, login_url,
    credentials_ready, timed_out}``. ``needs_login`` is True when a sign-in link
    is waiting, False when the session is already HEALTHY, and None when we
    couldn't tell within the budget (still provisioning, or terminal) — never a
    false green.
    """
    token = resolve_agent_token()

    # POST /credentials/request is what triggers Tabby's per-user auto-provisioning
    # from the App Template. It may 404 or come back empty before the profile
    # exists — that's expected; the status poll below is the source of truth.
    credentials_ready = False
    try:
        creds = tabby_client.request_credentials(profile_slug, token)
        credentials_ready = bool(creds.get("headers") or creds.get("cookies"))
    except RuntimeError:
        pass

    result: dict[str, Any] = {
        "profile_slug": profile_slug,
        "state": "",
        "session_id": "",
        "needs_login": None,
        "login_url": "",
        "credentials_ready": credentials_ready,
        "timed_out": False,
    }

    deadline = time.monotonic() + wait_seconds
    while True:
        status: dict[str, Any] = {}
        try:
            status = tabby_client.get_session_status(profile_slug, token)
        except RuntimeError:
            # 404 while the profile/session row is still being created.
            status = {}

        result["state"] = status.get("state", "") or result["state"]
        result["session_id"] = status.get("session_id", "") or result["session_id"]

        if status.get("hitl_active"):
            result["needs_login"] = True
            result["login_url"] = _login_link(result["session_id"], token, status)
            return result
        if result["state"] == "HEALTHY":
            result["needs_login"] = False
            return result
        if result["state"] in _TERMINAL_STATES:
            return result
        if time.monotonic() >= deadline:
            result["timed_out"] = True
            return result
        time.sleep(poll_interval)


def format_activation_notice(result: dict[str, Any]) -> str:
    """The operator-facing message for an ensure_session result.

    Shared by ``capture_import.py --activate-session`` and
    ``scripts/activate_session.py`` so the wording can't drift.
    """
    slug = result["profile_slug"]
    if result["needs_login"] is True:
        link = result["login_url"] or "(no viewer link available — check Tabby)"
        return (
            f"Activation sign-in required for profile '{slug}' (one time, per user):\n"
            f"  {link}\n"
            "This is the app's OWN Tabby session — a different browser from the recording "
            "sessions you just drove, which is why signing in there doesn't carry over. "
            "Nothing is stored: the session belongs to this user only. Sign in once, then "
            "run your live tests."
        )
    if result["needs_login"] is False:
        return (
            f"Profile '{slug}' already has a HEALTHY session — no activation sign-in needed. "
            "Live calls should work now."
        )
    if result["state"] in _TERMINAL_STATES:
        return (
            f"Profile '{slug}' session is {result['state']} — it will not recover on its own. "
            "Re-run this activation, or re-record the login if it keeps failing."
        )
    state = result["state"] or "not created yet"
    return (
        f"Profile '{slug}' is still provisioning (state: {state}). Retry in a moment with:\n"
        f"  python scripts/activate_session.py {slug}\n"
        "The first live call will also trigger it — expect one sign-in prompt then."
    )
