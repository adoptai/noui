# Pillar 1 — Capture

Record a login or workflow as a **bundle** = `{har, click_events, url_events}` (HAR with response bodies). Capture runs **server-side inside Tabby's worker** — NoUI never touches the page.

## Two modes

### VNC (manual, full-fidelity)
A human drives a real browser in a Tabby VNC session.

```bash
python scripts/capture_record.py --mode workflow --url https://example.com
# → prints session_id + vnc_url
# open vnc_url, drive the flow, click "Finish & export"
python scripts/capture_import.py <session_id> --as both --profile-slug <slug>
```

`capture_record.py` → `noui_core.capture.recording.start()` → Tabby `POST /recording/sessions`.
`capture_import.py` → `recording.fetch_bundle()` → Tabby `GET /recording/sessions/{id}/bundle`, then compile.

### Autopilot (agent-driven)
The agent drives the browser through Tabby `POST /execute/browser` (`har_start` → `navigate/click/type` → `har_stop`). Because the agent issues the commands, NoUI knows the interaction log and synthesizes the bundle from the inline HAR + that log (`noui_core.capture.validate.validate_har_dict` checks quality). The *driving* is fully headless — the only time a human is involved is the one-time login escalation below.

Implemented in `noui_core.capture.autopilot` (`AutopilotSession` for interactive driving; `run_steps` for scripted runs). It drives via `tabby_client.execute_browser` (Tabby `POST /execute/browser`, `{profile_id, command, params}` + bearer) and synthesizes the bundle from the inline `har_stop` HAR + the click/url events NoUI issued. **No Tabby-side change is required** — the existing `/execute/browser` command set (`navigate`, `click_element`, `type_text`, `har_start/stop`, …) is sufficient.

```python
from noui_core.capture.autopilot import AutopilotSession
ap = AutopilotSession("<profile-slug>")
ap.start_capture(); ap.navigate(url); ap.click("#go"); bundle = ap.finish()
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
    print("Log in here:", st["vnc_stream"]["url"])   # human completes login
# re-poll until st["state"] == "HEALTHY", then AutopilotSession(profile_slug)...
```

> Both tokens must share a tenant (see [tabby-setup](tabby-setup.md)). The driven session must be a non-recording, execute-enabled session (recording sessions reject `/execute/*` with 409). True *server-side* DOM-event drain for `/execute/browser` (full parity with VNC's `recording-stop`) remains an optional Tabby enhancement tracked in `plans/noui/noui-extensionless-autopilot-plan.md`; it is **not** needed for Autopilot workflow capture.

## Session reuse (`--from`)
Record a workflow already authenticated, with **no stored credentials**: seed the recording browser with cookies captured by a prior **login** recording.

```bash
python scripts/capture_record.py --mode workflow --url https://example.com --from <login-session-id>
```

Tabby pulls the source recording's cookies server-side; they never pass through NoUI.

## Combined login + workflow in one session (`--mode combined`)
Capture the login **and** the authenticated workflow in a **single** VNC session — no separate login recording, no `--from` seeding.

```bash
python scripts/capture_record.py --mode combined --url https://example.com/login
# sign in, THEN keep driving the workflow, one "Finish & export"
python scripts/capture_import.py <session_id> --combined --as skill --name example
```

Tabby's `recording_mode` is behaviorally inert, so a combined session is provisioned as an ordinary **`login`** session (zero Tabby change). NoUI does the differentiation at import: `--combined` splits the one bundle at the login boundary (`noui_core.capture.split.split_bundle` — the first stable navigation after the last credential-field interaction), then **registers the login App Template** from the login slice and **compiles the workflow** (`--auth-type session`) bound to that new profile. Splitting *before* tool generation is what keeps the login form-submit request — and its credential body — out of the workflow's tool set. If no login segment is present (no credential fields), `--combined` falls back to compiling the bundle workflow-only.

Prefer the split flow (`--mode login` then `--mode workflow`) when you want to confirm the login registered before recording the workflow.

## Duplicate-template pre-check (`--mode login`)
Before provisioning a **login** recording session, pass `--name`:

```bash
python scripts/capture_record.py --mode login --url https://example.com/login --name example
```

This checks Tabby's existing App Templates (`GET /admin/app-templates`, via `tabby_client.list_app_templates`) for one with the **same origin** (from `login_config.login_url` or `export_policy.target_urls`) **and** a similar `name` (`noui_core.capture.template_match.find_similar_templates`, fuzzy match + substring check, threshold 0.6). Both signals are required — same host alone is common across unrelated logins, and name similarity alone proves nothing about the site.

If a match is found, the login capture is **skipped** (no session is provisioned): the matching template's name/slug/id are printed along with the command to go straight to workflow recording against that profile —

```bash
python scripts/capture_record.py --mode workflow --url <workflow-url> --profile <slug>
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
`noui_core.capture.bundle.validate_bundle` enforces shape (`recording_mode` ∈ {login, workflow}, HAR present); `count_sensitive_unredacted` blocks import if passwords/OTP weren't redacted. `validate.validate_har_dict` reports API-call count, domains, and credential-leak / no-mutation warnings.

See also: [pillar-2-compile](pillar-2-compile.md), [auth-modes](auth-modes.md).
