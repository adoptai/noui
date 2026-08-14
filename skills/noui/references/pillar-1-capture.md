# Pillar 1 — Capture

Record a login or workflow as a **bundle** = `{har, click_events, url_events, cookies}`. Capture runs **server-side inside Tabby's worker** — NoUI never touches the page.

Workflow recordings from Tabby `schema_version >= 5` carry considerably more, and the browser compiler depends on all of it:

| Field | What it is | Why it matters |
|---|---|---|
| `click_events[].candidates` | Ranked ways to address the element, each with `match_count` — how many nodes it matched **at record time** | A candidate matching several nodes cannot identify that element. This is what turns ambiguity into a compile-time fact instead of a production misclick. |
| `click_events[].element` | Role, accessible name, visibility, occlusion, bounding box | Occlusion is the overlay-swallows-the-click problem, answered at the only moment it is knowable. |
| `click_events[].outcome` | What the interaction caused: navigation and where, requests fired, settle time, download started | Causality is recorded rather than inferred from timestamps, and it becomes each step's postcondition. |
| `download_events` | Files that arrived, with name and origin page | A `blob:` download never touches the network, so HAR cannot see it. For most browser skills this is the success condition. |
| `url_events[].page_id` | Which document a transition happened in (`0` = the page the human started on) | Bank portals open statements in a new tab; without this the human's clicks there are invisible. |
| `schema_version` | Capture-format revision | Absent means a pre-`seq` bundle. Detect fields per event rather than dispatching on this — a bundle can lose fields in transit. |

**HAR content depends on the skill kind.** A `browser_driven` workflow recording reduces the HAR to metadata — method, path, status, timing; no bodies, headers or query strings — because a browser skill never replays a request and a bank portal's payloads have no business sitting in a bundle. Login recordings and non-browser workflow recordings keep the full HAR, which is what the replay compiler needs.

## Two modes

### VNC (manual, full-fidelity)
A human drives a real browser in a Tabby VNC session.

```bash
# Default (no --mode): ONE session for the login AND the workflow — one link, one sign-in.
python scripts/capture_record.py --url https://example.com/login --name example
# → prints session_id + a short recording-viewer link
# open it, SIGN IN, then drive the flow in the same window, click "Finish & export"
python scripts/capture_import.py <session_id> --as skill --name example

# Auth already exists (profile/App Template set up) → the mode defaults to workflow-only:
python scripts/capture_record.py --url https://example.com/app --profile example
python scripts/capture_import.py <session_id> --as both --profile-slug example
```

**Give the human one link at a time.** Recording the halves separately means two links and
two sign-ins; the combined default exists so there is only ever one recording link in play.

`capture_record.py` → `noui_core.capture.recording.start()` → Tabby `POST /recording/sessions`.
`capture_import.py` → `recording.fetch_bundle()` → Tabby `GET /recording/sessions/{id}/bundle`, then compile.

### Autopilot (agent-driven)
The agent drives the browser through Tabby `POST /execute/browser` (`har_start` → `navigate/click/type` → `har_stop`). Because the agent issues the commands, NoUI knows the interaction log and synthesizes the bundle from the inline HAR + that log (`noui_core.capture.validate.validate_har_dict` checks quality). The *driving* is fully headless — the only time a human is involved is the one-time login escalation below.

Implemented in `noui_core.capture.autopilot` (`AutopilotSession` for interactive driving; `run_steps` for scripted runs). It drives via `tabby_client.execute_browser` (Tabby `POST /execute/browser`, `{profile_id, command, params}` + bearer) and synthesizes the bundle from the inline `har_stop` HAR + the click/url events NoUI issued. **No Tabby-side change is required** — the existing `/execute/browser` command set (`navigate`, `click_element`, `type_text`, `har_start/stop`, …) is sufficient.

```python
from noui_core.capture.autopilot import AutopilotSession

ap = AutopilotSession("<profile-slug>")
ap.start_capture()
ap.navigate(url)
ap.click("#go")
bundle = ap.finish()
```

#### Prerequisite: a HEALTHY execute session (+ login escalation)
`/execute/browser` resolves the profile's **healthy** session, so one must be running on an `execute_enabled` app. Bring it up and, if it needs auth, escalate to a human via VNC — **no credentials are stored**:

1. **Scale a session** (admin token): `tabby_client.scale_sessions(app_id, 1, admin_token)` → `POST /apps/{id}/sessions/scale`. The controller reconcile loop (~15s) starts a worker.
2. **Poll status** (agent token): `tabby_client.get_session_status(profile_slug, agent_token)` → `GET /agent/session-status/{profile}`.
   - If the session needs login it reports `hitl_active: true` and `vnc_stream: {url, expires_at}` (state `LOGIN_NEEDED` / `LOGIN_IN_PROGRESS`). **Hand that `vnc_stream.url` to the human** — they open it, complete the login (OTP/password/etc.) in the live browser, and the session proceeds. No `${USERNAME}`/`${PASSWORD}` secret needs to be stored.
   - When `state == "HEALTHY"`, drive with `AutopilotSession`.
3. **Drive → capture → compile**, then scale back to 0 when done.

```python
from noui_core import tabby_client

tabby_client.scale_sessions(app_id, 1, admin_token)
st = tabby_client.get_session_status(profile_slug, agent_token)
if st["hitl_active"]:
    print("Log in here:", st["vnc_stream"]["url"])  # human completes login
# re-poll until st["state"] == "HEALTHY", then AutopilotSession(profile_slug)...
```

> Both tokens must share a tenant (see [tabby-setup](tabby-setup.md)). The driven session must be a non-recording, execute-enabled session (recording sessions reject `/execute/*` with 409). True *server-side* DOM-event drain for `/execute/browser` (full parity with VNC's `recording-stop`) remains an optional Tabby enhancement tracked in `plans/noui/noui-extensionless-autopilot-plan.md`; it is **not** needed for Autopilot workflow capture.

## Session reuse (`--from`)
Record a workflow already authenticated, with **no stored credentials**: seed the recording browser with cookies captured by a prior **login** recording.

```bash
python scripts/capture_record.py --url https://example.com --from <login-session-id>
# --from implies the login already exists, so the mode defaults to workflow-only
```

Tabby pulls the source recording's cookies server-side; they never pass through NoUI.

## Residential proxy egress (`--residential-proxy`)
Route the recorded browser's egress through Tabby's residential proxy (a US residential IP) instead of the default datacenter egress. Use for sites that block or challenge datacenter IPs — e.g. bank portals.

> ⚠️ **Future agents: for any bank / financial institution, ALWAYS use `--residential-proxy`.**
> This applies to every capture that becomes a bank App Template or recording —
> logins, workflows, and combined sessions alike. Banks, brokerages, card issuers,
> and lenders routinely block datacenter IPs or treat a datacenter-IP login as
> fraud (extra challenges, step-up MFA, account locks). Recording such a site
> without the residential proxy is unreliable and can jeopardize the real account.
> When in doubt about whether a site is "financial enough", default to using it.

```bash
python scripts/capture_record.py --mode login --url https://www.pnc.com --residential-proxy
```

Works with any mode (`login`/`workflow`/`combined`) and combines with `--from`/`--profile`. The flag flows to Tabby's `POST /recording/sessions` as `residential_proxy: true`; omitted by default so the recording-shell app default applies. Requires the residential proxy to be configured on Tabby (`EGRESS_UPSTREAM_PROXY_URL` on the egress-proxy) — otherwise egress stays datacenter.

## Combined login + workflow in one session — **the default**
Capture the login **and** the authenticated workflow in a **single** VNC session: one viewer link, one sign-in, no separate login recording and no `--from` seeding.

```bash
# `--mode` omitted → combined (it becomes workflow-only if --profile/--from is given)
python scripts/capture_record.py --url https://example.com/login --name example
# sign in, THEN keep driving the workflow, one "Finish & export"
python scripts/capture_import.py <session_id> --as skill --name example
```

A combined session is provisioned as an ordinary **`login`** session server-side (zero Tabby change), and NoUI does the differentiation at import. **The import needs no flag:** `capture_record.py` writes the declared mode to the provision ledger (`<workbench>/sessions/<session_id>.json`), and `capture_import.py` reads it — see [the mode decision](#how-the-loginworkflow-mode-is-decided) below. `--mode combined` / the legacy `--combined` alias force it explicitly.

The split itself happens at the login boundary (`noui_core.capture.split.split_bundle` — the first stable navigation after the last credential-field interaction): NoUI **registers the login App Template** from the login slice and **compiles the workflow** (`--auth-type session`) bound to that new profile. Splitting *before* tool generation is what keeps the login form-submit request — and its credential body — out of the workflow's tool set. If no login segment is present (no credential fields), it falls back to compiling the bundle workflow-only.

Record the halves separately (`--mode login`, then `--mode workflow --from <session>`) only when you need the login registered before spending the user's time on the workflow. It costs an extra link and an extra sign-in, which is why it is not the default.

## How the login/workflow mode is decided
Tabby's `recording_mode` on the drained bundle is **never** used — a warm-pool recording session always reports `login` regardless of what was provisioned (see `noui_core.capture.classify`). `capture_import.py` decides in this order:

1. explicit `--mode {login,workflow,combined}` (or the legacy `--combined` alias);
2. the **provision ledger** — what `capture_record.py` asked Tabby for;
3. **content classification** — credential-field interactions, and whether the human kept driving afterwards.

It prints which source won, and warns when the content suggests something else. `python scripts/bundle_inspect.py <bundle.json>` shows all three side by side.

## Duplicate-template pre-check (`--name`)
Before provisioning a session that would capture a **login** (the combined default, or `--mode login`), pass `--name`:

```bash
python scripts/capture_record.py --url https://example.com/login --name example
```

This checks Tabby's existing App Templates (`GET /admin/app-templates`, via `tabby_client.list_app_templates`) for one with the **same origin** (from `login_config.login_url` or `export_policy.target_urls`) **and** a similar `name` (`noui_core.capture.template_match.find_similar_templates`, fuzzy match + substring check, threshold 0.6). Both signals are required — same host alone is common across unrelated logins, and name similarity alone proves nothing about the site.

If a match is found, the login capture is **skipped** (no session is provisioned): the matching template's name/slug/id are printed along with the command to go straight to workflow recording against that profile —

```bash
python scripts/capture_record.py --url <workflow-url> --profile <slug>
```

— since the login is already covered. Pass `--force` to record the login anyway (e.g. deliberately re-capturing a login to widen or refresh it). Without `--name`, the pre-check is skipped entirely (nothing to compare against).

## Login profiles: manual VNC takeover (the supported flow) + auto-resolve
A login profile compiled in **takeover** mode (`capture_import --credential-mode takeover`, the default) uses `credential_ref: manual:` and a minimal login DSL — the supported Tabby flow, mirroring the Salesforce template:

```
goto(login_url)
request_human_input(input_type=confirm)         # VNC link + "log in manually" prompt
wait_for_url(pattern=<post-login>, on_failure=request_help confirm)
```

The `wait_for_url` is the important part: when the browser reaches the post-login URL, **Tabby auto-resolves the HITL** — the human logs in and the flow continues *without clicking "Mark as Resolved"* every time (this is what fixes the repeated-click complaint and the login desync). If the URL can't be auto-verified, `on_failure: request_help` falls back to a confirm.

The `goto` lands on the recording's **initial page** (default `login_url` = the first recorded URL), and the human drives everything from there — the worker does **not** replay recorded clicks in takeover mode. So don't point `--url` at a deep login page (e.g. `/login`): that skips initial-page interactions the human needs — cookie/region banners, anti-bot **sliders**, "sign in" entry points. Start where the recording started.

It only works when the pattern **distinguishes the logged-in page from the login page**. NoUI auto-derives it from the recording's landing URL, but for **same-origin** apps (login and app on the same host/path, e.g. Expedia) auto-derivation is unreliable and is skipped (a review item is emitted) — pass `--post-login-url-pattern '<glob>'` (e.g. `**/lightning/**`) so the logged-in URL matches but the login page does not. NoUI does **not** submit username/password (that flow is unsupported); the human authenticates in the VNC viewer.

## Bundles are always saved — keep them for generalization
Both capture scripts persist the raw bundle to `workbench/bundles/<name>.json` via `noui_core.capture.bundle.save_bundle` (Autopilot saves the synthesized bundle; `capture_import` saves the drained one). **This is not optional and the saved bundle must be kept**, because:

- The **bundle is the only source for regenerating/generalizing** an asset — renaming tools to readable names, parameterizing request bodies, recovering a body the first compile dropped (e.g. a GraphQL query lost to `/graphql` dedup), stripping telemetry/ad calls, or rewriting for anti-bot. A compiled MCP/Skill **cannot** be re-generalized; recompile from the bundle: `python scripts/compile_workflow.py workbench/bundles/<name>.json --as both`.
- **Recording bundles expire server-side** (Tabby TTL) — once gone, the only way back is a full re-record. The local saved copy is the durable one.

`--save-bundle <path>` overrides the location; otherwise it lands in `workbench/bundles/`.

## Bundle validation
`noui_core.capture.bundle.validate_bundle` checks **shape only** — a HAR must be present. It deliberately does *not* validate or return `recording_mode`: that field is unreliable (see [the mode decision](#how-the-loginworkflow-mode-is-decided)), so it can neither classify a bundle nor fail one. `count_sensitive_unredacted` blocks import if passwords/OTP weren't redacted. `validate.validate_har_dict` reports API-call count, domains, and credential-leak / no-mutation warnings.

`python scripts/bundle_inspect.py <bundle.json>` renders all of this — plus the mode block, URL timeline, and the operation set compile would emit — for a saved bundle.

See also: [pillar-2-compile](pillar-2-compile.md), [auth-modes](auth-modes.md), [generalize](generalize.md).
