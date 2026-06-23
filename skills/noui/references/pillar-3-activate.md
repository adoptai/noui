# Pillar 3 — Activate

Make compiled assets usable, with Tabby `/execute` as the engine.

## Register + promote a login profile
`noui_core.activate.register.register_login(result, promote=…)`:

1. `POST /apps` (application_draft) → `app_id`.
2. `POST /admin/profiles` (service_profile_draft + app_id + version) → `profile_db_id`, state `STAGING`.
3. `--promote` → `POST /admin/profiles/{id}/promote` twice (STAGING → CANARY → ACTIVE).

> The runtime resolver matches **ACTIVE/CANARY only** — a STAGING-only profile 404s at the first tool call. Promote before use.

### Why this step needs an admin token (not agent client/secret)
Register/promote is the **only** part of NoUI that an agent client/secret cannot do. Those credentials mint an `Agent`-role token, and Tabby gates these endpoints higher:

| Endpoint | Required role | Agent token? |
|---|---|---|
| `POST /apps` | `Admin`, `Operator` | ❌ 403 |
| `POST /admin/profiles` | `Admin` | ❌ 403 |
| `POST /admin/profiles/{id}/promote` | `Admin` | ❌ 403 |

So `register_login` reads **`TABBY_ADMIN_TOKEN`** (`resolve_admin_token`) and fails fast if it's unset. Everything else — recording, Autopilot (`/execute/browser`), and running generated tools (`/execute/fetch`) — accepts the `Agent` role and works on agent client/secret alone. To avoid an admin token entirely, use the cloud **`platform_jwt`** route (per-user JWT + App-Template auto-provisioning) instead of these admin endpoints — see [auth-modes](auth-modes.md).

CLI: `scripts/activate_register.py compiled-login.json --promote` (or fold into `capture_import.py <id> --promote`).

## Verify auth before use
`scripts/activate_verify.py <server_dir>` → `noui_core.activate.verify.verify_before_install`. Deterministic-first repairs; statuses: `PASS`, `REPAIR_APPLIED`, `NEEDS_SECRET`, `UNSUPPORTED`. No `auth_plan.json` ⇒ `PASS` (unauthenticated server).

## Install into an agent (agnostic)
`scripts/activate_install.py <skill_dir> <agent> [--project]` → `noui_core.activate.install.install_skill`. Agents and their skills roots:

| agent | global | project (`--project`) |
|---|---|---|
| `claude-code` | `~/.claude/skills` | `./.claude/skills` |
| `codex` / `agents` | `~/.agents/skills` | `./.agents/skills` |
| `cline` | `~/.cline/skills` | `./.cline/skills` |
| `opencode` | `~/.config/opencode/skills` | `./.opencode/skills` |

## Execute runtime
Generated assets call Tabby `POST /execute/fetch` (and `/execute/browser`) via the vendored `noui_runtime/execute.py`. Auth modes: `agent_token` (shared, NULL-owner) or `platform_jwt` (per-user, carries `owner_user_id` → per-user profile + template auto-provision). See [auth-modes](auth-modes.md).

See also: [pillar-2-compile](pillar-2-compile.md), [tabby-setup](tabby-setup.md).
