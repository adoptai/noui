#!/usr/bin/env python3
"""Replay a compiled DRAFT against a live session, before anything is installed.

    python scripts/verify_replay.py workbench/skills/<app> --profile-slug <slug>

Compile first, replay here, show the result to the member, and only then
install. A browser skill that has never been run against the live app is
exactly the kind that looks correct in review and fails in production — every
failure this pipeline has hit looked correct on paper.

Writes ``replay_report.json`` beside the skill and prints it, so the caller can
render the verification card. It does NOT approve anything: `installable` stays
false until a member says otherwise (see verify_approve.py).

Exit codes:
  0  the replay ran — read the report to see whether the goals were reached
  1  could not replay (no skill, no operations, bad arguments)
  2  no signed-in session; show the sign-in card and run this again
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from noui_core.capture.recording import resolve_agent_token
from noui_core.verify.replay import plan_operations
from noui_core.verify.session import run_replay

REPORT_FILE = "replay_report.json"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("skill_dir", help="compiled skill directory (contains operations.json)")
    p.add_argument("--profile-slug", required=True, help="Tabby profile the skill drives")
    p.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="override a recorded parameter for this replay. Omit to use what was "
        "recorded, which is the run we have evidence for.",
    )
    p.add_argument(
        "--approve-step",
        action="append",
        default=[],
        metavar="SELECTOR|TEXT",
        help="pre-approve a step the risk rules would otherwise stop on (a control "
        "that moves money, is irreversible, or the human never touched). One flag "
        "per control; the member must have said so.",
    )
    args = p.parse_args()

    skill_dir = Path(args.skill_dir)
    ops_path = skill_dir / "operations.json"
    try:
        doc = json.loads(ops_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Cannot read {ops_path}: {exc}", file=sys.stderr)
        return 1

    operations = doc.get("operations") if isinstance(doc, dict) else None
    if not operations:
        print(f"{ops_path} has no operations to replay.", file=sys.stderr)
        return 1
    if not plan_operations(operations):
        # Not a failure: a HAR-replay skill is verified by its own test loop.
        print("No browser operations — nothing for this gate to replay.", file=sys.stderr)
        return 1

    values = {}
    for raw in args.param:
        name, _, value = raw.partition("=")
        if not name or not _:
            print(f"--param expects NAME=VALUE, got {raw!r}", file=sys.stderr)
            return 1
        values[name] = value

    report = run_replay(
        operations,
        profile_slug=args.profile_slug,
        token=resolve_agent_token(),
        approvals=set(args.approve_step) or None,
        parameter_values=values or None,
    )

    out = skill_dir / REPORT_FILE
    try:
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        # The report still goes to stdout; losing the file is not worth failing a
        # replay that already ran against a live session.
        print(f"(warning: could not write {out}: {exc})", file=sys.stderr)

    print(json.dumps(report, indent=2, ensure_ascii=False))

    if report.get("status") == "login_required":
        print(
            "\nNo signed-in session, so the workflow was never tried. This is not a "
            "fault in the skill.\n"
            "\n"
            "DO NOT start a recording session to fix this. A recording is a human "
            "driving a browser so NoUI can capture it; this needs the profile's own "
            "RUNTIME session, which is a different thing with a different sign-in "
            "surface. Handing over a recording link here produces a viewer with a "
            "'Finish & export' button, which is not what the member was asked for and "
            "throws away the sign-in they just did.\n"
            "\n"
            "To get a runtime session: call the skill's own operation through "
            "call_web_browser. It returns status=login_required with a sign-in link, "
            "the platform renders the sign-in card, the member signs in there, and the "
            "session stays warm. Then run this script again — replays after the first "
            "sign-in are free.\n"
            "\n"
            "THEN STOP AND WAIT. Tell the member you are waiting for their sign-in "
            "and say nothing else until they answer. Do not approve, do not install, "
            "do not re-run this script on a timer. None of those can succeed without "
            "a session, and each failed attempt buries the one thing the member needs "
            "to read: that you are waiting for them. A missing session is a WAIT, not "
            "a failure to work around.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
