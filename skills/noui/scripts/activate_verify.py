#!/usr/bin/env python3
"""Verify a generated MCP server's auth before use (deterministic-first dry-run).

    python scripts/activate_verify.py workbench/mcp_servers/<app>/<server_id>

PASS → safe to install/use. NEEDS_SECRET / UNSUPPORTED → see the printed reason.
"""

from __future__ import annotations

import argparse
import asyncio

import _bootstrap  # noqa: F401

from noui_core.activate.verify import verify_before_install
from noui_core.config import settings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("server_dir", help="path to a generated MCP server directory")
    args = p.parse_args()

    result = asyncio.run(
        verify_before_install(
            args.server_dir,
            tabby_api_host=settings.tabby_api_host,
            tabby_admin_token=settings.tabby_admin_token,
        )
    )
    print(f"{result.status}: {result.message}")
    return 0 if result.status in ("PASS", "REPAIR_APPLIED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
