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
from noui_core.compile import amendments as amendments_mod
from noui_core.compile import provenance
from noui_core.tabby_client import TabbyUnreachableError
from noui_core.verify.replay import plan_operations
from noui_core.verify.session import run_replay

REPORT_FILE = "replay_report.json"


def _read_text(path: Path) -> str:
    """File contents, or "" when absent."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _summarize(report: dict, report_path: Path) -> str:
    """A short, complete-enough account of the replay for the caller to act on.

    This used to print the whole report -- every operation, every step -- as
    indented JSON, while ALSO writing it to replay_report.json. For a two
    operation skill that is already 8KB of duplicate output, so an agent caller
    reading it as context did the rational thing and piped it (`| tail -5`,
    `| python3 -c ...`), which truncated away the per-operation results it then
    had to reason about.

    Printing the outcome instead removes the reason to pipe. What it must carry
    is set by what the caller does next, and by what the CARD shows the member:

    * The headline is ``goals`` -- the endpoint the member asked for, which is
      what the card headlines. ``all_goals_reached`` is a stricter AND across
      every operation and also trips on an intermediate submit that blocked
      after the download arrived, so leading with it would print INCOMPLETE over
      a card saying the goal was reached.
    * A report-level ``status``/``detail`` (e.g. ``not_replayable``) is the whole
      answer when present -- without it a segment-mixture report summarised to
      "0/0 operations" and exit 0, with the actionable line only in the file.
    * ``skipped`` and ``recovered`` are not failures. Counting them made a clean
      replay print "first failure at step 1".
    * ``needs_approval`` is a hold with a remedy (``--approve-step``), not a
      breakage; SKILL.md requires the caller to surface it. Rendering it
      identically to a broken locator hides the fix.

    Pass --json for the whole report.
    """
    lines: list[str] = []

    # A short-circuit report is the WHOLE answer. Trigger on `detail` too, not
    # just `status`: run_replay's empty-ops branch (session.py, `if not ops`)
    # sets detail and NO status, so a browser/non-browser skill replayed with
    # --only <non-browser-op> fell through to the generic "0/0 operation(s)"
    # line and silently dropped "no browser operations to replay". That is the
    # same class this function exists to fix, and losing it was a regression
    # against the old always-dump behaviour. Step-level detail lives on steps
    # (_replay_step_inner), never here, so this cannot fire on a normal replay.
    status = str(report.get("status") or "")
    detail = str(report.get("detail") or "")
    if detail or (status and status != "ok"):
        head = f"Replay {status.upper()}." if status and status != "ok" else "Replay did not run."
        lines.append(f"{head}" + (f" {detail}" if detail else ""))
        lines.append(f"Full report: {report_path}")
        return "\n".join(lines)

    ops = report.get("operations") or []
    goals = report.get("goals") or []
    if goals:
        reached = sum(1 for g in goals if g.get("reached"))
        head = "Replay OK" if reached == len(goals) else "Replay INCOMPLETE"
        lines.append(f"{head} -- {reached}/{len(goals)} goal(s) reached.")
    else:
        reached = sum(1 for o in ops if o.get("goal_reached"))
        head = "Replay OK" if report.get("all_goals_reached") else "Replay INCOMPLETE"
        lines.append(f"{head} -- {reached}/{len(ops)} operation(s) reached their goal.")

    for o in ops:
        name = o.get("name") or "?"
        steps = o.get("steps") or []
        # skipped/recovered are not failures; blocked and needs_approval are
        # reported by name below rather than folded into a step index.
        broken = [
            i for i, st in enumerate(steps) if str(st.get("status") or "") in ("error", "failed")
        ]
        mark = "PASS" if o.get("goal_reached") else "FAIL"
        detail_bits = [f"{len(steps)} step(s)"]
        if broken:
            detail_bits.append(f"first error at step {broken[0]}")
        if o.get("blocked_count"):
            detail_bits.append(f"{o['blocked_count']} blocked")
        if o.get("needs_approval_count"):
            detail_bits.append(f"{o['needs_approval_count']} awaiting approval")
        lines.append(f"  [{mark}] {name} -- {', '.join(detail_bits)}")

    if report.get("needs_approval"):
        lines.append(
            "  Some steps are HELD for approval, not broken: verify_approve.py "
            "--approve-step, or re-record if the step should not be there."
        )
    if report.get("partial"):
        lines.append(
            f"  PARTIAL: only {', '.join(report['partial'])} ran; this is not a full replay."
        )
    lines.append(f"Full report: {report_path}")
    lines.append(
        "The member sees this on the replay card -- do not summarise it back to "
        "them as approved, and do not approve it yourself."
    )
    return "\n".join(lines)


def _step_ran_ok(report: dict, operation: str, step_index: int) -> bool:
    """Did this exact step run, and run cleanly, in the replay just performed?

    Missing is not ok: when a replay dies early an operation has fewer step
    results than steps, and every step past the failure simply never happened.
    """
    for op in report.get("operations") or []:
        if op.get("name") != operation:
            continue
        steps = op.get("steps") or []
        if 0 <= step_index < len(steps):
            return str(steps[step_index].get("status") or "") == "ok"
    return False


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
    p.add_argument(
        "--amend",
        action="append",
        default=[],
        metavar="JSON",
        help="replace ONE step with a control discovered at replay, as a JSON object: "
        '{"operation": "...", "step_index": 0, "replacement": {"command": "...", '
        '"params": {...}}, "why": "..."}. Use this instead of editing '
        "operations.json -- editing it destroys the provenance that makes the whole "
        "skill installable, and an amendment recorded here survives review as what it "
        "is: a step observed working once, which the member approves separately.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="print the full report JSON instead of the summary (the report is "
        "written to replay_report.json either way)",
    )
    p.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="OPERATION",
        help="replay just these operations, from wherever the browser already is. "
        "For iterating on one operation without re-walking the journey to reach "
        "it -- the run is NOT evidence the skill works end to end, and its report "
        "says so.",
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

    # Check provenance BEFORE spending a live session on it. The installer checks
    # this too, but by then a replay has already run: one build rewrote the
    # compiled operations, replayed, and spent the whole session improvising --
    # clicking, screenshotting, hunting a nav that its invented steps could not
    # find -- before anything said the steps were not the recorded ones.
    # The steps digest first, and OUTSIDE the bundle check: a rewrite is knowable
    # from the manifest alone, and a build that had rewritten its operations was
    # sent to replay them -- spending a live session, and a member's sign-in, on
    # steps that could never install.
    try:
        manifest = json.loads((skill_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    stamped = str((manifest.get("provenance") or {}).get("steps_sha256") or "")
    if stamped and stamped != provenance.steps_digest(operations):
        print(
            "These are not the operations that were compiled from the recording "
            "— their steps changed after compiling.\n"
            "\n"
            "Do NOT replay them: they cannot install however the replay goes, and "
            "a live session spent on them is spent for nothing.\n"
            "\n"
            "Renaming an operation, rewording a description, or adding a parameter "
            "is fine. These are not: changing what a step targets, reordering "
            "steps, adding an operation, and REMOVING one — deleting an operation "
            "you judged unnecessary changes the digest exactly as much as inventing "
            "one, because the digest covers the whole list. If some operations look "
            "like noise, leave them; one nobody calls costs nothing, and SKILL.md "
            "is where you say which ones matter.\n"
            "\n"
            "Restore the compiled operations.json, or re-record the part you meant "
            "to change.",
            file=sys.stderr,
        )
        return 1

    bundle_path = skill_dir / provenance.BUNDLE_FILE
    if bundle_path.is_file():
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"Cannot read {bundle_path}: {exc}", file=sys.stderr)
            return 1

        # The dir's bundle MUST be the one these operations were sealed from.
        # A re-import, or a recompile against a different capture, can leave
        # operations.json and recording_bundle.json in the dir out of sync -- and
        # then unobserved_locators below reads the WRONG bundle and reports the
        # operations' real locators as "controls the recording never saw", which
        # reads as a bad recording and sends the build off to re-record. Catch the
        # mismatch here, first, and name it for what it is.
        if not provenance.bundle_matches_manifest(bundle, manifest):
            stamped = str((manifest.get("provenance") or {}).get("bundle_sha256") or "")
            print(
                "SKILL DIRECTORY IS INCONSISTENT -- a COMPILE/IMPORT problem, NOT a "
                "recording problem and NOT hand-editing.\n"
                "\n"
                "The recording_bundle.json in this skill dir is not the one these "
                "operations were compiled from: its checksum does not match the "
                f"manifest's bundle_sha256 ({stamped[:12]}...). operations.json and "
                "recording_bundle.json came from different compile/import runs.\n"
                "\n"
                "DO NOT re-record -- the recording is fine; the wrong bundle is in the "
                "dir. Restore the recording_bundle.json whose checksum matches the "
                "manifest, or re-import ONCE so operations.json + recording_bundle.json "
                "+ manifest.json all come from a single compile. (The 'controls the "
                "recording never saw' error you would hit next is a symptom of this, "
                "not a real fabrication.)",
                file=sys.stderr,
            )
            return 1

        unbacked = provenance.unbacked_control_steps(operations)
        if unbacked:
            shown = ", ".join(repr(u) for u in unbacked[:5])
            more = f" (and {len(unbacked) - 5} more)" if len(unbacked) > 5 else ""
            print(
                f"These steps drive a control the recording could never have backed: {shown}{more}.\n"
                "\n"
                "A coordinate click or a key-press names no control the recording saw, so "
                "nothing ties it to what was captured. The compiler never emits these; their "
                "presence means the operations were written or edited by hand. Restore the "
                "compiled operations.json, or re-record the part you meant to change.",
                file=sys.stderr,
            )
            return 1

        unobserved = provenance.unobserved_locators(operations, bundle)
        if unobserved:
            shown = ", ".join(repr(u) for u in unobserved[:5])
            more = f" (and {len(unobserved) - 5} more)" if len(unobserved) > 5 else ""
            print(
                f"These steps target controls the recording never saw: {shown}{more}.\n"
                "\n"
                "The bundle checksum already matched the manifest, so this is NOT a "
                "swapped bundle -- these operations genuinely diverge from THIS "
                "recording. That almost always means they were edited by hand after "
                "compiling. Replaying now would drive a live session through steps "
                "nobody has seen work, and when they miss, the run improvises and "
                "wanders.\n"
                "\n"
                "You may rename an operation, reword its description, or add a "
                "parameter. You may NOT change what a step targets, reorder steps, "
                "add an operation that was not recorded, or remove one you judged "
                "unnecessary -- those come from the recording and nothing else can "
                "supply them. Restore the compiled operations.json, or re-record the "
                "part you meant to change.",
                file=sys.stderr,
            )
            return 1

    # Narrow the run AFTER the provenance gates, never before.
    #
    # Filtering first made the digest check see a skill with four operations
    # removed, and it refused -- correctly in its own terms, since removing an
    # operation changes the digest exactly as much as inventing one. But --only
    # is a choice about what to EXERCISE, not a change to what was compiled, and
    # the gates must judge the file on disk. The other ordering is worse: it
    # would let a genuinely edited skill through by passing --only.
    if operations and args.only:
        wanted = {str(n) for n in args.only}
        missing = wanted - {str(o.get("name")) for o in operations}
        if missing:
            print(
                f"No operation named {', '.join(sorted(missing))} in this skill.",
                file=sys.stderr,
            )
            return 1
        operations = [o for o in operations if str(o.get("name")) in wanted]
        print(
            f"Replaying {len(operations)} of {len(doc['operations'])} operations, from "
            f"wherever the browser is. This is for iterating, not for approval: a "
            f"partial run cannot show the skill works end to end.",
            file=sys.stderr,
        )

    # Amendments: a step whose recorded locator no longer matches, replaced by a
    # control discovered at replay. Recorded HERE rather than by editing
    # operations.json, which is what three builds did -- destroying the digest
    # that proves the rest came from a recording, and losing the discovery along
    # with the evidence when the gate then refused the skill.
    pending: list[dict] = []
    for raw in args.amend:
        try:
            a = json.loads(raw)
        except ValueError as exc:
            print(f"--amend expects a JSON object, got {raw[:60]!r}: {exc}", file=sys.stderr)
            return 1
        if not (isinstance(a, dict) and a.get("operation") and a.get("replacement")):
            print("--amend needs at least 'operation' and 'replacement'.", file=sys.stderr)
            return 1
        a.setdefault("why", "the recorded locator matched nothing at replay")
        pending.append(a)

    if pending:
        by_name = {o.get("name"): o for o in operations}
        for a in pending:
            op = by_name.get(a["operation"])
            if op is None:
                print(f"No operation named {a['operation']!r} to amend.", file=sys.stderr)
                return 1
            idx = a.get("step_index")
            steps = op.get("steps") or []
            if not isinstance(idx, int) or not (0 <= idx < len(steps)):
                print(
                    f"step_index {idx!r} is out of range for {a['operation']} "
                    f"({len(steps)} steps).",
                    file=sys.stderr,
                )
                return 1
            # Applied to the in-memory plan only. operations.json stays exactly as
            # compiled, so its digest keeps proving what the recording showed.
            steps[idx] = a["replacement"]

    values = {}
    for raw in args.param:
        name, _, value = raw.partition("=")
        if not name or not _:
            print(f"--param expects NAME=VALUE, got {raw!r}", file=sys.stderr)
            return 1
        values[name] = value

    # Not being able to replay is not a fault in the skill, and it must not cost
    # the member the amendment they just recorded or dump a raw traceback. Two
    # gates sit BEFORE run_replay can turn "no session" (HTTP 404/409) into
    # login_required, and either can fail with nothing having run:
    #   - resolving the agent token: no credentials/broker token configured, or
    #     the auth endpoint is unreachable. Any failure here means there is no
    #     token, so there is no session to replay against.
    #   - the very first browser call, when the control plane is down entirely
    #     (TabbyUnreachableError rather than a 404).
    # Treat both the same way: no replay ran, nothing reached its goal, the
    # amendments below are recorded unverified, and the message says plainly why.
    def _not_replayed() -> dict:
        return {
            "operations": [
                {"name": o.get("name"), "goal_reached": False, "steps": []} for o in operations
            ],
            "all_goals_reached": False,
            "needs_approval": False,
            "installable": False,
            "status": "unreachable",
        }

    try:
        token = resolve_agent_token()
    except RuntimeError as exc:
        # No token, no session — missing creds/broker token, or the auth endpoint
        # is down. resolve_agent_token raises ONLY for "cannot obtain a token"
        # reasons, so catching RuntimeError here cannot swallow a replay bug.
        print(str(exc), file=sys.stderr)
        report = _not_replayed()
    else:
        try:
            report = run_replay(
                operations,
                profile_slug=args.profile_slug,
                token=token,
                approvals=set(args.approve_step) or None,
                parameter_values=values or None,
                # Every invocation restarts the journey rather than resuming it, so
                # an amended step is judged from the same place the original one was.
                entry_url=(doc.get("entry_url") if isinstance(doc, dict) else None),
                # One entry per host: a reset never changes hosts, because no route
                # between them was ever recorded.
                entry_by_origin=(doc.get("entry_urls") if isinstance(doc, dict) else None),
            )
        except TabbyUnreachableError as exc:
            print(str(exc), file=sys.stderr)
            report = _not_replayed()

    # Amendments are persisted AFTER the replay, stamped with what it proved
    # about each one. Written beforehand they all looked equally confirmed: one
    # build verified a single control by hand, amended the same step across six
    # operations, and four of those were blocked long before the amended step ran
    # -- yet every one of them said "confirmed working in live session".
    if pending:
        for a in pending:
            a["verified"] = _step_ran_ok(report, a["operation"], a["step_index"])
        existing = amendments_mod.load(_read_text(skill_dir / amendments_mod.AMENDMENTS_FILE))
        by_id = {amendments_mod.amendment_id(a): a for a in existing}
        for a in pending:
            by_id[amendments_mod.amendment_id(a)] = a  # a later replay re-judges it
        (skill_dir / amendments_mod.AMENDMENTS_FILE).write_text(
            json.dumps({"amendments": list(by_id.values())}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        unverified = [a for a in pending if not amendments_mod.is_verified(a)]
        print(
            f"Recorded {len(pending)} amendment(s). These are NOT part of the "
            f"recording -- the member approves each one separately "
            f"(verify_approve --approve-amendment ID):",
            file=sys.stderr,
        )
        for a in pending:
            print(
                f"  [{amendments_mod.amendment_id(a)}] {amendments_mod.describe(a)}",
                file=sys.stderr,
            )
        if unverified:
            print(
                f"\n{len(unverified)} of them never ran. Confirming a control by hand "
                "on one page is not evidence for the same step in another operation "
                "the replay never reached -- fix what blocked those operations and "
                "replay again, rather than asking the member to approve a step "
                "nothing has exercised.",
                file=sys.stderr,
            )

    if args.only:
        # Stamped BEFORE the file is written, not after. Marking only the copy
        # printed to stdout left replay_report.json looking like a full run --
        # and that file is what the card renders and the installer reads, which
        # is how a skill gets approved on evidence that only ever covered one
        # operation.
        report["partial"] = sorted(str(n) for n in args.only)
        report["installable"] = False

    out = skill_dir / REPORT_FILE
    try:
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        wrote_report = True
    except OSError as exc:
        # The report still goes to stdout; losing the file is not worth failing a
        # replay that already ran against a live session.
        wrote_report = False
        print(f"(warning: could not write {out}: {exc})", file=sys.stderr)

    # Summary by default. The full report goes to stdout when asked for, and
    # ALWAYS when the file could not be written -- otherwise a failed write would
    # silently take the report with it, which is the one case the fallback above
    # exists for.
    if args.json or not wrote_report:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(_summarize(report, out))

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
    if report.get("status") == "unreachable":
        # The reason was already printed above. Non-zero so nothing downstream
        # reads this as a passing replay; same "re-run once the platform answers"
        # family as login_required.
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
