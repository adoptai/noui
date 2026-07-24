---
name: noui
description: Use this skill to turn real websites into agent-callable tools without computer-use. NoUI captures a browser login or workflow through a Tabby session (manual VNC or Autopilot), compiles the capture into an MCP server, a Skill, or a Tabby ServiceProfile, and runs the result through Tabby's authenticated /execute engine. Triggers on "record a workflow", "record a login", "turn this site into an MCP/skill", "automate this website without computer use", "generate a tool from a website", "noui capture/compile/activate", "register a Tabby profile".
---

# NoUI

NoUI records what a site's browser already does and ships it as tools your agent calls directly — no DOM-walking, no computer-use, no Chrome extension. Capture happens **server-side inside Tabby**; this skill is a self-contained Python toolkit that drives Tabby and compiles the result.

**The only external requirement is a reachable Tabby.** There is no NoUI backend, no daemon, and no `ANTHROPIC_API_KEY` — the compile pipeline is deterministic.

> **Running in the Agent Harness?** If `NOUI_TABBY_AUTH_MODE=broker` is set in your
> environment, you are inside the harness sandbox. **Ignore the Setup, auth, and VNC
> recording instructions below** — they are for local CLI use. In the harness:
> `TABBY_API_URL` already points at the control-plane **broker** (fronting cloud Tabby),
> auth is injected per-user by the broker (**no `.env`, no `TABBY_CLIENT_ID/SECRET/
> ADMIN_TOKEN`**), recording is **Autopilot only** (no VNC), and you compile with
> **`--execution-mode harness`** (never `tabby`/`http`). Follow the **`noui` harness
> skill** instructions, not this file.

---

## The three pillars

1. **Capture** (`scripts/capture_*`) — record a login or workflow (HAR + DOM) via a Tabby **VNC** session (a human drives) or **Autopilot** (the agent drives via Tabby `/execute/browser`). Tabby's worker captures the bundle server-side.
2. **Compile** (`scripts/compile_*`, also folded into `capture_import`) — turn a capture into assets: a workflow → an **MCP server** and/or a **Skill**; a login → a Tabby **App Template + ServiceProfile**.
3. **Activate** (`scripts/activate_*`) — make assets usable: **register** a login as a tenant-wide App Template with Tabby (template-first — no promote step), **verify** auth, **install** a generated skill into any agent, and run tools through Tabby `/execute/fetch`.

> Between **Compile** and **Activate**, always run **Generalize** — an LLM-driven
> **agent** step (not a script; the compiler stays deterministic): prune the noise
> operations and test the rest until they reliably fetch what's expected. See the
> [Generalize](#generalize--prune-the-noise-then-test-until-it-works) section below
> and `references/generalize.md`.

---

## Setup (one time)

NoUI runs in **your own** Python environment. Two steps:

1. **Install dependencies** (declared in `pyproject.toml`):

   ```bash
   pip install httpx python-dotenv mcp        # or: pip install -e .   (from this skill dir)
   ```

   `httpx` + `python-dotenv` power the toolkit; `mcp` is only needed to *run* a generated MCP server.

2. **Point at Tabby.** Set these in your environment or a `.env` next to this file (`skills/noui/.env`):

   | Variable | Purpose |
   |---|---|
   | `TABBY_API_URL` | Tabby base URL (default `http://localhost:8000`) |
   | `TABBY_CLIENT_ID` / `TABBY_CLIENT_SECRET` | Agent credentials — minted by your Tabby setup; used for recording + execution |
   | `TABBY_ADMIN_TOKEN` | Required only to **register** App Templates (Activate); Editor role suffices |
   | `NOUI_TABBY_AUTH_MODE` | Optional: `agent_token` (default), `platform_jwt` (per-user cloud), or `broker` (harness-injected) |
   | `NOUI_WORKBENCH_DIR` | Optional: where generated assets are written (default `skills/noui/workbench/`) |

Run scripts from this directory: `python scripts/<name>.py …`.

---

## Quick flows

> ⚠️ **Banks / financial institutions → always add `--residential-proxy`.** Bank
> and other financial portals routinely block or fraud-flag datacenter IPs, so a
> login/workflow recorded (or an App Template captured) without it will fail, get
> challenged, or trip the account's fraud controls. For any bank, brokerage,
> card, or lender site, pass `--residential-proxy` on `capture_record.py` (routes
> the recorded browser through a US residential IP). See
> `references/pillar-1-capture.md` → *Residential proxy egress*.

**Record a workflow → MCP + Skill (authenticated):**

```bash
python scripts/capture_record.py --mode workflow --url https://example.com --from <login-session-id>
# open the printed VNC URL, drive the flow, click "Finish & export", then:
python scripts/capture_import.py <session_id> --as both --profile-slug <login-profile>
# GENERALIZE (agent step): prune noise ops, then test each 2-3x until it fetches
# what's expected — see references/generalize.md. THEN:
python scripts/activate_verify.py workbench/mcp_servers/<app>/<server_id>
python scripts/activate_install.py workbench/skills/<app> claude-code
```

**Record a login → registered Tabby profile:**

```bash
python scripts/capture_record.py --mode login --url https://example.com/login --name example
# ^ --name triggers a check for an existing App Template with a similar name + same URL;
#   if one matches, the capture is skipped and a reuse command is printed instead (--force to bypass)
# drive the login in VNC, finish, then:
python scripts/capture_import.py <session_id> --name example      # registers a tenant-wide App Template
```

**Combined — login + workflow in ONE session:**

```bash
# Provisions a normal login session (Tabby records it as 'login'); sign in AND
# then drive the workflow in the same session, one "Finish & export".
python scripts/capture_record.py --mode combined --url https://example.com/login
# NoUI splits the one capture at the login boundary → registers the login App
# Template AND compiles the workflow (auth_type=session) bound to that profile:
python scripts/capture_import.py <session_id> --combined --as skill --name example
```

**Static API-key app (no login to record):**

```bash
# The app authenticates with a static key sent on every request — there is no
# session to record. Record ONLY the workflow, then declare the auth model:
python scripts/capture_record.py --mode workflow --url https://example.com
python scripts/capture_import.py <session_id> --as skill --execution-mode harness \
    --auth-type api-key --api-key-header Authorization
# An admin registers the printed ${SECRET:...} value in the harness secret store
# (AGENT_HARNESS_WEB_API_SECRETS). NoUI never records or holds the key itself.
```

**Auth model is declared, not guessed.** Workflow compile takes `--auth-type`:
`session` (default — a login/session was recorded → `tabby_credentials`), `api-key`
(static key, no login → `static_secret_header`), or `auto` (legacy HAR heuristic).
The default removes the old failure where a separately-recorded login looked "static".

**Autopilot (agent drives, no human VNC):** see `references/pillar-1-capture.md`.

---

## Generalize — prune the noise, then test until it works

The initial compile is a **raw** mirror of the recording: it includes calls that
aren't part of the task (analytics, config pings, prefetch, third-party hosts) and
names lifted straight from the API. **After every compile, before install**, run the
generalization pass — an LLM-driven **agent** step (no script; the compiler stays
deterministic):

1. **Prune noise** — remove operations that don't serve the workflow goal
   (telemetry/analytics/consent/keepalive pings, typeahead/prefetch, duplicates, any
   non-app host) from `operations.json` (harness) or `operations/` + `manifest.json`
   (tabby/http).
2. **Test the survivors** — actually run each remaining operation against the live
   site (harness: the `call_web_api` tool with the recipe; tabby/http:
   `python operations/<tool>.py …` or `activate_verify.py`), **2–3 times each**, and
   confirm it returns the expected data.
3. **Fix or drop** failures (empty creds → healthy session; 429 → browser-side
   execute; wrong/empty body → recompile from the saved bundle), then **rename**
   cryptic tools/params to natural language and **parameterize** hardcoded values.
4. **Loop** until every remaining operation passes 2–3 clean runs — only then install.

Full playbook: `references/generalize.md`.

---

## Script reference

| Script | Pillar | Purpose |
|---|---|---|
| `capture_record.py` | 1 | Provision a VNC recording session; prints the viewer URL |
| `capture_autopilot.py` | 1→2 | Drive a profile's session via `/execute/browser` (scripted steps); synthesize a bundle + compile |
| `capture_import.py` | 1→2→3 | Drain the bundle; compile (workflow) or compile+register (login) |
| `bundle_inspect.py` | 1 | Summarise a saved bundle: mode block, URL timeline, endpoint/tool table |
| `compile_workflow.py` | 2 | Re-compile a saved workflow bundle → MCP/Skill |
| `compile_login.py` | 2 | Compile a saved login bundle → App/ServiceProfile drafts |
| `activate_register.py` | 3 | Register a compiled login result with Tabby as a tenant-wide App Template |
| `activate_verify.py` | 3 | Deterministic auth dry-run on a generated MCP server |
| `activate_install.py` | 3 | Install a generated skill into an agent (agnostic) |

---

## Inspect a bundle when a compile surprises you

```bash
python scripts/bundle_inspect.py workbench/bundles/<name>.json
python scripts/bundle_inspect.py --session <session_id>    # drain from Tabby first
```

Prints, in one pass: the **mode block** (Tabby's `recording_mode`, NoUI's own
classification, and the mode the session was *provisioned* as — side by side, with a
warning when they disagree), the URL timeline with the login boundary marked, credential
interactions (roles + redaction only, never values), and the **API endpoint table** built
with the compiler's own filter and naming — so it previews the operation set compile will
emit. Reach for it first whenever an import produced the wrong kind of asset or an
operation you didn't expect.

**NoUI never routes on Tabby's `recording_mode`.** The field is unreliable — a
warm-pool recording session always reports `login` regardless of what was provisioned.
`capture_import.py` decides from, in order: `--mode`, the provision ledger
(`<workbench>/sessions/<session_id>.json`, written by `capture_record.py`), then the
capture's content. It prints which source won.

---

## Capture bundles are always saved (keep them)

Every capture (`capture_autopilot.py` and `capture_import.py`) **persists the raw bundle** — `{har, click_events, url_events}` — to `workbench/bundles/<name>.json`. **Do not discard it.** The bundle, not the compiled asset, is the source of truth for *generalizing* and *regenerating* the asset later: renaming tools, parameterizing request bodies (e.g. recovering a GraphQL query body), dropping telemetry/ad calls, or fixing anti-bot issues. A compiled MCP/Skill cannot be re-generalized; its bundle can — recompile with `compile_workflow.py <bundle.json>`. Recording bundles also expire server-side (Tabby TTL), so the local copy is the only durable one.

---

## Reference docs

- `references/pillar-1-capture.md` — VNC vs Autopilot, bundle shape, session reuse (`--from`)
- `references/pillar-2-compile.md` — HAR→tools, execution modes (`tabby`/`http`/`harness`), login drafts
- `references/generalize.md` — post-compile agent pass: prune noise ops + test until they work
- `references/pillar-3-activate.md` — register (template-first), verify, install, `/execute` runtime
- `references/tabby-setup.md` — pointing NoUI at a local or cloud Tabby
- `references/auth-modes.md` — `agent_token` vs `platform_jwt`

Example generated assets live in the repo's `mcp/` directory. Demo plugins are sibling directories under `skills/` — `travel` (Airbnb, Expedia, Flydubai, Google Flights) and `quickbooks` (bank reconciliation).
