# NoUI Skills Handoff Document

> Last updated by Claude Sonnet 4.6 on 2026-07-09. Resume here after a break or new session.

---

## What This Project Is

Building NoUI skills — each skill lets an AI agent interact with a real website's internal API directly (no computer-use, no DOM scraping). Skills are grouped into **domain-based plugins** (one PR per domain, not one PR per site).

**Current focus: finish migrating the Airline Scanner plugin to the new execute API, then pivot to Tax/Accounting skills.**

---

## Architecture Change (IMPORTANT — read first)

Gabriel deprecated CDP-based skills. **All new and updated skills must use the `/execute/fetch` + `/execute/browser` HTTP API, not CDP WebSocket.**

| Old (deprecated) | New (required) |
|---|---|
| `noui_runtime/cdp.py` | `noui_runtime/execute.py` |
| `cdp_fetch(ws_url, ...)` | `execute_fetch(profile_id, url, ...)` |
| `cdp_eval(ws_url, js)` | `execute_browser(profile_id, command, params)` |
| `find_page("airline.com")` → WebSocket URL | profile_id string passed directly |

**Reference implementation:** `skills/expedia-stay-search/` on `dev` branch — read this before writing any new skill.

The new `execute.py` runtime is at `skills/expedia-stay-search/noui_runtime/execute.py` on `dev`. Copy it into any new skill's `noui_runtime/` directory.

---

## Plugin Structure (new architecture)

Skills are now organized into **plugins** (domain-based groupings), not individual skill directories.

```
skills/<plugin-name>/
├── SKILL.md                  ← top-level router
├── manifest.json             ← plugin manifest listing all sub-skills
└── <category>/
    ├── <site-slug>/
    │   ├── SKILL.md          ← per-site instructions
    │   ├── manifest.json
    │   ├── API.md
    │   ├── noui_runtime/
    │   │   ├── __init__.py
    │   │   └── execute.py    ← NEW runtime (not cdp.py)
    │   └── operations/
    │       ├── __init__.py
    │       └── search_flights.py
    └── ...
```

**Reference plugins already on dev:**
- `skills/expedia-stay-search/` — uses execute.py (good pattern reference)
- `skills/airbnb-search-places/` — uses auth.py pattern

---

## Current State: Airline Scanner Plugin

### PR #104 — open, needs update
- **Branch:** `feat/airline-scanner-plugin-fork` on `rahulICoding/noui` fork
- **PR:** https://github.com/adoptai/noui/pull/104
- **Status:** Gabriel flagged that skills use CDP (deprecated) — needs migration to execute API

### What's in the plugin
All 25 airline skills consolidated at `skills/airline-scanner/airlines/<slug>/`:

| Slug | Airline | Tabby | Needs Migration |
|---|---|---|---|
| `air-canada` | Air Canada | No | No (stdlib) |
| `airasia` | AirAsia | Yes | ✅ Yes |
| `alaska` | Alaska Airlines | Yes | ✅ Yes |
| `american-airlines` | American Airlines | Yes | ✅ Yes |
| `british-airways` | British Airways | No | No (stdlib) |
| `cathay-pacific` | Cathay Pacific | Yes | ✅ Yes |
| `delta` | Delta Air Lines | Yes | ✅ Yes |
| `easyjet` | EasyJet | No | No (stdlib) |
| `emirates` | Emirates | Yes | ✅ Yes |
| `finnair` | Finnair | Yes | ✅ Yes |
| `hawaiian-airlines` | Hawaiian Airlines | Yes | ✅ Yes |
| `indigo` | IndiGo | Yes | ✅ Yes |
| `jetblue` | JetBlue | No | No (stdlib) |
| `klm` | KLM | Yes | ✅ Yes |
| `lufthansa` | Lufthansa | Yes | ✅ Yes |
| `qatar-airways` | Qatar Airways | Yes | ✅ Yes |
| `ryanair` | Ryanair | No | No (stdlib) |
| `singapore-airlines` | Singapore Airlines | Yes | ✅ Yes |
| `southwest` | Southwest | Yes | ✅ Yes |
| `swiss` | Swiss | Yes | ✅ Yes |
| `turkish-airlines` | Turkish Airlines | Yes | ✅ Yes |
| `united` | United Airlines | Yes | ✅ Yes |
| `volaris` | Volaris | Yes | ✅ Yes |
| `westjet` | WestJet | Yes | ✅ Yes |
| `wizz-air` | Wizz Air | Yes | ✅ Yes |

5 no-Tabby (stdlib) airlines are fine as-is. **20 Tabby airlines need operations rewritten to use execute.py.**

### Migration plan
Start with a few, validate the pattern, then do the rest:
1. Pick an easy one first — Qatar Airways (`cdp_fetch` → `execute_fetch` is the most direct translation)
2. Then a harder one — Emirates (cdp_eval + form fill → `execute_browser` HAR capture)
3. Once pattern is validated, update the remaining 18

---

## How to Write a Skill with the New execute API

### Runtime file
Copy `execute.py` from `skills/expedia-stay-search/noui_runtime/execute.py` (on dev branch) into your skill's `noui_runtime/` directory.

### For direct API calls (replaces cdp_fetch)
```python
from noui_runtime.execute import execute_fetch

result = await execute_fetch(
    profile_id,          # e.g. "qatar-airways"
    url,
    method="POST",
    body={"key": "val"},
    headers={"Authorization": "Bearer ..."},
)
```

### For browser navigation + HAR capture (replaces cdp_eval form fills)
```python
from noui_runtime.execute import execute_browser

await execute_browser(profile_id, "har_start")
await execute_browser(profile_id, "navigate", {"url": search_url}, timeout_ms=60_000)
await execute_browser(profile_id, "wait_for_selector", {"selector": ".results"}, timeout_ms=15_000)
har_data = await execute_browser(profile_id, "har_stop")
# parse har_data["har"]["log"]["entries"] for API responses
```

### Path bootstrap (same as before)
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from noui_runtime.execute import execute_fetch, execute_browser
```

---

## Tabby Setup (how to get it running locally)

**Every time after machine restart:**

### Step 1 — Start infrastructure (Docker Compose)
```bash
cd /Users/rahuliyer/Documents/ADOPT_AI/noui/tabby
docker compose up -d
# Starts: postgres, redis, nats, minio
# Verify: docker ps (should show 4 containers running)
```

### Step 2 — Start the API (separate terminal tab, keep it running)
```bash
cd /Users/rahuliyer/Documents/ADOPT_AI/noui/tabby
set -a && source .env.local && set +a
pnpm --filter @browser-hitl/api start:dev
```

Or to run it in the background (so the terminal doesn't block):
```bash
cd /Users/rahuliyer/Documents/ADOPT_AI/noui/tabby && \
set -a && source .env.local && set +a && \
pnpm --filter @browser-hitl/api start:dev > /tmp/tabby-api.log 2>&1 &
```

### Step 3 — Verify
```bash
curl http://localhost:8000/health/live
# Should return: {"status":"ok","version":"unknown","commit":"unknown"}
```

### Step 4 — Get an agent token (for testing skills)
```bash
# Login as admin
curl -s -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@browser-hitl.local","password":"e2e-admin-password"}'
# Returns JWT token

# Use token to create agent client
TOKEN="<jwt from above>"
TENANT="2f3c15ff-8bc7-489e-bfbf-04ff61cbe442"
curl -s -X POST http://localhost:8000/admin/agent-clients \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"test-client\",\"tenant_id\":\"$TENANT\",\"allowed_profiles\":[\"qatar-airways\",\"emirates\",\"delta\"]}"
# Returns client_id and client_secret — set as env vars when running skills
```

### Running a skill with Tabby
```bash
TABBY_API_URL=http://localhost:8000 \
TABBY_CLIENT_ID=agent_cl_... \
TABBY_CLIENT_SECRET=secret_sk_... \
.venv/bin/python3 skills/airline-scanner/airlines/qatar-airways/operations/search_flights.py \
  --origin DOH --destination LHR --date 2026-09-01
```

**Note:** Browser workers (for actual browser sessions) require Kubernetes (Kind). The Docker Compose setup is API-only. Without workers, Tabby skills will fail with "No healthy Tabby session". For testing the execute API pattern, you need either Kind or access to cloud Tabby (`tabby-api.adoptai.dev`).

---

## Git / PR Workflow

**You don't have direct write access to `adoptai/noui`.** Use your personal fork.

```bash
# Push to your fork
git push personal <branch-name>

# Then open PR on GitHub from:
# rahulICoding/noui:<branch> → adoptai/noui:dev
```

**Remotes already configured:**
- `origin` → `https://github.com/adoptai/noui` (read-only)
- `personal` → `https://github.com/rahulICoding/noui` (your fork, has write access)

**If push fails with "workflow scope" error:**
The PAT can't push commits that touch `.github/workflows/`. Fix: create a fresh branch off `personal/dev` (not `origin/dev`) and cherry-pick your commits onto it.

```bash
git fetch personal
git checkout personal/dev -b <new-branch-name>
git cherry-pick <your-commit-hash>
git push personal <new-branch-name>
```

---

## What's Next

### 1. Finish Airline Scanner plugin migration (current)
- Migrate 20 Tabby airline operations from CDP to execute API
- Start with Qatar Airways (simplest), then Emirates (complex), then the rest
- Update PR #104 once migration is done

### 2. Tax/Accounting skills (after airline plugin is done)
Gabriel wants focus on tax/accounting skills next. Abhiram already built a plugin covering Wave, FreshBooks, Xero, Zoho Books, Odoo. **Don't duplicate those.**

Waiting on Gabriel's response to confirm which platforms to target. Likely candidates not yet covered:
- QuickBooks Online
- Sage
- NetSuite
- TurboTax / TaxJar / Avalara (tax-specific, not covered by Abhiram)

---

## Blocked / Skipped Airlines (for reference)

| Airline | Reason |
|---|---|
| Etihad | In progress — form fill + Mobiscroll calendar times out; approach not verified |
| ANA | Akamai sensor data + Queue-IT blocks Angular trigger |
| Air France | Akamai _abck cookie bot detection |
| JAL | Akamai ENC sensor token required |
| Norwegian | Cloudflare + Imperva — 403 even via CDP |
| Frontier / Copa | Navitaire `GetLowFareAvailability` needs 199KB server-rendered input |
| Ryanair | Public API returns 409 intermittently (cookie bootstrap flaky) |
| EasyJet | Public API returns 403 (blocked) |

---

## Common Errors

| Error | Fix |
|---|---|
| `No healthy Tabby session for profile "X"` | Need K8s workers running — or use cloud Tabby |
| `TABBY_CLIENT_ID and TABBY_CLIENT_SECRET must be set` | Set env vars before running skill |
| `git push` → 403 | Push to `personal` remote, not `origin` |
| `refusing to allow PAT to update workflow` | Branch off `personal/dev`, not `origin/dev` |
| Tabby API `Invalid credentials` | Use password `e2e-admin-password`, not `LocalDev123!@#` |
| `pnpm: command not found` | `corepack enable && corepack prepare pnpm@latest --activate` |
