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


def run_replay(
    operations: list[dict],
    *,
    profile_slug: str,
    token: str,
    approvals: set[str] | None = None,
    parameter_values: dict[str, str] | None = None,
    timeout_ms: int = 30000,
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
