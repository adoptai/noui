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


# URL fragments that mean "a sign-in flow ran here". Matched against the
# capture's url_events, which come from CDP frame navigations and so survive
# anything the page itself does.
_LOGIN_URL_MARKERS = (
    "/login",
    "/signin",
    "/sign-in",
    "/sign_in",
    "/prelogin",
    "/oauth",
    "/authorize",
    "/sso",
    "/session/new",
    "/account/login",
    "/auth/",
)


def diagnose_missing_login(bundle: dict[str, Any]) -> dict[str, Any] | None:
    """Explain a capture that yielded no login boundary but looks like it should.

    Returns None when the absence is unremarkable (a genuine workflow-only
    capture). Otherwise returns ``{"reason", "detail", "recorder_silent",
    "login_urls"}`` describing why the boundary is missing.

    There are two very different failure shapes, and telling them apart is the
    whole point — a silent fallback to workflow-only used to hide both:

    * **The recorder was silenced.** Zero interaction events alongside real
      network traffic does not mean the human sat still; it means nothing the
      recorder emitted got through. The usual cause is the page's
      Content-Security-Policy: the beacon the DOM recorder posts each event to
      is refused before it becomes a request, so the whole capture arrives with
      empty ``click_events`` (fixed in Tabby by posting the beacon same-origin —
      older workers and older bundles still show this).
    * **The login left no credential field.** Interaction events exist but none
      is a credential field: SSO hand-off, a magic link clicked in another tab,
      or a session already authenticated by a warm-pool browser.

    Both are recoverable without re-recording (``--mode login``), which is why
    the caller must say so instead of quietly compiling half the asset.
    """
    signals = bundle_signals(bundle)
    if signals["login_boundary"] is not None:
        return None

    clicks = [c for c in (bundle.get("click_events") or []) if isinstance(c, dict)]
    url_events = [u for u in (bundle.get("url_events") or []) if isinstance(u, dict)]
    login_urls = [
        u["to_url"]
        for u in url_events
        if u.get("to_url") and any(m in u["to_url"].lower() for m in _LOGIN_URL_MARKERS)
    ]
    recorder_silent = not clicks and (signals["api_calls_total"] > 0 or len(url_events) > 1)

    if recorder_silent:
        return {
            "reason": "recorder_silent",
            "recorder_silent": True,
            "login_urls": login_urls,
            "detail": (
                f"the capture holds {signals['api_calls_total']} API call(s) and "
                f"{len(url_events)} navigation(s) but NOT ONE interaction event. The human "
                "drove this session, so the recorder was silenced rather than idle — most "
                "often the page's Content-Security-Policy refusing the recorder's beacon. "
                "No credential field can be detected in this capture, whatever was typed."
            ),
        }
    if login_urls:
        return {
            "reason": "login_without_credential_field",
            "recorder_silent": False,
            "login_urls": login_urls,
            "detail": (
                f"{len(clicks)} interaction event(s) were captured but none is a credential "
                f"field, even though the session passed through a sign-in URL "
                f"({login_urls[0]}). Typical of SSO hand-off, magic-link/passwordless login, "
                "or a warm-pool browser that was already signed in."
            ),
        }
    return None


def missing_login_advice(bundle: dict[str, Any], session_id: str, name: str = "") -> list[str]:
    """Operator-facing lines for a capture whose login could not be detected.

    Empty when nothing looks wrong. Otherwise: what happened, why, and the exact
    commands that recover it — the recovery path (``--mode login``) is not
    guessable from the failure alone and used to require reading NoUI's source.
    """
    diagnosis = diagnose_missing_login(bundle)
    if diagnosis is None:
        return []

    name_flag = f" --name {name}" if name else ""
    lines = [
        "",
        "  ⚠ No login segment detected, but this capture does not look login-free:",
        f"    {diagnosis['detail']}",
        "",
        "    Compiling workflow-only means NO App Template is registered, so at runtime",
        "    call_web_api will report that no Tabby profile exists for this app.",
        "",
        "    Recover WITHOUT re-recording — the bundle is already saved:",
        f"      python scripts/capture_import.py {session_id} --mode login{name_flag}",
        "        └ registers the App Template from this same capture, then",
        f"      python scripts/capture_import.py {session_id} --mode workflow{name_flag} \\",
        "          --profile-slug <slug-printed-above> --as skill",
        "        └ compiles the workflow bound to it.",
    ]
    if diagnosis["reason"] == "recorder_silent":
        lines += [
            "",
            "    If you do re-record, note that a silenced recorder will silence the next",
            "    capture too: check the worker log for '[Recording] NO DOM interaction",
            "    events captured' and make sure the worker carries the same-origin beacon fix.",
        ]
    return lines


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
