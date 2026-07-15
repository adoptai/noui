---
name: quickbooks-bank-reconciliation
description: "Pull Acme Plumbing's Operating Checking general ledger LIVE from QuickBooks Online via Tabby execute/fetch on the quickbooks-sandbox profile, reconcile it against a bank statement with local Python scripts, and post the reconciling adjusting journal entry back into QuickBooks after human approval. Triggers on \"reconcile Operating Checking from QuickBooks\", \"bank reconciliation from QuickBooks\", \"month-end bank reconciliation\"."
---

# QuickBooks Bank Reconciliation (live)

Reconciles the **Operating Checking** account for Acme Plumbing by reading the ledger **live from QuickBooks** (Tabby `/execute/fetch`) and writing the reconciling change **back into QuickBooks**. Money moves in the system of record — this is the integration demo, not a spreadsheet tie-out.

## Prerequisites

1. Tabby is reachable (`TABBY_API_URL` / `TABBY_API_HOST`).
2. `TABBY_CLIENT_ID` and `TABBY_CLIENT_SECRET` are set.
3. ACTIVE Tabby profile **`quickbooks-sandbox`**, logged into the **Acme Plumbing** company.
4. Send only `accept` + `content-type` headers on QBO calls — never `intuit-company-id` etc. Auth/cookies/realm are attached by Tabby inside the browser session.
5. Per-user App Templates may require `platform_jwt` — see `skills/noui/references/auth-modes.md`.

## Steps

### 1 — Pull the Operating Checking ledger

```bash
python operations/list_transactions.py \
  --variables examples/pull_operating_checking.variables.json
```

Keep transactions that post to **Operating Checking** (money in = deposits, money out = checks).

⚠ **First live run:** confirm the account filter — the bank register may need `txnTypeFilter='bankingtxns'` in `with`, or an `account = '<accountId>'` clause in `filterBy`.

### 2a — Ask the user for the bank statement

**Always ask** for the bank statement for the period before reconciling. Accept CSV or pasted rows; normalise to `bank_ref,date,description,amount` and save as `bank_statement.csv` in the working directory (or `/workspace/bank_statement.csv` in a sandbox).

Only if the user declines, use the bundled sample under `assets/acme-plumbing/` **and flag it**.

### 2b — Normalise the pull for the reconciler

Write `gl_lines.json` (or `/workspace/gl_lines.json`):

```json
{
  "opening_balance": 600434.60,
  "as_of": "2025-12-31",
  "lines": [
    {"date": "2025-12-03", "type": "deposit", "amount": 258226.36, "memo": "...", "ref": "<qbo id>"},
    {"date": "2025-12-28", "type": "check", "amount": 1450.00, "memo": "...", "ref": "<qbo id>"}
  ]
}
```

### 3 — Reconcile

```bash
python scripts/run.py
```

Matches cleared deposits/checks between the ledger and the bank statement, classifies the rest, drafts the adjusting journal entry, and prints one JSON object. Check `gl_source` / `bank_source` — if either says "fallback", tell the user before presenting numbers as final.

### 4 — Approve (HITL)

Present the adjusting journal entry for approval. Do **not** post before the human approves. Timing items (outstanding checks, deposits in transit) self-correct and get **no** entry.

### 5 — Write the reconciling change back

**Primary — UI via Tabby `/execute/browser`:** navigate to
`https://sandbox.qbo.intuit.com/app/journal`; set date `2025-12-31`; line 1 **Operating Checking** Debits **1875.00**; line 2 **Undeposited Funds** Credits **1875.00**; memo as drafted; Save. Then re-run Step 1.

**Alternative — GraphQL mutation** (needs `x-csrf-token` on the profile allowlist):

```bash
python operations/create_journal_entry.py \
  --txn-date 2025-12-31 \
  --amount 1875.00 \
  --memo "Record ACH deposit not yet on the books (12/31 bank rec)" \
  --debit-account-id "<Operating Checking id>" \
  --credit-account-id "4"
```

Never fake a post — if nothing returned a real id, say the entry is approved and pending posting.

## Ground truth (sanity-check)

Book (GL) $641,055.06 · Bank $639,860.48 · deposits in transit $6,250.00 · outstanding checks $3,180.42 · unrecorded ACH **$1,875.00** · adjusted both sides $642,930.06 · difference after entry **$0.00**.

## Notes

- Bundled `assets/acme-plumbing/*.csv` is break-glass fallback only.
- Public Claude Code source of truth for this demo. Internal harness-shaped copy:
  `plans/handoffs/acme-plumbing-demo/skill/quickbooks-bank-reconciliation/`.
