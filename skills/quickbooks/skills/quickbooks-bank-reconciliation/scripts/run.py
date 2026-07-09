"""Bank reconciliation runner — ties the QuickBooks Operating Checking ledger to the bank.

No stdin. Emits a single JSON object on stdout, shaped for the `reconciliation-view` genui
component. Reads the live GL the agent pulled to `/workspace/gl_lines.json` (or the bundled
fallback CSV), plus the bank statement in `assets/acme-plumbing/`.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bank_reconciliation import build_bank_reconciliation  # noqa: E402


def main() -> int:
    result = build_bank_reconciliation()
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
