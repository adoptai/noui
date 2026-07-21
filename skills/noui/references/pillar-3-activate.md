# Pillar 3 — Activate

Make compiled assets usable, with Tabby `/execute` as the engine.

## Register a login → tenant-wide App Template
`noui_core.activate.register.register_login(result, *, token="", tenant_id="")`:

**Template-first is the only path** — NoUI never creates an App/ServiceProfile directly. `build_app_template_payload` turns the compiled `application_draft` + `service_profile_draft` into one tenant-wide **App Template**, registered via a single `POST /admin/app-templates`. It returns `{template_id, profile_id}`, where `profile_id` is the template's `profile_name_pattern` — the runtime slug the generated ops bake in.

When a federated member (platform JWT, `owner_user_id` set) first requests that slug, Tabby's `autoProvisionFromTemplate` clones a **private, owner-scoped App + Profile straight to ACTIVE** (+ a session) for them. There is **no STAGING/CANARY and no promote step** — per-user provisioning lands directly in ACTIVE, and a directly-created App would be creator-only / tenant-shared with no per-user isolation (exactly what template-first avoids).

### Token / role
`POST /admin/app-templates` is gated by Tabby at the **Editor** role — *not* Admin (the `/admin/` prefix is a URL namespace, not an admin-credential gate). `resolve_admin_token()` resolves an Editor+ bearer in priority order: (1) **broker** — the harness forwards the user's own federated bearer; (2) **platform_jwt** (`ADOPT_API_URL`/`ADOPT_CLIENT_ID`/`ADOPT_CLIENT_SECRET`) — a token-exchanged human account is typically Editor+; (3) **`TABBY_ADMIN_TOKEN`** — a directly-configured bearer for local/self-host. With platform_jwt set up, local dev needs no separately-minted admin token. Everything else — recording, Autopilot (`/execute/browser`), running generated tools (`/execute/fetch`) — works on an `Agent`-role token alone.

CLI: `scripts/activate_register.py compiled-login.json` (or, for a login capture, `capture_import.py <id> --name <app>` registers the template in one step).

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
