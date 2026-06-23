---
name: noui-record-login
description: Use this skill when the user wants to record a login flow for an authenticated app and register it with Tabby to get a tabby_profile_id. Triggers on "record a login", "capture a login flow", "register with Tabby", "set up authentication for NoUI", "create a tabby_profile_id", "noui login record", "login recording mode", "I need auth for my workflow", "record a login via VNC", "VNC login recording", "noui recording start", "noui recording import", or "record a login without the Chrome extension".
---

# NoUI Record Login

Record a login flow for an authenticated app and register it with Tabby to produce a `tabby_profile_id`. That ID is required when exporting a workflow as a FastMCP server for an app that needs authentication.

**Runtime prerequisite:** the default execution path (see `/noui-record-workflow` → *How Execution Works*) needs a healthy Tabby session for the profile *at invocation time*, not only during recording. Plan to keep Tabby running wherever the generated MCP server or Skill is used.

All commands run from the `noui/` directory using `.venv/bin/python cli/main.py`.

**Prerequisite:** `/noui-setup` must be complete — venv installed, `.env` configured with `ANTHROPIC_API_KEY`, `TABBY_API_URL`, and `TABBY_ADMIN_TOKEN`. The Chrome extension is required **only** for the extension capture method below, not for VNC recording.

---

## Two Ways to Capture a Login

Both methods produce the same artifact (a Tabby App + ServiceProfile + a `workbench/login_recordings/noui-<id8>-bundle.json`) and converge on the same post-register steps (`login credentials` → `login validate` → `tabby session ensure`):

1. **Tabby VNC recording (recommended — no extension, no local Chrome).** A human drives a Tabby-hosted browser through a VNC link; the worker captures interactions + URL flow + HAR server-side. See **VNC Recording** immediately below.
2. **Chrome extension.** Record locally with the NoUI Workflow Recorder extension. See **Chrome Extension Flow** (Steps 1–9) further down.

---

## VNC Recording (no Chrome extension)

A human completes the login in a Tabby browser over a VNC viewer link. The worker captures every interaction (over a network beacon that survives the stealth browser), the URL transitions, and HAR — then the bundle is compiled directly into a Tabby login profile. Passwords/OTP are redacted in-pod before the bundle leaves the browser.

**Prerequisite:** Tabby reachable at `TABBY_API_URL` with agent credentials in `.env`/cache. On a local **Kind** cluster the API is ClusterIP-only, so forward it first (cloud Tabby needs no forward — just point `TABBY_API_URL` at the hosted API):

```bash
.venv/bin/python cli/main.py tabby port-forward      # local Kind → localhost:18080
```

### Step V1 — Provision the recording session

```bash
.venv/bin/python cli/main.py recording start --url "<login-url>"
```

Prints a Tabby `session_id` and a VNC viewer URL (`…?mode=recording#token=…`). The token is single-use with a ~10-minute TTL — if it expires before you connect, re-run `recording start`.

### Step V2 — Record in the VNC viewer

Open the URL, complete the **full login** (the real site renders fully — egress is unrestricted during recording), then click **"Finish & export"**.

> Click "Finish & export" **only when the login is complete** — it ends the session and tears down the worker. Do not reload the viewer (the single-use token auto-refreshes internally).

### Step V3 — Compile + register

```bash
.venv/bin/python cli/main.py recording import <session_id> --url "<login-url>" --promote
```

Pulls the bundle, compiles the login DSL directly (no NoUI-backend round-trip), writes the standard bundle artifact (`workbench/login_recordings/noui-<id8>-bundle.json`), and registers the Tabby App + STAGING ServiceProfile. `extra_egress_allowlist` is auto-populated from the recorded HAR so enforce-mode replay won't hit egress 403s.

- `--auth-mode agent_token` (default) → stored K8s secret reused by the agent; `platform_jwt` → per-user HITL credentials.
- `--promote` → promote after registering; `--as-template` → also emit a tenant-wide App Template.

> **Kind note:** `--promote` reaches `CANARY` (which the runtime resolves, so the profile is usable), but the `CANARY → ACTIVE` canary-gate bypass uses `docker compose exec postgres` and fails on Kind — the profile stays at `CANARY`. On docker-compose Tabby it reaches `ACTIVE`.

### Step V4 — Converge with the standard flow

From here it is identical to the extension flow — operate on the written bundle (Steps 7–9 below):

```bash
.venv/bin/python cli/main.py login credentials workbench/login_recordings/noui-<id8>-bundle.json   # Step 7 (interactive — run via ! )
.venv/bin/python cli/main.py login validate    workbench/login_recordings/noui-<id8>-bundle.json   # Step 8
.venv/bin/python cli/main.py tabby session ensure --profile <profile_id>                            # Step 9
```

---

## Chrome Extension Flow

The original capture method, using the NoUI Workflow Recorder extension. Steps 1–9 below. (The VNC method above replaces Steps 1–6 with `recording start` + `recording import`; Steps 7–9 are shared.)

---

## Critical Rules (Never Violate)

- **ALWAYS** start the backend before asking the user to record
- **ALWAYS** run `login review` after export and before register — never skip it even for clean-looking recordings
- **NEVER** run `login register` if the review output shows `Generator valid: No` or any generator errors
- **NEVER** use the `tabby_profile_id` in a workflow export until `login validate` confirms a HEALTHY browser *session* (a profile has no HEALTHY state — see Step 8)
- **ALWAYS** note the `tabby_profile_id` printed by `login register` — it is needed for `/noui-record-workflow`

---

## Step 1 — Start the Backend

```bash
.venv/bin/python cli/main.py start
```

Spawns a detached FastAPI server at `http://localhost:8002`. Verify with:

```bash
.venv/bin/python cli/main.py status
```

**If startup fails:**

| Symptom | Fix |
|---|---|
| Port already in use | `lsof -i :8002`; kill conflicting process or set `NOUI_PORT` in `.env` |
| `uvicorn not found` | Deps not installed — run `poetry install --no-root` |
| Did not become ready | Check `.noui-backend.log` in the repo root |

---

## Step 2 — Create a Login Recording Session

```bash
.venv/bin/python cli/main.py login record "<AppName>" "<login-url>"
```

Example:

```bash
.venv/bin/python cli/main.py login record "HubSpot" "https://app.hubspot.com/login"
```

The CLI creates a session and prints the `session_id`. Note it — you need it for the export step.

---

## Step 3 — Record in Chrome

The CLI automatically created an App and a **Login** process in the extension (Step 2). Use the standard capture flow:

1. Click the **NoUI Workflow Recorder** extension icon in the Chrome toolbar
2. You will see the App (e.g., "HubSpot") in the Apps list — click it
3. Click the **Login** process inside the app
4. Navigate to the login URL in the active Chrome tab if not already there
5. Click **Start Capture** to begin recording
6. Perform the complete login flow: enter credentials, submit, and wait for the authenticated page to fully settle
7. Click **Stop** when done

> If the App or Login process is not visible, confirm the backend is running (`status`) and that Step 2 succeeded.

---

## Step 4 — Export the Bundle

```bash
.venv/bin/python cli/main.py login export <session_id>
```

Analyzes the captured session and writes:

```
workbench/login_recordings/<session_id8>-bundle.json
```

The bundle contains the application draft, service profile draft, inferred login steps, and review items.

---

## Step 5 — Review the Bundle

```bash
.venv/bin/python cli/main.py login review workbench/login_recordings/noui-<session_id8>-bundle.json
```

Check the output for:

- `Generator valid: No` — hard block; do not proceed to register; re-record
- `Errors:` section — must resolve before registering
- `Warnings:` section — review selector confidence; re-record if confidence is low
- `Generated login steps:` — verify the inferred steps match the actual login flow

**Only proceed to Step 6 if `Generator valid: Yes` and no errors.**

---

## Step 6 — Register with Tabby

Tabby must be reachable at `TABBY_API_URL` and `TABBY_ADMIN_TOKEN` must be set in `.env`. `TABBY_API_URL` is topology-dependent: `http://localhost:8080` for local compose, `http://localhost:18080` for local Kind (the VNC path below needs Kind/cloud, not compose), or the hosted URL for cloud. See the port map in `/noui-tabby-integration`.

> **If Tabby is not yet running:** run `noui tabby start` then `noui tabby setup` (interactive) to start the service and provision agent credentials before registering. See `/noui-setup` for the full Tabby CLI reference.

```bash
.venv/bin/python cli/main.py login register workbench/login_recordings/noui-<session_id8>-bundle.json
```

Provisions a Tabby Application and a STAGING ServiceProfile. On success:

> **Tenant-wide (optional):** add `--as-template` to also emit a Tabby App Template from the same bundle, so other tenant users auto-provision their own profile from this config (federated/`platform_jwt` runtime). You can also do this separately via `noui tabby template create <bundle.json>`. See `/noui-tabby-integration` → `reference/provisioning.md`.

```
Registered profile '<profile_id>'
  Tabby profile ID : <tabby_profile_id>
  Version state    : STAGING
```

**Record the `tabby_profile_id` value** — this is the `--profile` argument for `workflow export --as mcp`.

> **The STAGING trap — you MUST promote before any tool call.** A freshly
> registered profile is left in `STAGING`, but the runtime resolver matches only
> `ACTIVE`/`CANARY` (`credentials.service.ts:224,268`). A `STAGING`-only profile
> `404`s ("No active profile") at the first `/execute/fetch` or
> `/credentials/request`. Promote it with `noui login promote` (Step 6b) or, to
> register and promote in one go, pass `--promote`:
>
> ```bash
> .venv/bin/python cli/main.py login register <bundle.json> --promote
> ```
>
> (`login import <session_id> --promote` does the same through the convenience
> wrapper.) When promoted, the register output shows `Version state : ACTIVE`.

---

## Step 6b — Promote STAGING → ACTIVE (required if you did not pass `--promote`)

```bash
.venv/bin/python cli/main.py login promote workbench/login_recordings/noui-<session_id8>-bundle.json
```

Runs the `STAGING → CANARY → ACTIVE` walk (the same sequence `noui tabby setup`
uses). Skip this only if you already passed `--promote` to `register`/`import` —
in that case the profile is already `ACTIVE` and `login promote` is a no-op. Until
the profile is `ACTIVE`, the profile is *not resolvable* and any generated tool
call returns the actionable "run `tabby session ensure`" error.

---

## Step 7 — Set Credentials

After registration, provide the login credentials (username/password) so that Tabby's browser worker can perform the automated login:

```bash
.venv/bin/python cli/main.py login credentials workbench/login_recordings/noui-<session_id8>-bundle.json
```

The CLI will prompt interactively for:
- **Username (email):** the account email used to log in
- **Password:** entered with masked input (not echoed)

Credentials are stored in:
- `tabby/.tabby-noui-client.json` (username + profile metadata)
- `tabby/.env.local` (password as `TABBY_NOUI_<PROFILE>_PASSWORD`)

**This step is required** before `tabby session ensure` can start a browser session — without it the worker will fail with `Credentials not found`.

> **IMPORTANT:** This command requires user interaction (keyboard input). Ask the user to run it themselves via `! .venv/bin/python cli/main.py login credentials <bundle.json>` so that credentials are entered securely in their terminal.

---

## Step 8 — Validate the Profile

```bash
.venv/bin/python cli/main.py login validate workbench/login_recordings/noui-<session_id8>-bundle.json
```

Polls Tabby for up to 60 seconds waiting for a HEALTHY browser *session* for the profile's app. (The profile has no HEALTHY state — its `version_state` is `STAGING/CANARY/ACTIVE/RETIRED`; `HEALTHY` is a *session* state, and this command polls the `sessions` table.)

**If validation fails:**

| Symptom | Fix |
|---|---|
| Profile enters FAILED state | Re-record with slower, more deliberate interactions |
| Timeout (60s) | Check Tabby logs; confirm `TABBY_API_URL` is reachable |
| Keepalive URL errors | The keepalive URL must return HTTP 200 when authenticated — redirect-only URLs are not valid |

---

## Step 9 — Ensure a Live Tabby Browser Session

A HEALTHY browser session is distinct from the profile's `version_state` — a profile is just a config record and never enters a HEALTHY state. The runtime needs an active session worker to reach Tabby at tool-call time: the default `tabby` execution mode via `noui_runtime/execute.py` (`execute_fetch` → `/execute/fetch`), or the legacy `--execution-mode http` path via `noui_runtime/auth.py` (`resolve_auth` → `/credentials/request`). For why a profile can be usable only by its creator vs tenant-wide (the `owner_user_id` scoping model), see `/noui-tabby-integration`.

```bash
.venv/bin/python cli/main.py tabby session ensure --profile <profile_id>
```

Example:

```bash
.venv/bin/python cli/main.py tabby session ensure --profile expedia
```

Starts (or verifies) a persistent browser session worker for the registered profile. The `--profile` flag is **required** — without it the command uses the default profile list from the cache, which may not include the newly registered profile. Expected output:

```
✓ Session for '<profile_id>' is HEALTHY
  CDP :9222     : reachable
  Execute :8091 : ready  (execute/fetch routed …)
```

The two surface lines distinguish the CDP/streaming surface (`:9222`) from the execute surface (`:8091`, where `/execute/fetch` is served). If `Execute :8091` shows `NOT ready`, the session is HEALTHY but tools will fail — re-run `session ensure` so the worker gets `EXECUTE_ENABLED=true` and confirm `LOCAL_WORKER_URL` is set for the API.

> **HEALTHY ≠ authenticated.** `HEALTHY` only means the keepalive passed (it can pass *pre-login* on an SPA whose `url_check` matches before auth completes). It does not prove the browser is logged in: an unfinished login makes `fetch(credentials:'include')` run unauthenticated, and the target's 401/403 returns **wrapped as a 200 body**. Pre-warm the live session at the authenticated entry point with `tabby session ensure --profile <slug> --open <auth-url>` (or `--skill <id>` to pull the start URL from a generated skill manifest), and verify one known-authenticated request returns real data before trusting the profile.

**If this fails:**

| Symptom | Fix |
|---|---|
| `TABBY_CLIENT_ID` / `TABBY_CLIENT_SECRET` not set | Run `tabby setup` to provision agent credentials and write them to `.env` |
| Worker starts but immediately exits | Check `.noui-backend.log`; confirm Tabby is reachable at `TABBY_API_URL` |
| Profile not found in cache | Confirm `login register` ran successfully — it adds the profile to the cache automatically |
| `Credentials not found for k8s:secret/...` | Run `login credentials <bundle.json>` to set username/password |

> You only need to do this once per machine restart, or if the session worker has stopped. Run `tabby session status` to check current state without starting a new worker.

---

## Convenience Path (clean recordings only)

```bash
.venv/bin/python cli/main.py login import <session_id>
.venv/bin/python cli/main.py login import <session_id> --validate
```

Runs export + review + register in one command (and optionally validate). Use only for recordings with no expected issues — the decomposed flow above is easier to debug.

---

## Output

On completion you have:
- `workbench/login_recordings/noui-<session_id8>-bundle.json`
- `tabby_profile_id` — printed by `login register`
- A live Tabby browser session confirmed via `tabby session ensure`

Pass the `tabby_profile_id` to `/noui-record-workflow` via `--profile`. The session worker must remain running during workflow recording and runtime use.

---

## Decision Flow

```
Start
  │
  ├─ backend running?
  │     ├─ No  → Step 1: start
  │     └─ Yes → continue
  │
  Step 2: login record "<App>" "<url>" → note session_id
  │
  Step 3: Chrome (Apps → <AppName> → Login → Start Capture → login → Stop)
    └─ no events on export? → ensure target tab was active, retry Step 3
  │
  Step 4: login export <session_id>
    └─ writes noui-<id8>-bundle.json
  │
  Step 5: login review <bundle.json>
    ├─ generator errors? → re-record (return to Step 2)
    └─ clean → continue
  │
  Step 6: login register <bundle.json> → note tabby_profile_id
  │       (add --promote to fold Step 6b in here)
  │
  Step 6b: login promote <bundle.json> → STAGING → ACTIVE
  │        (REQUIRED — runtime resolves only ACTIVE/CANARY; skip if --promote)
  │
  Step 7: login credentials <bundle.json> (user enters username + password)
  │
  Step 8: login validate <bundle.json>
    ├─ HEALTHY → continue
    └─ FAILED  → re-record (return to Step 2)
  │
  Step 9: tabby session ensure --profile <profile_id>
    └─ session worker healthy → pass tabby_profile_id to /record-workflow
```

---

## CLI Command Reference

| Command | Purpose |
|---|---|
| `.venv/bin/python cli/main.py tabby port-forward` | (Kind) Forward the cluster Tabby API to `localhost:18080` |
| `.venv/bin/python cli/main.py recording start --url "<url>" [--workflow]` | **VNC:** provision a Tabby VNC recording session, print the viewer URL |
| `.venv/bin/python cli/main.py recording import <session_id> [--url <url>] [--auth-mode agent_token\|platform_jwt] [--promote] [--as-template]` | **VNC:** compile the recorded bundle + register the Tabby App + ServiceProfile (writes the standard bundle artifact) |
| `.venv/bin/python cli/main.py start` | Start the NoUI backend (Chrome-extension flow only) |
| `.venv/bin/python cli/main.py status` | Check backend and Tabby reachability |
| `.venv/bin/python cli/main.py login record "<App>" "<url>"` | Create a login recording session |
| `.venv/bin/python cli/main.py login list` | List existing login sessions |
| `.venv/bin/python cli/main.py login export <session_id>` | Analyze session → write bundle JSON |
| `.venv/bin/python cli/main.py login review <bundle.json>` | Print validation and review items |
| `.venv/bin/python cli/main.py login register <bundle.json> [--as-template]` | Provision Application + STAGING ServiceProfile (`--as-template`: also emit a tenant-wide App Template) |
| `.venv/bin/python cli/main.py login register <bundle.json> --promote` | Register and promote STAGING → ACTIVE in one step |
| `.venv/bin/python cli/main.py login promote <bundle.json>` | Promote a registered profile STAGING → ACTIVE (required before tool calls) |
| `.venv/bin/python cli/main.py login credentials <bundle.json>` | Set username/password for a registered profile |
| `.venv/bin/python cli/main.py login validate <bundle.json>` | Wait for a HEALTHY browser session for the profile |
| `.venv/bin/python cli/main.py login import <session_id>` | Convenience: export + review + register |
| `.venv/bin/python cli/main.py login import <session_id> --validate` | Convenience: export + review + register + validate |
| `.venv/bin/python cli/main.py login import <session_id> --promote` | Convenience: export + review + register + promote |
| `.venv/bin/python cli/main.py tabby session ensure --profile <id>` | Start or verify a live browser session worker |
| `.venv/bin/python cli/main.py tabby session status` | Show current browser session state |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Backend not running | `.venv/bin/python cli/main.py start` |
| `Tabby API not reachable` | Confirm Tabby is running; check `TABBY_API_URL` in `.env` |
| Bundle has generator errors | Re-record with slower, explicit interactions; avoid rapid clicks |
| App or Login process missing in extension | Confirm Step 2 ran successfully; check backend is running |
| Low selector confidence in review | Re-record; interact with fields one at a time with visible focus |
| Validate timeout (60s) | Check Tabby logs; verify the keepalive URL returns HTTP 200 when authenticated |
| `Credentials not found for k8s:secret/...` | Run `login credentials <bundle.json>` to set username/password |
| `TRANSIENT_FAIL` on health check | The site may be rate-limiting (429) the `url_check`. Switch the app's keepalive via `PUT /apps/{app_id}`. ⚠️ `dom_check` on `body` can false-negative on SPAs (Salesforce Lightning, Workday) where `body` reports not-visible even when logged in — for those, point `url_check` at a stable authenticated URL or use a specific (non-`body`) `dom_check` selector |
| Keepalive URL is redirect-only | Find a URL that loads authenticated content, not a redirect chain |
