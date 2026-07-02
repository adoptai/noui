# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Dynamic bearer/CSRF header capture for `tabby_credentials` logins whose apps
  attach a client-managed header (not just cookies) — a login's `target_urls`
  now cover the post-login app hosts (not just the login origin), the
  auth-strategy heuristic checks the paired login's declared headers before
  falling back to a static secret, and an already-registered login's scope
  can be widened after the fact when a later workflow capture needs more
  hosts. Fixes API replay for enterprise SPAs like QuickBooks Online, which
  previously required a fallback to (much more expensive) UI-driving.
- Codegen: a GraphQL-style object/array body param (e.g. `variables`) is no
  longer double-JSON-encoded on the wire, and `resolve_auth()` is now called
  for `tabby_credentials` strategy (both Skill and MCP outputs) whenever the
  auth plan declares required headers, not just for `static_secret_header`.

## [2.0.0] - 2026-06-23

> **Three-pillar consolidation (this release).** NoUI is now a single,
> agent-agnostic **skill bundle** at `skills/noui/` (Capture → Compile →
> Activate). The Chrome extension, the FastAPI backend daemon, and the
> monolithic CLI are removed; the deterministic compiler/runtime moved into
> `noui_core`, the CLI's useful commands became thin `scripts/`, and the
> `ANTHROPIC_API_KEY` dependency is gone (the record→compile pipeline makes no
> LLM calls). The standalone `compiler/`, `cli/`, and backend items listed
> further below are **superseded** by this section.

### Added — consolidation

- **Single skill bundle `skills/noui/`**: `noui_core/` (the moved, deterministic
  compiler + runtime, organized as `capture/`, `compile/`, `activate/`, plus
  `config`, `tabby_client`, `auth`), CLI-free `scripts/`, `references/`, a
  self-contained `pyproject.toml`, and `SKILL.md`. Installable agent-agnostically
  (`npx skills add … --skill noui`); the only runtime dependency is a reachable
  Tabby.
- **Pillar 1 — Capture**: `capture.recording` (VNC) and `capture.autopilot`
  (drives Tabby `POST /execute/browser`; `AutopilotSession`/`run_steps`
  synthesize a bundle from inline HAR + the commands issued). Capture **bundles
  are always saved** to `workbench/bundles/` (source of truth for
  generalizing/regenerating; recording bundles expire server-side).
- **Pillar 2 — Compile**: `compile.workflow` / `compile.login` orchestration over
  the generators; login `credential_types` are derived from the bundle's captured
  `cookies` (Tabby sanitizes `Set-Cookie` out of the HAR).
- **Pillar 3 — Activate**: `activate.register` (App + ServiceProfile, promote
  STAGING → **CANARY**, optional tenant-wide App Template), `activate.verify`
  (auth dry-run), `activate.install` (agent-agnostic skill install). Registration
  auto-targets the **agent token's tenant** via an Admin `tenant_id` override so
  the agent can resolve/drive the result.
- **Manual VNC login (takeover)**: `credential_ref: manual:` with a single
  `request_human_input(confirm)` + a `wait_for_url` **auto-resolve** step
  (Salesforce-template pattern) — reaching the post-login URL completes login
  with no repeated "Mark as Resolved" clicks. `--post-login-url-pattern` sets the
  distinguishing glob (auto-derived from the recording when login/landing differ;
  required for same-origin apps). `resolve_panel_url` appends `?from=mcp` so
  Tabby's viewer renders the HITL panel. No username/password is ever stored.
- **Tabby session helpers**: `tabby_client.scale_sessions`,
  `get_session_status` (agent-accessible; surfaces the HITL VNC URL),
  `register_app_template`, `execute_browser`.
- `docs/HANDOFF.md` — install + use guide for a colleague; `skills/noui/.env.example`.

### Removed — consolidation (BREAKING)

- The Chrome extension (`extension/`), the FastAPI backend daemon and all routers,
  and `backend/elicitation/*` (the only `ANTHROPIC_API_KEY` consumer — the `/chat`
  assistant). `ANTHROPIC_API_KEY` is no longer required.
- `cli/main.py` (6685-line HTTP client) — dissolved into `skills/noui/scripts/`.
- The 7 old `noui-*` orchestration skills — consolidated into the single
  `skills/noui` bundle (demo skills kept as siblings; MCP examples kept at repo
  root).
- `local_check.sh` and the `extension-lint` CI job (the extension is gone).

### Added

- **Cloud Tabby auth via platform token-exchange.** Generated MCP servers/Skills
  and the compile-time auth verifier can authenticate against a cloud/staging
  Tabby by exchanging a platform Personal Access Token for a platform JWT
  (`POST /v1/users/api-token`), then for a Tabby JWT (`POST /auth/token-exchange`),
  in addition to the local `/auth/agent-token` flow. The flow is selected by
  `NOUI_TABBY_AUTH_MODE` (auto-detects `platform_jwt` when `ADOPT_API_URL` +
  `ADOPT_CLIENT_ID` + `ADOPT_CLIENT_SECRET` are set, else `agent_token`). The
  exchanged bearer is cached in-process until shortly before expiry.
- `noui tabby setup --cloud` — verifies the PAT → platform-JWT → Tabby round-trip
  and writes the cloud env vars to `.env` (no local Tabby or admin token needed).

### Changed

- **BREAKING: `TABBY_API_HOST` and `TABBY_API_URL` collapsed into a single
  `TABBY_API_URL`.** Every env read now uses `TABBY_API_URL`; `TABBY_API_HOST` is
  no longer read (no fallback). Rename it in your `.env`/environment.
- **Default execution mode for generated MCP servers and Skills is the `tabby`
  mode** (`--execution-mode tabby`). Generated operations call Tabby's
  `POST /execute/fetch` endpoint over plain HTTP; the Tabby worker runs
  `fetch(url, {credentials: 'include'})` inside the real authenticated browser,
  so cookies and TLS fingerprint come from the browser. Sidesteps Akamai /
  Cloudflare false positives that fire on Python HTTP clients. (This mode was
  previously named `cdp`; renamed to `tabby` because the runtime no longer opens
  a client-side CDP/WebSocket connection.)
- **Removed the dead client-side CDP-WebSocket adapter**
  (`compiler/runtime/cdp_adapter.py`) and the related `mcp status` CDP-reachability
  probe. `--execution-mode cdp` is no longer accepted — use `tabby` (the default).
- `auth.execution_strategy` added to `manifest.json` (additive, non-breaking):
  `"tabby_execute_fetch"` under the default; mirrors `auth.strategy` under
  `--execution-mode http`. Existing readers of `auth.strategy` are unaffected.
- `/noui-record-workflow` skill documents the `tabby` default under *How
  Execution Works*; `/noui-generalize` reframed around hand-edit cases on top
  of the default (SPA DOM scraping, HITL login). New `/noui-tabby-integration`
  reference skill documents Tabby provisioning, `owner_user_id` scoping, the
  execute runtime, and the end-to-end integration gaps.
- Compiler now rejects empty, malformed, or no-API HARs early with
  `HarValidationError` instead of silently generating MCP servers or Skills
  with zero tools. The workflow export endpoint (`POST /workflow/.../export`)
  returns `422 Unprocessable Entity` with the validation message for these
  cases and reserves `500` for unexpected bugs. No output artifacts
  (`server.py`, `tools.json`, `SKILL.md`, `manifest.json`) are written on
  failure.
- CLI now loads the repo-root `.env` (matching `backend/config.py`) so
  `TABBY_API_HOST` set in `.env` is honoured by every `noui` subcommand
  instead of being silently ignored. Values without a scheme (e.g.
  `localhost:8080`) are normalised to `http://localhost:8080` and trailing
  slashes are stripped, eliminating opaque `urlopen` failures.

### Added

- `compiler/runtime/execute_adapter.py` — generates `noui_runtime/execute.py` in
  tabby-mode output, exposing `execute_fetch` and `execute_browser` (calls Tabby's
  `/execute/*` endpoints over plain HTTP).
- `--execution-mode {tabby,http}` flag on `noui workflow export` and
  `noui autopilot export`; corresponding query param on the backend export
  endpoint.
- Explicit `httpx` runtime dependency in `pyproject.toml` (was previously
  transitive). `websockets` remains for the CLI's worker CDP-navigation bridge.
- 18 new tests covering tabby-default invariants and the `--execution-mode http`
  opt-in for both MCP and Skill outputs.

### Notes

- Existing generated servers under `workbench/mcp_servers/` are **not**
  rewritten. Re-exporting an old recording will produce tabby-mode output;
  pass `--execution-mode http` to reproduce the legacy shape.
- `auth.strategy` is unchanged and remains the credential-source descriptor
  (`tabby_credentials` / `static_secret_header`). Only execution mechanics
  changed, not credential classification.

## [1.0.0] - 2026-04-17

Initial open-source release.

### Added

- Public MIT license.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`.
- Tabby pinned as a git submodule tracking the `tabby-noui` branch at SHA
  `d212467`. NoUI's CLI now resolves `TABBY_DIR` to the in-repo submodule by
  default; set `TABBY_DIR` to point at a sibling checkout.
- Issue and pull-request templates under `.github/`.
- Dependabot configuration for `pip` and `github-actions`.
- Secrets-scan and extension-lint jobs in CI.
- Chrome extension: configurable backend URL via popup Settings and
  `homepage_url` in the manifest.

### Changed

- Unified version to `1.0.0` across `pyproject.toml`, `extension/manifest.json`,
  and this changelog.
- Replaced internal fixture references (`adopt-bank` → `example-bank`).

[Unreleased]: https://github.com/adoptai/noui/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/adoptai/noui/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/adoptai/noui/releases/tag/v1.0.0
