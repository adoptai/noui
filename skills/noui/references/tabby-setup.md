# Pointing NoUI at Tabby

NoUI talks to whatever Tabby instance the environment names. Config is read by
`noui_core.config.settings` from the environment / a `.env` at the bundle root.

## Local / self-host (agent_token)
```
TABBY_API_URL=http://localhost:8000
TABBY_CLIENT_ID=<minted by your Tabby setup>
TABBY_CLIENT_SECRET=<minted by your Tabby setup>
TABBY_ADMIN_TOKEN=<admin token>     # only to register App Templates (Activate); Editor role suffices
```
`TABBY_CLIENT_ID/SECRET` are exchanged for an agent bearer token
(`tabby_client.get_agent_token` → `POST /auth/agent-token`) used for recording
and `/execute`. `TABBY_ADMIN_TOKEN` is used for `POST /apps` and
`POST /admin/profiles`.

## Cloud / staging (platform_jwt)
```
TABBY_API_URL=https://<cloud-tabby>
NOUI_TABBY_AUTH_MODE=platform_jwt
ADOPT_API_URL=https://api.adopt.ai
ADOPT_CLIENT_ID=<platform PAT id>
ADOPT_CLIENT_SECRET=<platform PAT secret>
```
The platform PAT mints a platform JWT, exchanged for a Tabby JWT that carries
`owner_user_id` (per-user profiles + template auto-provision). No admin token.

## Tenant alignment (admin token vs agent token)
`TABBY_ADMIN_TOKEN` (registers App Templates) and
`TABBY_CLIENT_ID/SECRET` (the agent token used to record + execute) may belong to
**different** Tabby tenants. If a profile is created in the admin's tenant but
the agent resolves a different one, every agent-token call
(`/agent/session-status`, `/execute/*`) 404s with *"No active profile found"*.

NoUI handles this automatically: `register_login` (and `capture_import` /
`activate_register`) **default the create calls to the agent token's tenant**
via Tabby's Admin `tenant_id` override — so the App/Profile/Template land where
the agent can resolve + drive them, even with a cross-tenant admin token.
Override with `--tenant-id <id>` if you need a specific tenant. (Decode a token's
`tenant_id` claim — middle JWT segment — to check; `noui_core.auth.tenant_id_from_token`.)

Targets must be **HTTPS** (`https://…`) — `POST /recording/sessions` rejects
`http://` URLs.

## Health check
`noui_core.tabby_client.is_alive()` probes `GET /health/live`. If recording or
execute calls fail, confirm Tabby is reachable at `TABBY_API_URL` and the
credentials are valid.

Tabby itself lives in the repo as a git submodule (`tabby/`) for local dev/test
and to version-lock the `recording/` + `execute/` endpoint contract; it is not
shipped inside the installed skill.

See also: [auth-modes](auth-modes.md).
