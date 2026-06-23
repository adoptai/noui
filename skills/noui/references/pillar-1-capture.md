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
The agent drives the browser through Tabby `POST /execute/browser` (`har_start` → `navigate/click/type` → `har_stop`). Because the agent issues the commands, NoUI knows the interaction log and synthesizes the bundle from the inline HAR + that log (`noui_core.capture.validate.validate_har_dict` checks quality). No human, no VNC viewer.

> Status: the driving + inline-HAR commands are live on Tabby; the NoUI-side synthesize path is `noui_core.capture.autopilot`. True *server-side* drainable capture for `/execute/browser` (parity with VNC's `recording-stop`) is a Tabby enhancement tracked in `plans/noui/noui-extensionless-autopilot-plan.md`.

## Session reuse (`--from`)
Record a workflow already authenticated, with **no stored credentials**: seed the recording browser with cookies captured by a prior **login** recording.

```bash
python scripts/capture_record.py --mode workflow --url https://example.com --from <login-session-id>
```

Tabby pulls the source recording's cookies server-side; they never pass through NoUI.

## Bundle validation
`noui_core.capture.bundle.validate_bundle` enforces shape (`recording_mode` ∈ {login, workflow}, HAR present); `count_sensitive_unredacted` blocks import if passwords/OTP weren't redacted. `validate.validate_har_dict` reports API-call count, domains, and credential-leak / no-mutation warnings.

See also: [pillar-2-compile](pillar-2-compile.md), [auth-modes](auth-modes.md).
