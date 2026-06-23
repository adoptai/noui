#!/usr/bin/env python3
"""Register a compiled login result with Tabby (App + ServiceProfile).

    python scripts/activate_register.py compiled-login.json --promote

Requires TABBY_ADMIN_TOKEN. --promote moves STAGING → ACTIVE so the runtime
resolver (ACTIVE/CANARY only) can resolve the profile at the first tool call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from noui_core.activate import register


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("compiled", help="path to a compiled login result JSON")
    p.add_argument("--promote", action="store_true", help="promote STAGING → ACTIVE")
    p.add_argument("--as-template", dest="as_template", action="store_true", help="also create a tenant-wide App Template")
    args = p.parse_args()

    result = json.loads(Path(args.compiled).read_text())
    try:
        prov = register.register_login(result, promote=args.promote, as_template=args.as_template)
    except RuntimeError as exc:
        print(f"Register failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(prov, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
