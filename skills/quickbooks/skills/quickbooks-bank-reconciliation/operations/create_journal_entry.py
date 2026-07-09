#!/usr/bin/env python3
"""Post a QBO journal entry via Tabby execute/fetch (GraphQL mutation).

Requires x-csrf-token (and related headers) on the quickbooks-sandbox profile
allowlist. Prefer execute_browser UI fill if this returns 400.
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


def _fill_template(text: str, mapping: dict[str, str]) -> str:
    out = text
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return out


async def execute(
    txn_date: str,
    amount: str,
    memo: str,
    debit_account_id: str,
    credit_account_id: str,
    profile_slug: str | None = None,
) -> dict:
    query = (_ROOT / "examples" / "create_journal_entry.graphql").read_text(encoding="utf-8")
    template = (_ROOT / "examples" / "create_journal_entry.variables.template.json").read_text(
        encoding="utf-8"
    )
    variables = json.loads(
        _fill_template(
            template,
            {
                "TXN_DATE": txn_date,
                "AMOUNT": amount,
                "MEMO": memo,
                "DEBIT_ACCOUNT_ID": debit_account_id,
                "CREDIT_ACCOUNT_ID": credit_account_id,
            },
        )
    )
    body = {
        "operationName": "UpdateTransactions_Transaction_qbo",
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
    p = argparse.ArgumentParser(prog="create_journal_entry")
    p.add_argument("--txn-date", dest="txn_date", required=True, help="YYYY-MM-DD")
    p.add_argument("--amount", required=True)
    p.add_argument("--memo", required=True)
    p.add_argument("--debit-account-id", dest="debit_account_id", required=True)
    p.add_argument("--credit-account-id", dest="credit_account_id", required=True)
    p.add_argument("--profile-slug", dest="profile_slug", default=None)
    args = p.parse_args(argv)
    try:
        result = asyncio.run(
            execute(
                txn_date=args.txn_date,
                amount=args.amount,
                memo=args.memo,
                debit_account_id=args.debit_account_id,
                credit_account_id=args.credit_account_id,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"create_journal_entry failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
