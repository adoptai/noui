"""What a browser skill is FOR — the goal, distinct from its operations.

A goal is the user's outcome ("download the annual statement"), NOT one badge per
operation. A statement download is one goal even though the recording navigates,
selects a period, submits a form and only then downloads — those operations are
the PATH to the goal, and the download is the goal.

Two things live here, both compile-side:

  - ``infer_primary_goal`` reads ONE goal off the recording's endpoint. It is a
    FALLBACK: an explicit ask always wins (see the design precedence). When the
    member stated nothing, the caller surfaces this for a one-line confirm before
    compiling. Deliberately single: inferring several goals from a silent
    recording is guesswork; the strongest single signal is the safe floor.
  - ``goal_coverage_warning`` warns (never blocks) when the recording produced no
    artifact at all — the "compiled a read-only skill for a task that was meant to
    fetch a file" trap.

Note what is NOT here: enumerating every terminal as a goal. A workflow that
submits a period, submits a GO, then downloads has three terminals and ONE goal
(the download). The terminals are mechanism; only the endpoint is the goal.
"""

from __future__ import annotations

from typing import Any

# Terminal kinds, strongest goal-signal first (a completed download is a file the
# user asked for; a submit is a form driven to a result; a read is only a step).
# Imported from the compiler's terminal detection, NOT re-declared here, so the two
# can never drift: a new terminal kind added there is picked up by goal inference
# and the coverage warning automatically.
from noui_core.compile.browser_skill import (
    _ARTIFACT_KINDS,
    _SUBMIT_KINDS,
    _TERMINAL_KINDS,
)


def _goal_of(op: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": op.get("name") or "",
        "kind": op.get("kind") or "read",
        "description": op.get("description") or "",
    }


def primary_goal_index(operations: list[dict[str, Any]] | None) -> int | None:
    """Index of the endpoint operation the goal is read from, or None.

    Preference, strongest signal first:

      1. the LAST download — a completed file is the clearest statement of intent,
         even if idle clicks or an intermediate submit followed it;
      2. else the LAST submit — a form the human drove to a result;
      3. else the LAST operation reached — a navigation-only workflow still has a
         destination ("open the dashboard").

    Returning the INDEX (not the op) is deliberate: the replay report's operations
    are positional-1:1 with this list, so a caller reads the endpoint's
    reached-status off the RIGHT operation even when two operations share a name —
    matching by name would resolve to whichever came first.

    Only dict operations are considered; a non-dict entry (a placeholder count, a
    malformed op) can never be an endpoint, and the returned index always points
    at a dict so ``infer_primary_goal`` can read a descriptor off it.
    """
    ops = list(operations or [])
    for kinds in (_ARTIFACT_KINDS, _SUBMIT_KINDS):
        matches = [
            i
            for i, op in enumerate(ops)
            if isinstance(op, dict) and (op.get("kind") or "") in kinds
        ]
        if matches:
            return matches[-1]
    dict_indices = [i for i, op in enumerate(ops) if isinstance(op, dict)]
    return dict_indices[-1] if dict_indices else None


def infer_primary_goal(operations: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The ONE goal a silent recording implies, read off its endpoint.

    A ``{name, kind, description}`` descriptor for the operation
    ``primary_goal_index`` selects, or None for a recording with no operations at
    all. An explicit ask ALWAYS overrides this — it is only the floor for a
    recording the member said nothing about.
    """
    ops = list(operations or [])
    idx = primary_goal_index(ops)
    return _goal_of(ops[idx]) if idx is not None else None


def goal_coverage_warning(operations: list[dict[str, Any]] | None) -> str | None:
    """Warn (never block) when the recording demonstrated no artifact-bearing goal.

    No download and no submit means nothing produced a result — a bank server that
    failed mid-record, or a walkthrough that stopped short. That is exactly the
    trap where a task meant to fetch a file compiled to a read-only skill and
    "passed" every check. The skill still compiles and the member still decides,
    so this warns rather than refusing.
    """
    if not any(
        isinstance(op, dict) and (op.get("kind") or "") in _TERMINAL_KINDS
        for op in operations or []
    ):
        return (
            "This recording demonstrated no goal: it captured no download and no "
            "submit, so nothing produced a result. It still compiles as read-only, "
            "but confirm with the member what the skill should achieve — and that "
            "the recording actually reached it — before installing."
        )
    return None
