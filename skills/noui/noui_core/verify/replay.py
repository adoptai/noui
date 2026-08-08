"""Replaying a DRAFT skill before anything is installed.

A recording is a claim about how an app works. Compiling it produces a plan that
looks right on paper — and every failure this project has hit looked right on
paper. `a.mb-0` reads like a selector. A missing download operation looks like a
skill with three operations. A hidden radio looks clickable. The only question
that matters is whether a step resolves against the live page, and the only way
to answer it is to try.

So: compile a draft, replay it against the real app in the session the human just
authenticated, show them what happened, and install NOTHING until they approve.

Three rules shape the design.

**Nothing is installed until a human approves.** The draft is compiled in place,
replayed, and either approved or amended. No App Template is registered, no
profile bound, no catalog entry written before that.

**Risky steps stop and ask.** The replay agent is allowed to improvise around a
blocked step, and an improvising agent on a bank portal can reach Pay, Transfer
or Block Card while hunting for a statement. Anything that moves money, is
irreversible, or touches a control the human never touched is refused here and
handed back for explicit approval — even when it looks like the way forward.

**Replay starts where a real run starts.** Not where the recording happened to
end. An installed skill gets a session provisioned from its profile, loads the
app with stored cookies, and lands on the post-login page; replay does the same.
A plan that only works from mid-flow is the exact false pass this gate exists to
prevent.
"""

from __future__ import annotations

import re
from typing import Any


class SessionNotReadyError(RuntimeError):
    """Tabby has no healthy session for this profile — the human must sign in.

    Deliberately its own type so it can pass THROUGH replay_step rather than
    becoming a blocked step. "The session is not signed in" is not a defect in
    the plan, and recording it as one would blame the skill for the operator not
    having signed in yet. The caller shows the sign-in card and replays again.
    """


#: Step outcomes, as rendered to the human.
OK = "ok"
BLOCKED = "blocked"
RECOVERED = "recovered"
NEEDS_APPROVAL = "needs_approval"
SKIPPED = "skipped"

#: Commands that only read. Safe to replay without asking, always.
_READ_ONLY_COMMANDS = frozenset(
    {"get_page_summary", "get_page_info", "screenshot", "wait_for_selector", "list_downloads"}
)

#: Words that mean this control does something to the account, not just shows it.
#: Deliberately broad: a false stop costs one question, a false proceed can move
#: money.
_RISKY_TEXT = (
    "pay",
    "transfer",
    "remit",
    "send money",
    "fund",
    "block",
    "close",
    "cancel",
    "delete",
    "remove",
    "deactivate",
    "confirm",
    "authorise",
    "authorize",
    "submit",
    "update",
    "change",
    "reset",
    "modify",
)


def _text_of(step: dict) -> str:
    params = step.get("params") or {}
    parts = [
        params.get("text") or "",
        params.get("label") or "",
        params.get("selector") or "",
        (step.get("locator") or {}).get("recorded_text") or "",
    ]
    return " ".join(str(p) for p in parts).lower()


def classify_risk(step: dict, *, recorded_controls: set[str]) -> str | None:
    """Why this step must not be replayed unattended, or None if it is safe.

    Three grounds, in the order they catch things:

    1. It moves money or is irreversible — pay, transfer, block, delete, submit.
    2. It touches a control the human never touched. This is the one that
       actually catches the unexpected: the plan only departs from the recording
       when the agent is improvising, and that is exactly when nobody has
       verified what the control does.
    3. It is a form submission. `submit` operations are compiled from a real
       submit the human made, but replaying one on a bank portal repeats
       whatever it did.

    Read-only commands are never risky, whatever their text says — reading a page
    that happens to contain the word "Transfer" is not a transfer.
    """
    command = step.get("command") or ""
    if command in _READ_ONLY_COMMANDS:
        return None

    text = _text_of(step)
    for word in _RISKY_TEXT:
        if re.search(rf"\b{re.escape(word)}", text):
            return f"looks like it {word}s something rather than reading it"

    identity = _control_identity(step)
    if identity and identity not in recorded_controls:
        return "the human never touched this control during the recording"

    return None


def _control_identity(step: dict) -> str:
    """A stable key for "the same control", for comparing against the recording."""
    params = step.get("params") or {}
    for key in ("selector", "label", "text"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return f"{key}:{value.strip().lower()}"
    return ""


def recorded_controls(operations: list[dict]) -> set[str]:
    """Every control the compiled draft addresses — i.e. what the human did.

    The draft is compiled from the recording, so its steps ARE the human's
    actions. A step outside this set can only have come from the agent
    improvising.
    """
    out: set[str] = set()
    for op in operations or []:
        for step in op.get("steps") or []:
            identity = _control_identity(step)
            if identity:
                out.add(identity)
    return out


def plan_operations(operations: list[dict], *, browser_only: bool = True) -> list[dict]:
    """The operations replay should run.

    Browser skills only, by decision: a HAR-replay skill is validated by its
    existing test loop, and replaying one means firing recorded requests, which
    is a different risk profile entirely.
    """
    if not browser_only:
        return list(operations or [])
    return [op for op in operations or [] if (op.get("tool") or "") == "call_web_browser"]


def replay_step(
    execute: Any,
    step: dict,
    *,
    recorded: set[str],
    approvals: set[str] | None = None,
) -> dict:
    """Run one step and report what happened, without ever raising.

    ``execute(command, params)`` performs the command and returns the worker's
    response; anything it raises becomes a BLOCKED result rather than ending the
    replay. A blocked step is information — it is the thing the human needs to
    see — so it must never abort the run and lose everything after it.

    ``approvals`` holds control identities the human has already approved this
    run, so an approved risky step executes on the next pass instead of asking
    again.
    """
    approvals = approvals or set()
    identity = _control_identity(step)
    result: dict[str, Any] = {
        "command": step.get("command"),
        "params": step.get("params") or {},
        "expect": step.get("expect"),
        "locator": step.get("locator"),
    }

    risk = classify_risk(step, recorded_controls=recorded)
    if risk and identity not in approvals:
        result["status"] = NEEDS_APPROVAL
        result["detail"] = risk
        return result

    try:
        response = execute(step.get("command"), step.get("params") or {})
    except SessionNotReadyError:
        # Not a step failure. Let it out so the caller can ask for a sign-in
        # instead of recording the plan as broken.
        raise
    except Exception as exc:  # noqa: BLE001 — a blocked step is data, not a crash
        result["status"] = BLOCKED
        result["detail"] = str(exc)
        return result

    if isinstance(response, dict) and response.get("success") is False:
        result["status"] = BLOCKED
        result["detail"] = str(response.get("error") or "the command reported failure")
        return result

    result["status"] = OK
    result["data"] = response.get("data") if isinstance(response, dict) else None
    return result


def goal_reached(operation: dict, steps: list[dict]) -> bool:
    """Did this operation actually achieve what it exists for?

    Not "did every step pass" — an operation whose steps all ran but which never
    produced its file has not reached its goal, and that distinction is the whole
    reason `kind` is recorded. For a download the evidence is a file; for
    everything else it is that no step was left blocked.
    """
    if any(s.get("status") in (BLOCKED, NEEDS_APPROVAL) for s in steps):
        return False
    if (operation.get("kind") or "") == "download":
        for s in steps:
            if s.get("command") == "list_downloads":
                data = s.get("data") or {}
                return bool((data or {}).get("downloads"))
        return False
    return True


def build_report(operations: list[dict], results: list[list[dict]]) -> dict:
    """The whole replay, in the shape the confirmation card renders.

    ``results`` is one list of step-results PER operation, positionally matched
    to ``operations``.

    Reports per operation AND overall, because "11 of 12 steps passed" is the
    kind of summary that hides exactly the failure that matters. What the human
    needs to decide on is whether each GOAL was reached.
    """
    ops_out = []
    for op, steps in zip(operations, results, strict=False):
        reached = goal_reached(op, steps)
        ops_out.append(
            {
                "name": op.get("name"),
                "kind": op.get("kind") or "read",
                "description": op.get("description"),
                "goal_reached": reached,
                "steps": steps,
                "blocked_count": sum(1 for s in steps if s.get("status") == BLOCKED),
                "needs_approval_count": sum(1 for s in steps if s.get("status") == NEEDS_APPROVAL),
            }
        )
    return {
        "operations": ops_out,
        "all_goals_reached": bool(ops_out) and all(o["goal_reached"] for o in ops_out),
        "needs_approval": any(o["needs_approval_count"] for o in ops_out),
        # The gate itself. Nothing downstream should install on anything else.
        "installable": False,
    }


def approve(report: dict) -> dict:
    """Mark a replayed draft as approved by the human.

    Separate from build_report on purpose: `installable` is never something the
    replay concludes on its own, however well it went. It is set only here, by an
    explicit human decision, so no code path can install a skill nobody looked at.
    """
    approved = dict(report)
    approved["installable"] = True
    return approved


def substitute_parameters(operation: dict, values: dict[str, str] | None = None) -> list[dict]:
    """The operation's steps with ``{{placeholders}}`` filled in.

    Replay runs the recorded defaults unless told otherwise, because that is the
    run we have evidence for. Typing a literal "{{from_date}}" into a bank's date
    field would fail for a reason that has nothing to do with the plan.
    """
    values = dict(values or {})
    for param in operation.get("parameters") or []:
        values.setdefault(param["name"], param.get("default", ""))

    def fill(text: str) -> str:
        for name, value in values.items():
            text = text.replace("{{" + name + "}}", str(value))
        return text

    out: list[dict] = []
    for step in operation.get("steps") or []:
        params = {
            k: (fill(v) if isinstance(v, str) else v) for k, v in (step.get("params") or {}).items()
        }
        out.append({**step, "params": params})
    return out
