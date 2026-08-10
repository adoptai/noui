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

from noui_core.compile import amendments as amendments_mod
from noui_core.verify.gate import write_approval
from noui_core.verify.replay import approve

REPORT_FILE = "replay_report.json"


def _read(path) -> str:
    """File contents, or "" when absent — an unreadable file is not amendments."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("skill_dir", help="compiled skill directory (must contain replay_report.json)")
    p.add_argument(
        "--force",
        action="store_true",
        help="approve although the replay did not reach every goal. The member has "
        "to have said so explicitly, knowing which operation failed.",
    )
    p.add_argument(
        "--approve-amendment",
        action="append",
        default=[],
        metavar="ID",
        help="approve one step that was discovered at replay rather than recorded. "
        "One flag per amendment, by the id in the replay report. The member has to "
        "have seen and answered on each: nobody watched a human perform these.",
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

    # Record which amendments the member approved, by id.
    #
    # An amendment is a step discovered at replay, not one a human was watched
    # performing, and the installer approves those ONE AT A TIME -- approving the
    # skill as a whole would wave through exactly the steps that most need
    # looking at. Nothing wrote this field, so an amended skill could never
    # install however carefully it was reviewed.
    amendments = amendments_mod.load(_read(skill_dir / amendments_mod.AMENDMENTS_FILE))
    approved_ids: list[str] = []
    if amendments:
        wanted = set(args.approve_amendment or [])
        unknown = wanted - {amendments_mod.amendment_id(a) for a in amendments}
        if unknown:
            print(
                f"No amendment has id {', '.join(sorted(unknown))}. Ids come from the "
                f"replay report; approving one that does not exist would record a "
                f"decision about nothing.",
                file=sys.stderr,
            )
            return 1
        # An amendment the replay never ran is not approvable. One build
        # confirmed a single control by hand, amended that step across six
        # operations, and four of them were blocked long before the amended step
        # could run -- all six then read "confirmed working in live session".
        # Approving those would put the member's name on steps nothing has ever
        # executed, which is the one thing this whole file exists to prevent.
        unexercised = [
            a
            for a in amendments
            if amendments_mod.amendment_id(a) in wanted and not amendments_mod.is_verified(a)
        ]
        if unexercised:
            print(
                f"{len(unexercised)} of the amendment(s) you are approving never ran "
                f"in the replay:",
                file=sys.stderr,
            )
            for a in unexercised:
                print(
                    f"  [{amendments_mod.amendment_id(a)}] {amendments_mod.describe(a)}",
                    file=sys.stderr,
                )
            print(
                "\nConfirming a control by hand on one page says nothing about the "
                "same step in an operation the replay never reached. Fix what blocked "
                "those operations and replay again -- then these carry evidence and "
                "the member has something real to decide on.",
                file=sys.stderr,
            )
            return 1
        approved_ids = [
            amendments_mod.amendment_id(a)
            for a in amendments
            if amendments_mod.amendment_id(a) in wanted
        ]
        outstanding = [a for a in amendments if amendments_mod.amendment_id(a) not in wanted]
        if outstanding:
            print(
                f"{len(outstanding)} amendment(s) not approved -- the installer will "
                f"refuse until the member answers on each:",
                file=sys.stderr,
            )
            for a in outstanding:
                print(
                    f"  [{amendments_mod.amendment_id(a)}] {amendments_mod.describe(a)}",
                    file=sys.stderr,
                )

    try:
        record = approve(report)
        if approved_ids:
            record["approved_amendments"] = approved_ids
        path = write_approval(skill_dir, record)
    except (OSError, ValueError) as exc:
        print(f"Could not record the approval: {exc}", file=sys.stderr)
        return 1

    if amendments and len(approved_ids) < len(amendments):
        # Do not say installable when it is not. The installer refuses on an
        # unapproved amendment, and a message that contradicts it just sends the
        # caller to an install that fails.
        print(
            f"Approved — recorded at {path}. The skill will NOT install yet: "
            f"{len(amendments) - len(approved_ids)} amendment(s) still need the "
            f"member's answer (see above)."
        )
        return 0

    print(f"Approved — recorded at {path}. The skill may now be installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
