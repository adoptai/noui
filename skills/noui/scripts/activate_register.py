#!/usr/bin/env python3
"""Register a compiled login result with Tabby as a tenant-wide App Template.

    python scripts/activate_register.py compiled-login.json

Template-first: creates the App Template only (POST /admin/app-templates). Tabby
auto-provisions a private per-user App+Profile (→ ACTIVE) on each member's first
request — no direct App/Profile creation, no promote. Editor role suffices
(TABBY_ADMIN_TOKEN locally; the broker forwards the user's federated bearer).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from noui_core import auth
from noui_core.activate import register
from noui_core.capture.recording import resolve_agent_token


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("compiled", help="path to a compiled login result JSON")
    p.add_argument(
        "--tenant-id",
        dest="tenant_id",
        default="",
        help="Admin-only: register in this tenant (default: the agent token's tenant)",
    )
    args = p.parse_args()

    # Default to the agent token's tenant so the agent can resolve/drive the result.
    tenant_id = args.tenant_id
    if not tenant_id:
        try:
            tenant_id = auth.tenant_id_from_token(resolve_agent_token())
        except RuntimeError:
            tenant_id = ""  # no agent creds → admin's own tenant

    result = json.loads(Path(args.compiled).read_text())
    try:
        prov = register.register_login(result, tenant_id=tenant_id)
    except RuntimeError as exc:
        print(f"Register failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(prov, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
