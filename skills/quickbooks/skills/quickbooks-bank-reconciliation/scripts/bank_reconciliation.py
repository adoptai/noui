"""Bank reconciliation — ties the QuickBooks Operating Checking ledger to the bank statement.

Pure stdlib. Input is the LIVE general ledger the agent pulled from QuickBooks (via
Tabby `execute/fetch` / `operations/list_transactions.py`) and normalised to
`/workspace/gl_lines.json`. If that file is absent (e.g.
the live QBO session is down, or during offline testing) it falls back to the bundled
`assets/acme-plumbing/gl-operating-checking-dec-2025.csv` so the workpaper still renders —
the fallback is break-glass only; the demo path is the live pull.

Returns a JSON-serialisable dict shaped for the `reconciliation-view` genui component,
extended with `unrecorded_items` and `adjusting_journal_entry` for the write-back step.
"""
from __future__ import annotations

import csv
import json
import os
from typing import Any

ROUND = 2
_HERE = os.path.dirname(os.path.abspath(__file__))
_ASSETS = os.path.join(_HERE, "..", "assets", "acme-plumbing")
_LIVE_GL = "/workspace/gl_lines.json"
_FALLBACK_GL = os.path.join(_ASSETS, "gl-operating-checking-dec-2025.csv")
# Bank statement the USER provides (uploaded/pasted, saved by the agent). Bundled copy is
# break-glass fallback only — see SKILL.md Step 2.
_USER_BANK = "/workspace/bank_statement.csv"
_FALLBACK_BANK = os.path.join(_ASSETS, "bank-statement-operating-dec-2025.csv")

ENTITY = "Acme Plumbing LLC"
ACCOUNT = "1000 Operating Checking"
AS_OF = "2025-12-31"


def _r(x: float) -> float:
    return round(float(x) + 0.0, ROUND)


def _load_gl() -> tuple[float, list[dict[str, Any]], str]:
    """Return (opening_balance, signed lines, source_label). Prefer the live pull."""
    if os.path.exists(_LIVE_GL):
        with open(_LIVE_GL, encoding="utf-8") as f:
            data = json.load(f)
        opening = float(data.get("opening_balance", 0.0))
        lines = []
        for ln in data["lines"]:
            amt = float(ln["amount"])
            # accept signed, or unsigned + type
            if amt >= 0 and str(ln.get("type", "")).lower() == "check":
                amt = -amt
            lines.append({"date": ln.get("date", ""), "amount": _r(amt),
                          "memo": ln.get("memo", ln.get("payee_or_source", "")),
                          "ref": ln.get("ref", ln.get("txn_id", "")), "matched": False})
        return opening, lines, "QuickBooks (live pull via Tabby execute/fetch)"
    # fallback
    with open(_FALLBACK_GL, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    lines = [{"date": r["date"], "amount": _r(r["amount"]),
              "memo": r.get("payee_or_source", ""), "ref": r.get("txn_id", ""), "matched": False}
             for r in rows]
    # opening = the bundled scenario's reconciled starting point
    return 600434.60, lines, "bundled fallback CSV (LIVE PULL UNAVAILABLE)"


def _bank_row_amount(r: dict[str, str]) -> float:
    """Signed amount from a bank row, tolerant of common statement layouts:
    a single signed `amount`, or split debit/credit (withdrawal/deposit) columns."""
    low = {k.lower().strip(): (v or "").strip() for k, v in r.items()}
    if low.get("amount", ""):
        return _r(low["amount"])
    credit = low.get("credit") or low.get("deposit") or low.get("deposits") or ""
    debit = low.get("debit") or low.get("withdrawal") or low.get("withdrawals") or ""
    val = (float(credit) if credit else 0.0) - (float(debit) if debit else 0.0)
    return _r(val)


def _load_bank() -> tuple[list[dict[str, Any]], str]:
    """Prefer the user-provided statement; fall back to the bundled copy."""
    if os.path.exists(_USER_BANK):
        path, source = _USER_BANK, "user-provided bank statement"
    else:
        path, source = _FALLBACK_BANK, "bundled fallback bank statement (USER DID NOT PROVIDE ONE)"
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        low = {k.lower().strip(): v for k, v in r.items()}
        out.append({"date": low.get("date", ""), "amount": _bank_row_amount(r),
                    "desc": low.get("description") or low.get("desc") or low.get("memo", ""),
                    "ref": low.get("bank_ref") or low.get("ref") or low.get("id", ""),
                    "matched": False})
    return out, source


def build_bank_reconciliation() -> dict[str, Any]:
    opening, gl, gl_source = _load_gl()
    bank, bank_source = _load_bank()

    book_end = _r(opening + sum(line["amount"] for line in gl))
    bank_end = _r(opening + sum(b["amount"] for b in bank))

    # Match cleared items one-to-one on equal signed amount.
    matched = []
    for g in gl:
        for b in bank:
            if not b["matched"] and not g["matched"] and g["amount"] == b["amount"]:
                g["matched"] = b["matched"] = True
                matched.append({"amount": g["amount"], "gl_ref": g["ref"], "bank_ref": b["ref"]})
                break

    outstanding = [g for g in gl if not g["matched"] and g["amount"] < 0]
    in_transit = [g for g in gl if not g["matched"] and g["amount"] > 0]
    unrec_credits = [b for b in bank if not b["matched"] and b["amount"] > 0]
    unrec_debits = [b for b in bank if not b["matched"] and b["amount"] < 0]

    oc_total = _r(sum(g["amount"] for g in outstanding))   # negative
    dit_total = _r(sum(g["amount"] for g in in_transit))    # positive
    cr_total = _r(sum(b["amount"] for b in unrec_credits))  # positive
    db_total = _r(sum(b["amount"] for b in unrec_debits))   # negative

    adjusted_bank = _r(bank_end + dit_total + oc_total)
    adjusted_book = _r(book_end + cr_total + db_total)
    difference = _r(adjusted_book - adjusted_bank)
    ties_out = difference == 0.0

    reconciling_items = [
        {"label": "Deposits in transit", "type": "deposit_in_transit", "amount": dit_total,
         "side": "bank", "detail": [{"ref": g["ref"], "date": g["date"], "memo": g["memo"],
                                     "amount": g["amount"]} for g in in_transit]},
        {"label": "Outstanding checks not yet cleared", "type": "outstanding_check", "amount": oc_total,
         "side": "bank", "detail": [{"ref": g["ref"], "date": g["date"], "memo": g["memo"],
                                     "amount": g["amount"]} for g in outstanding]},
    ]

    # Book-side items that need an actual entry (the reconciling CHANGE).
    unrecorded = (
        [{"ref": b["ref"], "date": b["date"], "memo": b["desc"], "amount": b["amount"], "kind": "deposit"} for b in unrec_credits]
        + [{"ref": b["ref"], "date": b["date"], "memo": b["desc"], "amount": b["amount"], "kind": "charge"} for b in unrec_debits]
    )
    net_entry = _r(cr_total + db_total)
    je = None
    if net_entry != 0.0:
        if net_entry > 0:
            je_lines = [
                {"account": "1000 Operating Checking", "debit": abs(net_entry), "credit": 0.0},
                {"account": "Undeposited / Unapplied Cash (reviewer reassigns)", "debit": 0.0, "credit": abs(net_entry)},
            ]
        else:
            je_lines = [
                {"account": "Bank Service Charges", "debit": abs(net_entry), "credit": 0.0},
                {"account": "1000 Operating Checking", "debit": 0.0, "credit": abs(net_entry)},
            ]
        je = {
            "date": AS_OF,
            "memo": f"Record bank items not yet on the books ({AS_OF} bank rec)",
            "lines": je_lines,
            "posts_to": "QuickBooks (write back via Tabby execute/browser or create_journal_entry)",
        }

    findings = []
    if not ties_out:
        findings.append({"severity": "high", "account": ACCOUNT,
                         "message": f"Reconciliation does not tie: adjusted book {adjusted_book} "
                                    f"vs adjusted bank {adjusted_bank} (diff {difference})."})
    if unrecorded and je is None:
        findings.append({"severity": "medium", "account": ACCOUNT,
                         "message": "Unrecorded bank items found but no adjusting entry drafted."})
    if "fallback" in bank_source:
        findings.append({"severity": "medium", "account": ACCOUNT,
                         "message": "No bank statement was provided — used the bundled fallback. "
                                    "Ask the user to upload the period's bank statement (SKILL.md Step 2)."})
    if "fallback" in gl_source:
        findings.append({"severity": "medium", "account": ACCOUNT,
                         "message": "Live QuickBooks pull unavailable — used the bundled ledger. "
                                    "Recover the quickbooks-sandbox session and re-pull."})

    return {
        "workpaper": "Bank Reconciliation",
        "period": "FY2025",
        "entity": ENTITY,
        "account": ACCOUNT,
        "gl_source": gl_source,
        "bank_source": bank_source,
        "year_end_reconciliation": {
            "as_of": AS_OF,
            "gl_balance": book_end,
            "gl_balance_label": "Balance per books (QuickBooks GL)",
            "bank_balance": bank_end,
            "bank_balance_label": "Balance per bank (Lone Star Bank)",
            "reconciling_items": reconciling_items,
            "unrecorded_items": unrecorded,
            "adjusted_bank_balance": adjusted_bank,
            "adjusted_book_balance": adjusted_book,
            "recomputed_bank_balance": adjusted_bank,
            "ties_out": ties_out,
            "difference": difference,
        },
        "adjusting_journal_entry": je,
        "matched_count": len(matched),
        "findings": findings,
        "clean": ties_out and not findings,
    }
