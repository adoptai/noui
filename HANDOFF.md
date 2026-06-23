# NoUI → Tabby: Install & Use (Handoff)

A practical guide to install the NoUI skill bundle and run the full
record → compile → activate → use loop against Tabby. Reflects the flow we
validated end-to-end on cloud Tabby (`tabby-api.adoptai.dev`).

## What this is

NoUI turns real websites into agent-callable tools **without computer-use**:
record a login/workflow through a Tabby browser session, compile it into an MCP
server / Skill / Tabby ServiceProfile, and run it through Tabby's authenticated
`/execute` engine. The whole thing is one agent-agnostic skill bundle at
`skills/noui/`. **Only external dependency: a reachable Tabby.** No backend
daemon, no `ANTHROPIC_API_KEY`.

Three pillars: **Capture** (record) → **Compile** (→ MCP/Skill/Profile) →
**Activate** (register/promote, install, run via `/execute/fetch`).

---

## 1. Prerequisites

- **Python 3.11+**
- **A reachable Tabby** with the `recording/` + `execute/` endpoints (the
  `tabby-noui` build). Cloud (`tabby-api.adoptai.dev`) has them.
- **Credentials, all in the SAME Tabby tenant** (see gotcha #1):
  - `TABBY_CLIENT_ID` / `TABBY_CLIENT_SECRET` — agent client/secret (recording + execute)
  - `TABBY_ADMIN_TOKEN` — admin token (register/promote apps & profiles)
- Repo cloned with submodules (`git clone --recurse-submodules …`) if you want
  the `tabby/` submodule for local dev; not required to talk to a remote Tabby.

## 2. Install

```bash
cd skills/noui
python -m venv .venv && . .venv/bin/activate
pip install -e .          # installs noui_core + deps (httpx, python-dotenv, mcp)
```

All scripts are run from `skills/noui/` as `python scripts/<name>.py …`
(they self-bootstrap `noui_core` onto the path).

## 3. Configure

Create `skills/noui/.env` (gitignored):

```
TABBY_API_URL=https://tabby-api.adoptai.dev
TABBY_CLIENT_ID=agent_cl_...
TABBY_CLIENT_SECRET=secret_sk_...
TABBY_ADMIN_TOKEN=...            # only needed for register/promote
```

Sanity check:

```bash
python - <<'PY'
from noui_core import tabby_client
from noui_core.capture.recording import resolve_agent_token
print("alive:", tabby_client.is_alive())
print("agent token len:", len(resolve_agent_token()))
PY
```

---

## 4. End-to-end flow

### 4a. Record a login (manual VNC)

```bash
python scripts/capture_record.py --mode login --url "https://www.expedia.com/"
# prints session_id + a VNC URL. Open it, log in, click "Finish & export".
```

### 4b. Import the login → App + ServiceProfile (+ App Template)

```bash
python scripts/capture_import.py <session_id> \
  --name expedia --credential-mode takeover --promote --as-template \
  --post-login-url-pattern "**/onboarding**"
```

- `--credential-mode takeover` (default): `credential_ref: manual:` + a single
  `request_human_input(confirm)` + a `wait_for_url` auto-resolve step. **No
  username/password is ever stored** — the human logs in via VNC.
- `--promote` → STAGING → **CANARY** (the runtime resolves ACTIVE *and* CANARY).
- `--as-template` → tenant-wide App Template for per-user auto-provisioning.
- `--post-login-url-pattern` → a glob the **logged-in** URL matches but the login
  page does **not** (e.g. `**/onboarding**`, `**/lightning/**`). Enables
  auto-resolve. Needed for same-origin apps (see gotcha #3); auto-derived
  otherwise.
- Registers into the **agent token's tenant** automatically (gotcha #1).
- The drained bundle is saved to `workbench/bundles/` — keep it (gotcha #5).

### 4c. Bring up a session and log in (HITL / VNC auto-resolve)

There isn't a dedicated script yet (recommended follow-up). Bring a session up,
get the login URL, and wait for it to go HEALTHY:

```bash
python - <<'PY'
import time
from noui_core import tabby_client
from noui_core.activate.register import resolve_admin_token
from noui_core.capture.recording import resolve_agent_token
from noui_core.capture.autopilot import resolve_panel_url

APP_ID  = "<app_id from 4b>"
PROFILE = "expedia"
admin, agent = resolve_admin_token(), resolve_agent_token()
tabby_client.scale_sessions(APP_ID, 1, admin)          # start a worker session
while True:
    st = tabby_client.get_session_status(PROFILE, agent)
    vs = st.get("vnc_stream") or {}
    if vs.get("url"):
        print("LOG IN HERE:", resolve_panel_url(vs["url"])); break   # ?from=mcp added
    if st.get("state") == "HEALTHY": print("HEALTHY"); break
    time.sleep(8)
PY
```

Open the printed URL, log in. When the browser reaches the post-login URL, Tabby
**auto-resolves** the session to HEALTHY — usually no "Mark as Resolved" click
needed. When done, scale back to 0: `tabby_client.scale_sessions(APP_ID, 0, admin)`.

### 4d. Capture a workflow → MCP + Skill

Workflow search etc. runs **inside** the authenticated session.

- **Autopilot** (agent drives, scripted): `scripts/capture_autopilot.py <profile> --steps steps.json --as both` — drives `/execute/browser`, synthesizes a bundle, compiles. Or drive interactively with `noui_core.capture.autopilot.AutopilotSession`.
- **VNC** (human drives): `capture_record.py --mode workflow --from <login-session-id>` (seeds the prior login's cookies), then `capture_import.py <session> --as both --profile-slug <profile>`.

Output lands in `workbench/skills/<app>/` and `workbench/mcp_servers/<app>/…`.

### 4e. Install the skill into an agent and run it

```bash
python scripts/activate_install.py workbench/skills/<app> claude-code   # or codex|cline|opencode|agents
# run an operation (needs a HEALTHY session, step 4c):
python workbench/skills/<app>/operations/<op>.py
```

The operation calls Tabby `POST /execute/fetch` for the profile → returns real,
authenticated data.

---

## 5. Gotchas (learned the hard way)

1. **Same tenant.** `TABBY_ADMIN_TOKEN` and `TABBY_CLIENT_ID/SECRET` must be the
   same Tabby tenant, or register lands where the agent can't see it and every
   agent call 404s "No active profile found". NoUI auto-targets the agent's
   tenant on register; decode a token's `tenant_id` claim to verify.
2. **HTTPS only.** `POST /recording/sessions` rejects `http://` target URLs.
3. **Auto-resolve pattern must distinguish logged-in vs login page.** Same-origin
   root-landing apps (e.g. Expedia) need an explicit `--post-login-url-pattern`;
   different host/path (SSO/Salesforce) auto-derives.
4. **Takeover `goto` starts on the recording's INITIAL page** (the home, where
   cookie banners / anti-bot sliders / the sign-in entry live) — the worker
   doesn't replay recorded clicks, the human does. Don't override `--url` with a
   deep `/login` path.
5. **Keep the bundles.** `workbench/bundles/<name>.json` is the source for
   regenerating/generalizing an asset; recording bundles also expire server-side.
   Recompile from one with `compile_workflow.py <bundle.json>`.
6. **`?from=mcp`** — the Tabby API VNC viewer only shows the HITL/"Mark as
   Resolved" panel when the URL has `?from=mcp` (NoUI appends it via
   `resolve_panel_url`). The adoptwebui platform viewer always shows it.
7. **No credential auto-fill.** Tabby has no supported flow to submit
   username/password; the human authenticates in the VNC. (Confirmed with the
   original contributor.)

## 6. Known follow-ups (not blockers)

- Generated assets include third-party telemetry/ad calls as operations (Datadog,
  DoubleClick, PerimeterX, sponsoredcontent…) — trim to the real API ops, or add
  telemetry-host filtering to `har_to_tools`.
- `/graphql` dedup keeps one representative; a body-bearing search query can be
  collapsed away. Dedup by `operationName` for GraphQL.
- No dedicated session-management script (4c is inline) — a `scripts/session_*`
  helper would make bring-up/teardown clean.
- Agnosticism gate: install + run an op under a non-Claude agent (codex/opencode).

## Quick reference — scripts

| Script | Purpose |
|---|---|
| `capture_record.py --mode login\|workflow` | Provision a VNC recording session |
| `capture_autopilot.py <profile> --steps steps.json` | Drive `/execute/browser`, synthesize a workflow bundle |
| `capture_import.py <session>` | Drain bundle → compile (workflow) or compile+register (login) |
| `compile_workflow.py <bundle.json>` | Re-compile a saved workflow bundle → MCP/Skill |
| `compile_login.py <bundle.json>` | Compile a saved login bundle → App/ServiceProfile drafts |
| `activate_register.py <compiled.json>` | Register a compiled login result (+`--promote`, `--as-template`, `--tenant-id`) |
| `activate_verify.py <mcp_server_dir>` | Deterministic auth dry-run on a generated MCP server |
| `activate_install.py <skill_dir> <agent>` | Install a generated skill into an agent |

See `skills/noui/SKILL.md` and `skills/noui/references/` for the full docs.
