# NoUI

> **Skip the UI. Turn any website into fast, reliable APIs for
> agents.**\
> *Go beyond Claw. Call the underlying APIs.*\
> *Skip Computer-use Agents.*

------------------------------------------------------------------------

## 🚀 What is NoUI?

**NoUI turns any website into an API your agents can call.**

Instead of automating clicks and scraping the UI, NoUI:
1. Records how you use a website (through a Tabby browser session)
2. Extracts the underlying APIs
3. Converts them into callable Python functions
4. Exposes them via MCP or as an installable Skill for agents

No clicks. No DOM parsing. No brittle automation.

NoUI ships as a **single, agent-agnostic skill bundle** (`skills/noui/`). The
only external dependency is a reachable Tabby — there is no separate backend
service to run and no LLM API key required.

------------------------------------------------------------------------

## ⚡ Why NoUI?

Computer-use agents simulate humans:
- 🐢 Slow (UI loops, page loads)
- 💸 Expensive (token-heavy, step-heavy)
- 🧱 Fragile (break on UI changes)

**NoUI executes software directly:**
- ⚡ **Fast** --- direct API calls
- 💸 **Cheap** --- fewer steps, fewer tokens
- 🎯 **Reliable** --- uses the same APIs the app uses internally

> **Stop automating clicks. Execute software.**

------------------------------------------------------------------------

## 🧠 How it works

1.  Record a session in a Tabby browser — manually in a **VNC** viewer, or with
    **Autopilot** (the agent drives `POST /execute/browser`)
2.  Tabby captures HAR traces + interaction events **server-side**
3.  Compile the capture into Python API functions (a FastMCP server and/or a Skill)
4.  Execute tools from inside the authenticated Tabby browser session
5.  Install the Skill / run the MCP server in any agent
6.  Agents call APIs instead of clicking UI

### Execution

Generated tools run from **inside** the authenticated Tabby browser, not from a
Python HTTP client. By default (the `tabby` execution mode) each operation calls
Tabby's `POST /execute/fetch` endpoint over plain HTTP; the Tabby worker runs
`fetch(url, {credentials: 'include'})` inside the real authenticated browser, so
the browser's TLS fingerprint and cookies are used — no credential extraction,
and no Akamai/Cloudflare false positives. There is no WebSocket or CDP-port
access from the NoUI side.

The legacy Python-side path (`httpx` + resolved credentials) is still available
for server-to-server APIs that aren't reachable from the browser origin; opt in
explicitly when compiling with `--execution-mode http`.

------------------------------------------------------------------------

## 🏗️ Architecture

```
Tabby browser session
    → VNC (human drives) | Autopilot (agent drives POST /execute/browser)
    ↓ (HAR + click/url events — captured server-side by Tabby)
noui_core  (the skill bundle, skills/noui/)
    → capture/   — drain/synthesize the recording bundle
    → compile/   — login → Tabby Application + ServiceProfile; workflow → FastMCP server / Skill
    → activate/  — register/promote profiles, verify auth, install into agents
    ↓
Output (all under skills/noui/workbench/)
    → bundles/                       — saved capture bundles (source for re-generalization)
    → mcp_servers/<app>/<server_id>/ — runnable FastMCP packages
    → skills/<app>/                  — installable agent Skills
    ↓
Tabby Runtime  (persistent browser sessions + live auth, POST /execute/fetch)
    ↓
MCP / Skill → Claude / Codex / Agents
```

------------------------------------------------------------------------

## 🔑 Core Components

### 🧩 HAR → API Compiler

-   Parses browser network traffic (HAR), deterministically — no LLM calls
-   Groups requests into logical workflows
-   Generates clean Python functions (MCP server and/or Skill)

### 🐾 Tabby Runtime

-   Keeps browser sessions alive in the cloud
-   Handles cookies, headers, auth
-   Streams VNC for login / 2FA when needed; runs `POST /execute/*` for tools

### 🔌 MCP Server / Skill

-   Exposes generated APIs as tools
-   Works with Claude, Codex, and MCP-/skill-compatible agents

------------------------------------------------------------------------

## 🎯 What you can do

-   Automate websites with no public APIs
-   Turn internal tools into agent-ready SDKs
-   Replace brittle browser automation workflows
-   Build production-grade agents that actually scale

------------------------------------------------------------------------

## ⚡ Example

``` python
def create_invoice(customer_id, amount):
    return call_api(
        method="POST",
        endpoint="/api/invoices",
        headers=session_headers,
        json={
            "customer_id": customer_id,
            "amount": amount
        }
    )
```

Then your agent simply says:

> "Create an invoice for customer X"

NoUI executes it directly.

------------------------------------------------------------------------

## 🔐 Authentication Flow

1.  NoUI provisions a remote Tabby browser session
2.  Streams it via VNC
3.  You complete login / 2FA **once**, in the VNC viewer
4.  Tabby maintains the session

No username/password is ever stored (`credential_ref: manual:`). When the
browser reaches the post-login URL, Tabby **auto-resolves** the login — no
repeated "Mark as Resolved" clicks. Agents reuse the authenticated context
automatically through `POST /execute/fetch`.

------------------------------------------------------------------------

## ⚔️ NoUI vs Computer-Use Agents

|               | Computer-Use Agents  | NoUI               |
|---------------|----------------------|--------------------|
| Speed         | Slow (UI loops)      | Fast (direct APIs) |
| Cost          | High                 | Low                |
| Reliability   | Breaks on UI changes | More Stable        |
| Approach      | Simulates humans     | Executes software  |

------------------------------------------------------------------------

## 🧠 Philosophy

> Websites already expose APIs.
> The UI is just a layer on top.

NoUI removes that layer.

------------------------------------------------------------------------

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- A reachable Tabby (cloud, or self-host via Docker + the `tabby/` submodule)
- Tabby credentials in one tenant: `TABBY_CLIENT_ID` / `TABBY_CLIENT_SECRET`
  (agent), and `TABBY_ADMIN_TOKEN` (to register/promote profiles)

### Clone with submodules

> ⚠️ **NoUI includes Tabby as a git submodule** (for local dev/test and to
> version-lock the endpoint contract). Clone recursively, or the `tabby/`
> directory will be empty.

```bash
git clone --recursive https://github.com/adoptai/noui.git

# Or, if you already cloned without --recursive:
git submodule update --init
```

### Install

```bash
cd noui/skills/noui

# 1. Create a venv and install the bundle (noui_core + deps: httpx, python-dotenv, mcp)
python -m venv .venv && . .venv/bin/activate
pip install -e .

# 2. Configure environment
cp .env.example .env
# Edit .env: TABBY_API_URL, TABBY_CLIENT_ID / TABBY_CLIENT_SECRET,
#            and TABBY_ADMIN_TOKEN (for register/promote). All in the SAME tenant.
```

Scripts are run from `skills/noui/` as `python scripts/<name>.py …`.

------------------------------------------------------------------------

## 🤖 Agent Skills

Install the NoUI skill — one self-contained bundle covering the full
record → compile → activate pipeline — into your agent (Claude Code, Codex,
OpenCode…):

```bash
npx skills add https://github.com/adoptai/noui --skill noui
```

Then invoke `/noui` in your agent. The bundle's `SKILL.md` documents the three
pillars and the scripts; `references/` holds the deep dives.

> **Human users** who prefer an interactive picker can run `npx skills add https://github.com/adoptai/noui` (no `--skill` flag) and tick what they want. AI agents must use the per-skill `--skill <name>` form — the interactive selector blocks on stdin.

### Available skills

| Skill / plugin | Purpose |
|---|---|
| `/noui` | The bundle — Capture (VNC/Autopilot) → Compile (MCP/Skill/Profile) → Activate (register/install/run) |
| `skills/travel` | Claude Code plugin: Airbnb, Expedia, Flydubai, Google Flights via Tabby `/execute` |
| `skills/quickbooks` | Claude Code plugin: QuickBooks bank reconciliation via Tabby `/execute` |

Install demo plugins from a clone:

```bash
claude plugin install ./skills/travel
claude plugin install ./skills/quickbooks
```

------------------------------------------------------------------------

## 🛠️ Developer Flow

All commands run from `skills/noui/`.

### Unauthenticated apps

```bash
# 1. Record a workflow in a Tabby VNC session
python scripts/capture_record.py --mode workflow --url "https://example.com"
#    open the printed VNC URL, perform the workflow, click "Finish & export"

# 2. Drain + compile to a FastMCP server (and/or Skill)
python scripts/capture_import.py <session_id> --as mcp
```

### Authenticated apps (with Tabby)

```bash
# 1. Record a login and register it with Tabby (no stored credentials — VNC takeover)
python scripts/capture_record.py --mode login --url "https://app.example.com/"
python scripts/capture_import.py <session_id> \
  --credential-mode takeover --promote --as-template \
  --post-login-url-pattern "<glob the logged-in URL matches but login does not>"
# → registers App + ServiceProfile (CANARY) in the agent's tenant

# 2. Record the workflow, authenticated — Autopilot (agent drives) …
python scripts/capture_autopilot.py <profile_slug> --steps steps.json --as both
#    … or VNC, seeded from the login: capture_record.py --mode workflow --from <login-session-id>

# 3. Install the generated Skill into your agent
python scripts/activate_install.py workbench/skills/<app> claude-code
```

### Combined capture — login + workflow in one session

```bash
# Sign in AND drive the workflow in a single VNC session; NoUI splits the one
# capture into a registered login App Template + a workflow asset.
python scripts/capture_record.py --mode combined --url "https://app.example.com/login"
python scripts/capture_import.py <session_id> --combined --as skill --name <app>
```

### Static API-key apps (no login to record)

```bash
# The app authenticates with a static key on every request — record only the
# workflow and declare the auth model. NoUI emits a ${SECRET:...} placeholder;
# an admin registers the value in the harness secret store (never held by NoUI).
python scripts/capture_record.py --mode workflow --url "https://example.com"
python scripts/capture_import.py <session_id> --as skill --execution-mode harness \
  --auth-type api-key --api-key-header Authorization
```

> **`--auth-type` declares the app's auth model** (`session` default | `api-key` | `auto`).
> Distinct from `--auth-mode` (`agent_token`/`platform_jwt`), which is the Tabby **token** mode.

------------------------------------------------------------------------

## 📦 Generated MCP Output

```
skills/noui/workbench/
  bundles/<name>.json                 # saved capture bundle (re-generalization source)
  mcp_servers/<app_slug>/<server_id>/
    server.py                         # FastMCP entrypoint
    tools.json                        # Tool inventory
    manifest.json                     # Server manifest (lifecycle + auth metadata)
    noui_runtime/
      auth.py                         # Runtime auth adapter (fetches live creds from Tabby)
      execute.py                      # Calls Tabby /execute/fetch from inside the browser
    operations/
      <tool_name>.py                  # One file per generated tool
  skills/<app_slug>/                  # Installable agent Skill (SKILL.md + operations/)
```

------------------------------------------------------------------------

## 🖥️ Scripts Reference

The bundle replaces the old CLI with thin, purpose-built scripts (run from
`skills/noui/`):

```
scripts/capture_record.py    --mode login|workflow|combined --url <url> [--from <login-session>]
                             [--profile <slug>] [--auth-type {session|api-key}] [--api-key-header <h>]
scripts/capture_autopilot.py <profile> --steps steps.json --as {mcp|skill|both}
scripts/capture_import.py    <session_id> [--as {mcp|skill|both}] [--execution-mode {tabby|http|harness}]
                             [--combined] [--auth-type {session|api-key|auto}] [--api-key-header <h>]
                             [--profile-slug <slug>] [--credential-mode {takeover|manual|stored|auto}]
                             [--tenant-id <id>] [--post-login-url-pattern <glob>]

scripts/compile_workflow.py  <bundle.json> --as {mcp|skill|both}
scripts/compile_login.py     <bundle.json> --out <compiled.json>

scripts/activate_register.py <compiled-login.json> [--promote] [--as-template] [--tenant-id <id>]
scripts/activate_verify.py   <mcp_server_dir>
scripts/activate_install.py  <skill_dir> <agent>    # agent: claude-code|codex|cline|opencode|agents [--project]
```

------------------------------------------------------------------------

## 🔌 Tabby endpoints used

NoUI talks only to Tabby (no NoUI backend service):

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/agent-token` | Exchange `TABBY_CLIENT_ID/SECRET` for an agent token |
| POST | `/recording/sessions` | Provision a VNC recording session |
| GET  | `/recording/sessions/{id}/bundle` | Drain the captured bundle (HAR + events) |
| POST | `/apps` | Create an Application (admin) |
| POST | `/admin/profiles` · `/{id}/promote` | Create / promote a ServiceProfile |
| POST | `/admin/app-templates` | Create a tenant-wide App Template |
| POST | `/apps/{id}/sessions/scale` | Bring a worker session up/down |
| GET  | `/agent/session-status/{profile}` | Session state + HITL VNC URL (login) |
| POST | `/execute/fetch` · `/execute/browser` | Run a tool / drive the browser inside the session |

------------------------------------------------------------------------

## 🔗 Tabby compatibility

NoUI depends on [Tabby](https://github.com/adoptai/tabby) for authenticated
browser sessions. To keep behavior deterministic across releases, NoUI pins a
specific SHA on Tabby's `tabby-noui` branch via a git submodule.

- **Current pin:** `tabby-noui @ d3c7373`
- **Branch:** `tabby-noui`
- **Submodule path:** `tabby/` (inside this repository)

Using a different Tabby revision is unsupported. The submodule is for local
dev/test; at runtime NoUI talks to whatever Tabby `TABBY_API_URL` points at. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the submodule-bump workflow.

------------------------------------------------------------------------

## 🧩 Capture (no Chrome extension)

Capture happens **server-side inside Tabby** — there is no Chrome extension to
install. Two modes:

- **VNC** — `capture_record.py` provisions a recording session; you drive a real
  browser in the VNC viewer and click "Finish & export".
- **Autopilot** — `capture_autopilot.py` / `AutopilotSession` drives the browser
  through `POST /execute/browser`; NoUI synthesizes the bundle from the inline
  HAR plus the commands it issued.

Both produce the same `{har, click_events, url_events}` bundle that the compiler
consumes.

------------------------------------------------------------------------

## 📚 Project docs

- [docs/HANDOFF.md](docs/HANDOFF.md) — install & use the skill with Tabby (start here)
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, PR process, bumping the Tabby submodule
- [SECURITY.md](SECURITY.md) — responsible disclosure
- [CHANGELOG.md](CHANGELOG.md) — release history
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

------------------------------------------------------------------------

## 🔥 Status

Early open source --- expect rough edges.
Contributions welcome.

------------------------------------------------------------------------

## 📢 Closing

Computer-use agents were step one.

**NoUI is what comes next.**
