#!/usr/bin/env python3
"""Record a member's approval of a replayed draft, so it may be installed.

    python scripts/verify_approve.py workbench/skills/<app>

Run this ONLY after showing the member the replay result and getting their
answer. It is the record of a human decision, not a formality to clear on the
way to installing — approving on someone's behalf defeats the entire gate.

Refuses when the replay did not reach every goal. An operation that did not do
what it exists for is not something to wave through, and the fix is to amend and
replay again rather than to approve anyway.

Writes ``replay_approval.json`` beside the skill. The installer checks it, and
checks that its fingerprint still matches the operations on disk — so amending a
step after approval invalidates the approval rather than smuggling a change past
the member who approved something else.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from noui_core.verify.gate import write_approval
from noui_core.verify.replay import approve

REPORT_FILE = "replay_report.json"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("skill_dir", help="compiled skill directory (must contain replay_report.json)")
    p.add_argument(
        "--force",
        action="store_true",
        help="approve although the replay did not reach every goal. The member has "
        "to have said so explicitly, knowing which operation failed.",
    )
    args = p.parse_args()

    skill_dir = Path(args.skill_dir)
    report_path = skill_dir / REPORT_FILE
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(
            f"Cannot read {report_path}: {exc}. Replay the draft first "
            f"(scripts/verify_replay.py) — there is nothing to approve.",
            file=sys.stderr,
        )
        return 1

    if report.get("status") == "login_required":
        print(
            "That replay never ran — there was no signed-in session, so there is "
            "nothing to approve. Approving here would mean vouching for a workflow "
            "nobody has seen work.\n"
            "\n"
            "Stop and wait for the member to sign in. Do not retry this, and do not "
            "install: the installer refuses an unapproved browser skill, so every "
            "attempt fails and hides the fact that you are waiting for them.",
            file=sys.stderr,
        )
        return 1

    if not report.get("all_goals_reached") and not args.force:
        failed = [
            o.get("name") for o in report.get("operations") or [] if not o.get("goal_reached")
        ]
        print(
            "Refusing to approve: the replay did not reach every goal "
            f"({', '.join(str(f) for f in failed) or 'unknown'}). Amend the workflow "
            "and replay again, or pass --force if the member decided to ship it "
            "knowing which operation failed.",
            file=sys.stderr,
        )
        return 1

    try:
        path = write_approval(skill_dir, approve(report))
    except (OSError, ValueError) as exc:
        print(f"Could not record the approval: {exc}", file=sys.stderr)
        return 1

    print(f"Approved — recorded at {path}. The skill may now be installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
