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

### Auth model (`--auth-type`) — declared, not guessed
How the app authenticates is **declared** at compile, not inferred from the HAR:

- `session` (default) — a login/session backs the app → `tabby_credentials`. A separately-recorded login can never be miscategorised as static (this replaced a HAR heuristic that mis-fired when the login was captured apart from the workflow, so the workflow HAR had no `Set-Cookie`).
- `api-key` — a static key sent on every request, **no login recorded** → `static_secret_header`. The auth header (`--api-key-header`, default `Authorization`) is emitted as a `${SECRET:name}` placeholder; an admin registers the value in the harness secret store (`AGENT_HARNESS_WEB_API_SECRETS`). NoUI never records or holds the key.
- `auto` — legacy `_is_static_api_key_app` HAR heuristic (escape hatch).

`generate_auth_plan(declared_strategy=…)` implements the override; `auto`/`None` keeps the heuristic. (Distinct from `--auth-mode`/`NOUI_TABBY_AUTH_MODE`, which is the runtime **token** mode — see [auth-modes](auth-modes.md).)

### Combined capture (`--combined`)
`capture_import.py --combined` splits one login+workflow bundle (`noui_core.capture.split`) and runs both compilers: register the login App Template, then `compile_workflow_bundle(..., auth_type="session")` bound to the new profile — see [pillar-1-capture](pillar-1-capture.md).

## Login → App Template + ServiceProfile drafts
`noui_core.compile.login.compile_login_bundle` → `login_assets.generate`: builds `application_draft` (target URLs, login DSL from click/url events, egress allowlist, keepalive) and `service_profile_draft` (`profile_id`, `credential_types`, `target_domains`). `build_app_template_payload` emits a tenant-wide App Template for federated auto-provisioning.

Re-compile a saved bundle without re-recording: `scripts/compile_workflow.py` / `scripts/compile_login.py`.

The compile output is a **raw** mirror of the recording. Next, run the agent-driven **[generalize](generalize.md)** pass — prune noise operations and test the rest until they work — before activating.

See also: [generalize](generalize.md), [pillar-3-activate](pillar-3-activate.md).
