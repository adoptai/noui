# Auth modes: agent_token vs platform_jwt

> **Not to be confused with `--auth-type`.** This page is about the runtime
> **token** mode — *how NoUI/the asset authenticates to Tabby* (`agent_token` vs
> `platform_jwt`, set by `--auth-mode` / `NOUI_TABBY_AUTH_MODE`). That is a
> different axis from **`--auth-type`** (`session` | `api-key` | `auto`), which
> declares *how the target app authenticates* — the credential strategy baked
> into the compiled asset. See [pillar-2-compile](pillar-2-compile.md#auth-model---auth-type--declared-not-guessed).

NoUI resolves credentials via `NOUI_TABBY_AUTH_MODE` (explicit) or auto-detection
(`platform_jwt` when `ADOPT_*` are present, else `agent_token`); a third value,
`broker`, is injected when running inside the Agent Harness (below). Bearer
tokens are cached per mode.

## agent_token (default — local / self-host)
- `POST /auth/agent-token` with `TABBY_CLIENT_ID` / `TABBY_CLIENT_SECRET`.
- No `owner_user_id` → **shared (NULL-owner) reach**: any caller with the agent
  token resolves the same profile.
- Simplest for a single-tenant local Tabby.

## platform_jwt (cloud / staging, per-user)
- `POST /v1/users/api-token` (Adopt platform, via a PAT) → `POST /auth/token-exchange` (Tabby).
- Carries `owner_user_id` → Tabby resolves the **per-user** profile and can
  trigger **App Template auto-provisioning** for that user.
- Use when a connection must scale per-user across a tenant.

## broker (Agent Harness)
- Set by the harness (`NOUI_TABBY_AUTH_MODE=broker`): a per-conversation worker
  **forwards the signed-in user's own federated bearer** to Tabby — no
  `TABBY_CLIENT_ID/SECRET`, no `TABBY_ADMIN_TOKEN`, no `ADOPT_*` in the sandbox.
- `resolve_admin_token()` prefers this forwarded bearer for the Editor-gated
  `POST /admin/app-templates` (register) when broker mode is active.

## Where it applies
- **Recording + register**: both resolve in the same preference order —
  `broker` → `platform_jwt` (`ADOPT_*`) → `agent_token` (recording:
  `TABBY_CLIENT_ID/SECRET`) or `TABBY_ADMIN_TOKEN` (register). A pinned
  `NOUI_TABBY_AUTH_MODE` is honoured and errors when its own credentials are
  missing rather than resolving a different identity.
- **Execution**: the vendored `noui_runtime/auth.py` in each generated asset
  mirrors these two modes for `POST /execute/fetch`.

## Profile ownership consequence
A profile created under `agent_token` is NULL-owner (creator-only vs tenant
sharing depends on Tabby config); a profile created/resolved under
`platform_jwt` is scoped to `owner_user_id`. If a tool 404s on the profile,
check the auth mode used to create vs resolve it.

See also: [pillar-3-activate](pillar-3-activate.md), [tabby-setup](tabby-setup.md).
