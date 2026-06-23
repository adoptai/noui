#!/usr/bin/env python3
"""Compile a saved login bundle JSON into Tabby App + ServiceProfile drafts.

    python scripts/compile_login.py bundle.json --out compiled-login.json

Writes the compiled drafts; register them with scripts/activate_register.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from noui_core.compile.login import compile_login_bundle


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bundle", help="path to a login recording bundle JSON")
    p.add_argument("--session-id", dest="session_id", default="")
    p.add_argument("--name", default="")
    p.add_argument("--url", default="", help="login URL; else inferred from URL flow")
    p.add_argument(
        "--auth-mode",
        dest="auth_mode",
        choices=["agent_token", "platform_jwt"],
        default="agent_token",
    )
    p.add_argument("--out", default="", help="write compiled drafts here (default: stdout)")
    args = p.parse_args()

    bundle = json.loads(Path(args.bundle).read_text())
    session_id = args.session_id or bundle.get("session_id") or "bundle"
    try:
        compiled = compile_login_bundle(
            session_id=session_id,
            bundle=bundle,
            name=args.name,
            login_url=args.url,
            auth_mode=args.auth_mode,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Compile failed: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(compiled, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
