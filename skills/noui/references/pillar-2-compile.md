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

## Login → App Template + ServiceProfile drafts
`noui_core.compile.login.compile_login_bundle` → `login_assets.generate`: builds `application_draft` (target URLs, login DSL from click/url events, egress allowlist, keepalive) and `service_profile_draft` (`profile_id`, `credential_types`, `target_domains`). `build_app_template_payload` emits a tenant-wide App Template for federated auto-provisioning.

Re-compile a saved bundle without re-recording: `scripts/compile_workflow.py` / `scripts/compile_login.py`.

See also: [pillar-3-activate](pillar-3-activate.md).
