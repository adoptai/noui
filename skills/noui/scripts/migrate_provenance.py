#!/usr/bin/env python3
"""Attach a recording to a browser skill compiled before provenance existed.

    python scripts/migrate_provenance.py workbench/skills/<app>
    python scripts/migrate_provenance.py workbench/skills/<app> --bundle <path>

Browser skills compiled before the installer started checking provenance carry
no `manifest.provenance` and no `recording_bundle.json`, so they are refused on
their next install. They were compiled from a real recording, and capture always
saves the bundle, so the fix is to re-attach the recording they already have --
no re-recording, and no re-compiling.

THIS IS NOT A WAY TO BLESS A SKILL. It runs the same check the installer runs:
every locator the operations drive must appear in the recording. A skill written
by hand fails it, because its selectors were never in any bundle -- pointing this
at an arbitrary recording will not make one pass. That is the point of having it
verify rather than just stamp.

Finds the bundle by the session id in the manifest when --bundle is omitted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from noui_core.compile import provenance
from noui_core.config import settings


def _bundles_dir() -> Path:
    return Path(settings.workbench_dir) / "bundles"


def find_bundle(manifest: dict, explicit: str = "") -> tuple[Path | None, str]:
    """The recording this skill was compiled from, and why we think so."""
    if explicit:
        p = Path(explicit)
        return (p, "given with --bundle") if p.is_file() else (None, f"{p} does not exist")

    session_id = str((manifest.get("workflow") or {}).get("workflow_session_id") or "")
    root = _bundles_dir()
    if not root.is_dir():
        return None, f"no bundles directory at {root}"
    candidates = sorted(root.glob("*.json"))
    if not candidates:
        return None, f"no saved bundles in {root}"

    if session_id:
        for path in candidates:
            try:
                if str(json.loads(path.read_text()).get("session_id") or "") == session_id:
                    return path, f"session id {session_id} matches the manifest"
            except (OSError, ValueError):
                continue
        return None, (
            f"no saved bundle has session id {session_id} — the capture may have been "
            f"pruned. Pass --bundle explicitly, or re-record."
        )
    return None, "the manifest names no workflow_session_id; pass --bundle explicitly"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("skill_dir", help="compiled browser skill directory")
    p.add_argument(
        "--bundle", default="", help="recording bundle JSON (default: find by session id)"
    )
    p.add_argument("--dry-run", action="store_true", help="report what would happen, write nothing")
    args = p.parse_args()

    skill_dir = Path(args.skill_dir)
    try:
        manifest = json.loads((skill_dir / "manifest.json").read_text(encoding="utf-8"))
        ops_doc = json.loads((skill_dir / "operations.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Cannot read the skill at {skill_dir}: {exc}", file=sys.stderr)
        return 1

    if (manifest.get("runtime") or {}).get("operation_style") != "browser":
        print(
            "This is not a browser skill — provenance is only checked for skills that "
            "drive a live browser, so there is nothing to migrate.",
            file=sys.stderr,
        )
        return 1

    if manifest.get("provenance") and (skill_dir / provenance.BUNDLE_FILE).is_file():
        print("Already has provenance and a recording beside it — nothing to do.")
        return 0

    bundle_path, why = find_bundle(manifest, args.bundle)
    if bundle_path is None:
        print(
            f"No recording to attach: {why}.\n"
            "\n"
            "Without the bundle this skill was compiled from, there is nothing that "
            "proves its steps were ever observed. Re-record the workflow and compile "
            "again — that is the only honest way back.",
            file=sys.stderr,
        )
        return 1

    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Cannot read {bundle_path}: {exc}", file=sys.stderr)
        return 1

    operations = ops_doc.get("operations") or []
    unobserved = provenance.unobserved_locators(operations, bundle)
    if unobserved:
        shown = ", ".join(repr(u) for u in unobserved[:5])
        more = f" (and {len(unobserved) - 5} more)" if len(unobserved) > 5 else ""
        print(
            f"Refusing to attach {bundle_path.name}: these locators do not appear in "
            f"it: {shown}{more}.\n"
            "\n"
            "Either this is the wrong recording — try --bundle with another — or the "
            "operations were not compiled from any recording, in which case attaching "
            "one would only disguise that. Re-record and compile.",
            file=sys.stderr,
        )
        return 1

    prov = provenance.build(bundle, bundle_file=provenance.BUNDLE_FILE)
    if args.dry_run:
        print(f"Would attach {bundle_path} ({why}).")
        print(json.dumps(prov, indent=2))
        return 0

    (skill_dir / provenance.BUNDLE_FILE).write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    manifest["provenance"] = prov
    (skill_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Attached {bundle_path.name} ({why}).\n"
        f"  {prov['recorded_events']} recorded interactions, "
        f"{prov['observed_selectors']} observed locators, all steps accounted for.\n"
        "The skill installs again. Its approval is unaffected — the operations did "
        "not change, so the fingerprint still matches."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
