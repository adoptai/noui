#!/usr/bin/env python3
"""Autopilot capture: drive a profile's Tabby session and compile the result.

Scripted run from a steps file, then compile to MCP/Skill:

    python scripts/capture_autopilot.py <profile-slug> --steps steps.json --as both

steps.json is a list of actions, e.g.:
    [{"action": "navigate", "url": "https://example.com/search?q=foo"},
     {"action": "click", "selector": "#result-0"}]

For interactive driving (the agent issues commands one at a time), import
noui_core.capture.autopilot.AutopilotSession directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from noui_core.capture.autopilot import run_steps
from noui_core.capture.validate import validate_har_dict
from noui_core.compile.workflow import compile_workflow_bundle


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("profile_slug", help="Tabby profile slug whose session to drive")
    p.add_argument("--steps", required=True, help="JSON file: list of {action, ...} steps")
    p.add_argument("--name", default="", help="name for the generated asset")
    p.add_argument("--as", dest="target", choices=["mcp", "skill", "both"], default="mcp")
    p.add_argument("--execution-mode", dest="execution_mode", choices=["tabby", "http", "harness"], default="tabby")
    p.add_argument("--save-bundle", default="", help="also write the synthesized bundle here")
    args = p.parse_args()

    steps = json.loads(Path(args.steps).read_text())
    if not isinstance(steps, list):
        print("steps file must contain a JSON list of actions", file=sys.stderr)
        return 1

    try:
        bundle = run_steps(args.profile_slug, steps)
    except (RuntimeError, ValueError, KeyError) as exc:
        print(f"Autopilot run failed: {exc}", file=sys.stderr)
        return 1

    report = validate_har_dict(bundle.get("har", {}))
    print(f"Captured {report.api_call_count} API call(s) across {len(report.domains)} domain(s).", file=sys.stderr)
    for w in report.warnings:
        print(f"  {w}", file=sys.stderr)
    if not report.passed:
        print("HAR did not pass validation — aborting compile.", file=sys.stderr)
        return 1

    if args.save_bundle:
        Path(args.save_bundle).write_text(json.dumps(bundle, indent=2) + "\n")

    name = args.name or f"autopilot-{args.profile_slug}"
    result = compile_workflow_bundle(
        session_id=name,
        bundle=bundle,
        name=name,
        target=args.target,
        profile_slug=args.profile_slug,
        execution_mode=args.execution_mode,
    )
    print(json.dumps({k: v.get("server_id") or v.get("skill_id") for k, v in result.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
