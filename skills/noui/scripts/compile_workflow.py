#!/usr/bin/env python3
"""Compile a saved workflow bundle JSON into MCP and/or Skill assets.

    python scripts/compile_workflow.py bundle.json --as both --profile-slug <slug>

Use this to re-compile from a previously fetched bundle without re-recording.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from noui_core.compile.workflow import compile_workflow_bundle


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bundle", help="path to a workflow recording bundle JSON")
    p.add_argument("--session-id", dest="session_id", default="")
    p.add_argument("--name", default="")
    p.add_argument("--as", dest="target", choices=["mcp", "skill", "both"], default="mcp")
    p.add_argument("--profile-slug", dest="profile_slug", default="")
    p.add_argument(
        "--execution-mode", dest="execution_mode", choices=["tabby", "http", "harness"], default="tabby"
    )
    p.add_argument("--start-url", dest="start_url", default="")
    args = p.parse_args()

    bundle = json.loads(Path(args.bundle).read_text())
    session_id = args.session_id or bundle.get("session_id") or "bundle"
    try:
        result = compile_workflow_bundle(
            session_id=session_id,
            bundle=bundle,
            name=args.name,
            target=args.target,
            profile_slug=args.profile_slug,
            execution_mode=args.execution_mode,
            start_url=args.start_url,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Compile failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: v.get("server_id") or v.get("skill_id") for k, v in result.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
