"""The gate: a browser skill installs only after a human approved its replay.

Without this, replay is a report — something produced, looked at, and routinely
skipped when it is inconvenient. The point is that it is a GATE: the installer
refuses a browser skill that has no approved replay for the exact plan being
installed.

Two properties make that hold.

**Approval is tied to the plan.** The report carries a fingerprint of the
operations that were replayed. Amend a step, recompile, and the fingerprint moves
— the old approval no longer matches and the installer refuses again. That is
what makes "the human's confirmation takes precedence" enforceable rather than a
convention. Descriptions are excluded from the fingerprint, so renaming an
operation or fixing a typo does not cost a replay; behaviour changes do.

**Only browser skills are gated.** A HAR-replay skill is verified by its existing
test loop, and replaying one means firing recorded requests, which is a different
risk profile. Gating those here would block a path that already has an answer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from noui_core.verify.replay import operations_fingerprint

#: Where an approved replay is recorded, beside the skill it approves.
APPROVAL_FILE = "replay_approval.json"


class NotApprovedError(RuntimeError):
    """The skill has no approved replay for the plan being installed."""


def _operations_of(skill_dir: Path) -> list[dict]:
    try:
        data = json.loads((skill_dir / "operations.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ops = data.get("operations")
    return ops if isinstance(ops, list) else []


def is_browser_skill(skill_dir: Path) -> bool:
    """Does this directory hold a browser-driven skill?

    Read from the manifest's runtime style, falling back to the operations' tool
    — a skill whose operations call `call_web_browser` is browser-driven whatever
    the manifest happens to say.
    """
    try:
        manifest = json.loads((skill_dir / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("runtime") or {}).get("operation_style") == "browser":
            return True
    except (OSError, ValueError):
        pass
    return any((op.get("tool") or "") == "call_web_browser" for op in _operations_of(skill_dir))


def write_approval(skill_dir: str | Path, report: dict) -> Path:
    """Record a human's approval beside the skill.

    Refuses a report that is not actually approved, so the file can never be a
    rubber stamp written by the same code that produced the report.
    """
    if not report.get("installable"):
        raise ValueError(
            "refusing to record an approval for a report that was never approved — "
            "call replay.approve() with the human's decision first"
        )
    path = Path(skill_dir) / APPROVAL_FILE
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_approval(skill_dir: str | Path) -> dict | None:
    try:
        return json.loads((Path(skill_dir) / APPROVAL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check_installable(skill_dir: str | Path) -> None:
    """Raise unless this skill may be installed.

    A non-browser skill passes straight through. A browser skill must carry an
    approval whose fingerprint matches the operations on disk right now.
    """
    path = Path(skill_dir)
    if not is_browser_skill(path):
        return

    approval: dict[str, Any] | None = read_approval(path)
    if not approval:
        raise NotApprovedError(
            "this browser skill has not been replayed and approved. Replay the draft "
            "against a live session, show the result to the human, and record their "
            "approval before installing — a browser skill that has never been run is "
            "exactly the kind that looks correct in review and fails in production."
        )
    if not approval.get("installable"):
        raise NotApprovedError("the recorded replay was not approved by a human")

    current = operations_fingerprint(_operations_of(path))
    if approval.get("fingerprint") != current:
        raise NotApprovedError(
            "the skill has changed since it was approved — its steps no longer match "
            "the plan that was replayed. Replay again and get a fresh approval; the "
            "previous one was for a different set of steps."
        )
