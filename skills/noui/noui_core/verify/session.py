"""Running a draft replay against a FRESH Tabby session for the profile.

Replay deliberately uses the ordinary profile session — the same one an
installed skill gets — rather than trying to inherit the recording's auth.

Inheriting it was the obvious idea and it does not survive contact with real
apps. A bank's session is not always in a cookie: ICICI's statement portal
carries it in the URL (``AuthenticationController;jsessionid=…``), and SPAs
routinely keep their access token in ``sessionStorage``. Seeding cookies into a
cold browser reproduces none of that, so replay would fail for reasons that have
nothing to do with the plan being tested.

Using a real profile session costs one extra sign-in and buys three things:

  - it is exactly how the skill will run once installed, so a pass means what it
    looks like it means;
  - it validates the PROFILE too — if its login or keepalive cannot sustain a
    session on this app, the skill fails in production however good its steps
    are, and that is worth learning now;
  - keepalive holds it, so the amend-and-replay loop costs nothing after the
    first sign-in.
"""

from __future__ import annotations

import re
from typing import Any

from noui_core import tabby_client
from noui_core.verify.replay import (
    SessionNotReadyError,
    build_report,
    plan_operations,
    recorded_controls,
    replay_step,
    substitute_parameters,
)

#: Tabby answers /execute/browser with 404/409 when there is no active profile or
#: no healthy session. noui's HTTP client folds the status into the message.
_NO_SESSION = re.compile(r"HTTP (404|409) from POST /execute/browser")


def _executor(profile_slug: str, token: str, *, timeout_ms: int = 30000) -> Any:
    def execute(command: str, params: dict) -> dict:
        try:
            return tabby_client.execute_browser(
                profile_slug, command, params, token=token, timeout_ms=timeout_ms
            )
        except RuntimeError as exc:
            if _NO_SESSION.search(str(exc)):
                raise SessionNotReadyError(str(exc)) from exc
            raise

    return execute


def session_is_ready(profile_slug: str, token: str) -> bool:
    """Is there a signed-in session to replay against?

    Probed with get_page_info, which reads nothing and changes nothing — asking
    the question must not itself be an action against the app.
    """
    try:
        _executor(profile_slug, token)("get_page_info", {})
    except SessionNotReadyError:
        return False
    except RuntimeError:
        # Reachable but unhappy (a transient worker error). Let the replay run
        # and report per step rather than refusing to start.
        return True
    return True


def _return_to_entry(execute: Any, entry_url: str) -> dict | None:
    """Put the browser back at the start of the journey before replaying it.

    A replay does not get a fresh browser -- the session is deliberately kept
    warm so the amend-and-replay loop costs no sign-ins (see the module
    docstring). The cost of that is a browser sitting wherever the LAST run
    abandoned it, and these operations are one recorded journey cut into pieces:
    every one of them assumes the page its predecessor left behind, and the first
    assumes the landing page.

    Observed: a replay died mid-journey on ICICI, an amendment fixed the step it
    died on, and the re-run began on `/credit-card/add-card` -- where the
    already-passing early steps no longer matched anything, so the amendment
    looked no better than what it replaced. Re-running has to mean re-running,
    not resuming.

    Returns a step-shaped dict when the reset itself failed, so the caller can
    report it as what stopped the replay rather than blaming the first step.
    """
    try:
        info = execute("get_page_info", {})
        here = str((info.get("data") or info).get("url") or "")
    except SessionNotReadyError:
        raise
    except Exception:  # noqa: BLE001 — an unreadable url just means "navigate anyway"
        here = ""

    if here and here.rstrip("/") == entry_url.rstrip("/"):
        return None  # already at the start; navigating would only cost a load

    try:
        execute("navigate", {"url": entry_url})
    except SessionNotReadyError:
        raise
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        return {
            "command": "navigate",
            "params": {"url": entry_url},
            "status": "blocked",
            "error": (
                f"could not return to the start of the journey ({entry_url}): {exc}. "
                f"The browser is on {here or 'an unknown page'}, which is not where "
                "these steps were recorded, so replaying them from here proves nothing."
            ),
        }
    return None


def run_replay(
    operations: list[dict],
    *,
    profile_slug: str,
    token: str,
    approvals: set[str] | None = None,
    parameter_values: dict[str, str] | None = None,
    timeout_ms: int = 30000,
    entry_url: str | None = None,
) -> dict:
    """Replay a draft and report what happened.

    Returns the report from ``build_report``, or ``{"status": "login_required"}``
    when there is no session to replay against — the caller shows the sign-in
    card and calls again. Partial results are kept if the session dies mid-run:
    the steps that already ran are still evidence, and discarding them would make
    a timed-out session look like a plan that does nothing.
    """
    ops = plan_operations(operations)
    if not ops:
        return {
            "operations": [],
            "all_goals_reached": False,
            "needs_approval": False,
            "installable": False,
            "detail": "no browser operations to replay",
        }

    recorded = recorded_controls(ops)
    execute = _executor(profile_slug, token, timeout_ms=timeout_ms)

    results: list[list[dict]] = []
    if entry_url:
        try:
            failed = _return_to_entry(execute, entry_url)
        except SessionNotReadyError as exc:
            report = build_report([], [])
            report["status"] = "login_required"
            report["detail"] = str(exc)
            return report
        if failed is not None:
            results.append([failed])
            report = build_report(ops[:1], results)
            report["detail"] = failed["error"]
            return report

    for op in ops:
        steps = substitute_parameters(op, parameter_values)
        step_results: list[dict] = []
        try:
            for step in steps:
                step_results.append(
                    replay_step(execute, step, recorded=recorded, approvals=approvals)
                )
        except SessionNotReadyError as exc:
            results.append(step_results)
            report = build_report(ops[: len(results)], results)
            report["status"] = "login_required"
            report["detail"] = str(exc)
            return report
        results.append(step_results)

    return build_report(ops, results)
