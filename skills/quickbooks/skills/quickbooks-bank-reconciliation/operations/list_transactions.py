#!/usr/bin/env python3
"""Pull QBO transactions via Tabby execute/fetch (GraphQL).

Reads examples/list_transactions.graphql and a variables JSON file, then
POSTs to sandbox.qbo.intuit.com/api/v4/graphql inside the Tabby browser session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from noui_runtime.execute import execute_fetch

DEFAULT_PROFILE = "quickbooks-sandbox"
GRAPHQL_URL = "https://sandbox.qbo.intuit.com/api/v4/graphql"
_ROOT = Path(__file__).resolve().parent.parent


def _profile_slug(override: str | None = None) -> str:
    return override or os.environ.get("PROFILE_SLUG") or DEFAULT_PROFILE


async def execute(
    variables_path: str,
    query_path: str | None = None,
    profile_slug: str | None = None,
) -> dict:
    query_file = Path(query_path) if query_path else _ROOT / "examples" / "list_transactions.graphql"
    vars_file = Path(variables_path)
    query = query_file.read_text(encoding="utf-8").strip()
    variables = json.loads(vars_file.read_text(encoding="utf-8"))

    body = {
        "query": query,
        "variables": variables,
    }
    return await execute_fetch(
        _profile_slug(profile_slug),
        GRAPHQL_URL,
        method="POST",
        body=body,
        headers={
            "accept": "*/*",
            "content-type": "application/json",
        },
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="list_transactions")
    p.add_argument(
        "--variables",
        required=True,
        help="Path to GraphQL variables JSON (e.g. examples/pull_operating_checking.variables.json)",
    )
    p.add_argument(
        "--query",
        default=None,
        help="Path to GraphQL query file (default: examples/list_transactions.graphql)",
    )
    p.add_argument("--profile-slug", dest="profile_slug", default=None)
    args = p.parse_args(argv)
    try:
        result = asyncio.run(
            execute(
                variables_path=args.variables,
                query_path=args.query,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"list_transactions failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
