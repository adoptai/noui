"""Decide what a capture bundle actually **is**: a login, a workflow, or both.

``bundle["recording_mode"]`` is deliberately **never read here**. Tabby's stamp
cannot be trusted:

* A **combined** capture (sign in, then drive the workflow in one session) is
  provisioned as ``login`` on purpose — see ``split.py``.
* Worse, a session served from Tabby's **warm recording pool** always reports
  ``login`` whatever the client declared: the pool's shared shell app hardcodes
  ``browser_policy.recording_mode: 'login'``, and the worker reads that policy
  once at pod boot. A warm claim rebinds the running pod to the request's own
  shell app but never re-reads it, so the mode the caller asked for is lost by
  drain time (the HTTP provision response still reports it correctly, which is
  why the divergence only shows up at import). This silently routed a workflow
  recording into the login/App-Template path.

So NoUI classifies from the capture's **content** instead. Callers combine this
with two stronger signals — an explicit ``--mode`` flag and the provision
ledger (``noui_core.capture.ledger``, what the operator actually asked for) —
and fall back here only when neither is available.

The signal for "a login happened" is credential-field interaction, the same one
``split.find_login_boundary`` uses. The signal for "and then a workflow
happened" is the human continuing to *drive* after the login landed — a click,
or at least two stable navigations — **plus** real API traffic there.

Both halves of that test are deliberately conservative, because a false
"combined" splits a pure login capture and registers a truncated App Template:

* Post-login API traffic alone means nothing — a login lands on a dashboard that
  fires plenty of XHRs by itself (a real Airbnb login capture: 21 of them, zero
  interaction).
* A single post-login navigation means nothing either — logins settle through
  one last bounce (a real Expedia login capture ends
  ``/onboarding?originUrl=…`` → ``/?challengeReferer=noref``). Two or more
  stable navigations, or any click, is a human going somewhere on purpose.
"""

from __future__ import annotations

from typing import Any

from noui_core.capture.split import CREDENTIAL_FIELD_ROLES, find_login_boundary, split_bundle
from noui_core.compile.login_assets import _is_redirect_hop

# The three shapes a capture can have. "combined" holds both halves and is split
# at import (`capture_import.py --mode combined`).
LOGIN = "login"
WORKFLOW = "workflow"
COMBINED = "combined"
MODES = (LOGIN, WORKFLOW, COMBINED)


def _api_call_count(bundle: dict[str, Any]) -> int:
    """HAR entries the compiler would consider real API calls.

    Uses the compiler's own filter so this count can't drift from what compile
    actually emits.
    """
    from noui_core.compile.har_to_tools import _is_api_call

    entries = ((bundle.get("har") or {}).get("log") or {}).get("entries") or []
    return sum(1 for e in entries if isinstance(e, dict) and _is_api_call(e))


def bundle_signals(bundle: dict[str, Any]) -> dict[str, Any]:
    """Content signals behind the classification (also surfaced by bundle_inspect)."""
    clicks = [c for c in (bundle.get("click_events") or []) if isinstance(c, dict)]
    credential_events = sum(1 for c in clicks if c.get("field_role") in CREDENTIAL_FIELD_ROLES)
    boundary = find_login_boundary(bundle)

    signals: dict[str, Any] = {
        "credential_events": credential_events,
        "login_boundary": boundary,
        "api_calls_total": _api_call_count(bundle),
        "post_login_clicks": 0,
        "post_login_url_events": 0,
        "post_login_stable_navs": 0,
        "post_login_api_calls": 0,
    }
    if boundary is None:
        return signals

    parts = split_bundle(bundle)
    if parts is None:  # pragma: no cover — boundary implies a split
        return signals
    _, workflow_slice = parts
    url_events = [u for u in (workflow_slice.get("url_events") or []) if isinstance(u, dict)]
    signals["post_login_clicks"] = len(workflow_slice.get("click_events") or [])
    signals["post_login_url_events"] = len(url_events)
    signals["post_login_stable_navs"] = sum(
        1
        for u in url_events
        if u.get("to_url") and not _is_redirect_hop(u.get("from_url", "") or "", u["to_url"])
    )
    signals["post_login_api_calls"] = _api_call_count(workflow_slice)
    return signals


def classify_bundle(bundle: dict[str, Any]) -> str:
    """Return ``"login"``, ``"workflow"`` or ``"combined"`` from the capture's content."""
    signals = bundle_signals(bundle)
    if signals["login_boundary"] is None:
        return WORKFLOW
    # "The human kept driving after signing in": a click, or a second stable
    # navigation (the first one is the login settling — see the module docstring).
    kept_driving = signals["post_login_clicks"] >= 1 or signals["post_login_stable_navs"] >= 2
    if kept_driving and signals["post_login_api_calls"]:
        return COMBINED
    return LOGIN
