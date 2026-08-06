# Pillar 2 — Compile

Turn a capture bundle into assets. **Deterministic — no LLM calls.**

## Workflow → MCP and/or Skill
`noui_core.compile.workflow.compile_workflow_bundle(target="mcp"|"skill"|"both")`:

- `har_to_tools` converts HAR entries → tool definitions (regex-based param/path inference).
- `server_generator` writes a FastMCP server tree (`server.py`, `tools.json`, `manifest.json`, `API.md`, `operations/<tool>.py`, `noui_runtime/`).
- `skill_generator` writes an installable Skill (`SKILL.md`, `operations/`, `noui_runtime/`).
- `api_doc_generator` writes `API.md` (never calls external services).

Outputs go under `NOUI_WORKBENCH_DIR` (default `skills/noui/workbench/`): `mcp_servers/<app>/<server_id>/` and `skills/<app>/`.

### Execution modes (`--execution-mode`)
- `tabby` (default) — operations run **inside Tabby's browser** via `POST /execute/fetch`; cookies ride on `credentials:'include'`, real TLS fingerprint. Best against anti-bot.
- `http` — legacy in-process `httpx` + resolved credentials. Use only when in-browser execution is impossible (CORS, server-to-server).
- `harness` (skill only) — emits operation cards + `operations.json`, **no transport code**; the Adopt Agent Harness routes via its own `call_web_api`.

The vendored `noui_runtime/execute.py` (from `noui_core.activate.execute_adapter`) is what makes a generated asset self-contained and Tabby-`/execute`-backed.

### Browser-driven skills (`--browser-driven`) — for apps replay can't reach
Some single-page apps encrypt **every request body in the page's JavaScript** with a per-session key fetched at load time (and/or stamp each request with rotating per-request headers). A recorded request is then unreplayable: the body is an opaque `{data, key}` blob only the live page can produce, and the headers die with the recording. ICICI net-banking is the canonical case — a normal HAR-replay skill compiles dozens of operations that **all 403 at run time**, and even a perfect capture of the target endpoint is dead on arrival.

For those apps, compile a **browser-driven** skill: it drives the page (reach each data page via in-app `click_by_text`, then `get_page_summary` — never a full-page `navigate`/reload, which expires the SPA session) via the harness `call_web_browser` tool and reads the DOM the page already fetched and decrypted, so the encryption is irrelevant. Its "operations" are the readable data pages from the recording, not replayed API calls. Requires a bound `--profile-slug` (it drives an authenticated session).

**This is auto-detected — you usually don't pass the flag.** `capture_import` inspects the HAR for the unreplayable fingerprint (opaque `{data,key}` bodies on the app's own origin + a key-fetch endpoint like `/getKeys`) via `noui_core.compile.unreplayable.detect_unreplayable`, and switches to browser mode on its own, printing *why* (surfaced under `result["browser_detection"]`). Overrides: `--browser-driven` forces it on; `--no-auto-browser` forces the legacy replay compile off.

**When a user asks you to build a skill for a bank or other portal that encrypts its traffic, expect browser mode** — let the auto-detection choose, and tell the user the skill will read the rendered page rather than replay APIs (so it needs them to sign in through the sign-in card, and reads promptly because such portals idle out fast).

### Auth model (`--auth-type`) — declared, not guessed
How the app authenticates is **declared** at compile, not inferred from the HAR:

- `session` (default) — a login/session backs the app → `tabby_credentials`. A separately-recorded login can never be miscategorised as static (this replaced a HAR heuristic that mis-fired when the login was captured apart from the workflow, so the workflow HAR had no `Set-Cookie`).
- `api-key` — a static key sent on every request, **no login recorded** → `static_secret_header`. The auth header (`--api-key-header`, default `Authorization`) is emitted as a `${SECRET:name}` placeholder; an admin registers the value in the harness secret store (`AGENT_HARNESS_WEB_API_SECRETS`). NoUI never records or holds the key.
- `auto` — legacy `_is_static_api_key_app` HAR heuristic (escape hatch).

`generate_auth_plan(declared_strategy=…)` implements the override; `auto`/`None` keeps the heuristic. (Distinct from `--auth-mode`/`NOUI_TABBY_AUTH_MODE`, which is the runtime **token** mode — see [auth-modes](auth-modes.md).)

### Combined capture (the default)
A capture holding both halves splits at the login boundary (`noui_core.capture.split`) and runs both compilers: register the login App Template, then `compile_workflow_bundle(..., auth_type="session")` bound to the new profile. `capture_import.py` routes there on its own — from the provision ledger, or from content classification (`noui_core.capture.classify`), never from Tabby's `recording_mode`; `--mode combined` (legacy alias `--combined`) forces it. See [pillar-1-capture](pillar-1-capture.md).

## Login → App Template + ServiceProfile drafts
`noui_core.compile.login.compile_login_bundle` → `login_assets.generate`: builds `application_draft` (target URLs, login DSL from click/url events, egress allowlist, keepalive) and `service_profile_draft` (`profile_id`, `credential_types`, `target_domains`). `build_app_template_payload` emits a tenant-wide App Template for federated auto-provisioning.

Re-compile a saved bundle without re-recording: `scripts/compile_workflow.py` / `scripts/compile_login.py`.

The compile output is a **raw** mirror of the recording. Next, run the agent-driven **[generalize](generalize.md)** pass — prune noise operations and test the rest until they work — before activating.

See also: [generalize](generalize.md), [pillar-3-activate](pillar-3-activate.md).
