# QuickBooks plugin

Claude Code plugin for QuickBooks Online demos that run through Tabby's
`POST /execute/fetch` (and `/execute/browser` for CSRF-protected writes).

## Skills

| Skill | Profile slug | Notes |
|---|---|---|
| `quickbooks-bank-reconciliation` | `quickbooks-sandbox` | Live GL pull + local reconcile scripts + JE write-back |

Further QBO skills (expenses, AP/AR) can be added under `skills/` later.

## Install

```bash
claude plugin install ./skills/quickbooks
```

(If your Claude Code build uses the older verb, try `claude plugins add ./skills/quickbooks`.)

## Prerequisites

1. Reachable Tabby + `TABBY_CLIENT_ID` / `TABBY_CLIENT_SECRET`.
2. ACTIVE `quickbooks-sandbox` profile logged into the target company.
3. Per-user App Templates may require `platform_jwt` auth mode — see
   [`../noui/references/auth-modes.md`](../noui/references/auth-modes.md).

## Internal counterpart

The Agent Harness–shaped Acme demo remains under
`plans/handoffs/acme-plumbing-demo/skill/quickbooks-bank-reconciliation/`.
This plugin is the **public** Claude Code source of truth.
