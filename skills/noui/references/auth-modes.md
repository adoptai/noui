# Auth modes: agent_token vs platform_jwt

NoUI resolves credentials two ways. Mode is set by `NOUI_TABBY_AUTH_MODE`
(explicit) or auto-detected (`platform_jwt` when `ADOPT_*` are present, else
`agent_token`). Bearer tokens are cached per mode.

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

## Where it applies
- **Recording + register**: `agent_token` (recording) / `TABBY_ADMIN_TOKEN` (register).
- **Execution**: the vendored `noui_runtime/auth.py` in each generated asset
  mirrors these two modes for `POST /execute/fetch`.

## Profile ownership consequence
A profile created under `agent_token` is NULL-owner (creator-only vs tenant
sharing depends on Tabby config); a profile created/resolved under
`platform_jwt` is scoped to `owner_user_id`. If a tool 404s on the profile,
check the auth mode used to create vs resolve it.

See also: [pillar-3-activate](pillar-3-activate.md), [tabby-setup](tabby-setup.md).
