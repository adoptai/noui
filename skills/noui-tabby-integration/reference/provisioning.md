# Provisioning: how NoUI creates Apps & Profiles, and the App Template path

This is the heart of the skill. It documents **what NoUI does today** (raw App + ServiceProfile creation), **where everything is stored**, and the **App Template path** that Tabby supports but NoUI does not yet use.

Paths: NoUI is `noui/...`; Tabby is `noui/tabby/apps/api/src/...`.

---

## 1. What NoUI provisions today (the raw path)

NoUI provisions Tabby entities through **direct admin REST calls made inline in `cli/main.py`** (via the `_tabby_http` helper, `cli/main.py:544`). The FastAPI backend does **not** call Tabby — it only does local SQLite CRUD and runs the draft generator. (A `compiler/login/tabby_client.py` module exists but is **dead code** — not imported anywhere; ignore it.)

NoUI provisions as a **Tabby Admin** (human JWT): `TABBY_ADMIN_TOKEN` if set, else `POST /login` with `ADMIN_BOOTSTRAP_EMAIL`/`ADMIN_BOOTSTRAP_PASSWORD` from `tabby/.env.local` (`cli/main.py:639`). Admin is required because `POST /admin/profiles` and the promote endpoints are `@Roles('Admin')`. (The agent client and platform-JWT are *runtime* auth, not provisioning.)

There are two entry points, both creating the **same two-step App + ServiceProfile pair**:

### `noui login register <bundle>` (`cmd_login_register`, `cli/main.py:1013`)

1. Load the bundle produced by the draft generator (`compiler/login/tabby_draft_generator.py:generate()`, which builds `application_draft` + `service_profile_draft` dicts — **never a template**).
2. Patch the `application_draft` (force a `dom_check` keepalive on `body`; rewrite `http://localhost`→`https://localhost`) and `POST /apps` → read `app_id` (`cli/main.py:1067`).
3. `POST /admin/profiles` with `{**service_profile_draft, app_id, version: "YY.M.D"}` → read `id` as `profile_db_id` (`cli/main.py:1083`).
4. The profile is left in **`STAGING`** (not promoted). Credentials are **not** set.
5. Write `_provisioned {app_id, profile_db_id, profile_id, version_state: STAGING}` into the bundle JSON, and upsert `tabby/.tabby-noui-client.json` (`apps[profile_id] = {app_id, profile_db_id, credential_ref}`, append `profile_id` to `default_profiles`).

Then `noui login credentials <bundle>` prompts username/password, stores the username + `credential_ref` in the cache, and writes `<PREFIX>_PASSWORD` to `tabby/.env.local`. `noui login validate <bundle>` polls for a **HEALTHY session** (not profile state). `noui tabby session ensure --profile <slug>` spawns the live worker.

> **The STAGING trap.** The documented login happy path (`register → credentials → validate → session ensure`) leaves the profile in `STAGING` unless you promote it. The runtime resolver only matches `ACTIVE`/`CANARY` (`credentials.service.ts:224,268`), so a `STAGING`-only profile `404`s `No active profile` at the first tool call. **Fix (the gap-closure plan B1, now implemented):** promote with `noui login register <bundle> --promote`, the standalone `noui login promote <bundle>`, or `noui login import <session_id> --promote` — all run the same `STAGING → CANARY → ACTIVE` walk as `noui tabby setup`. See [troubleshooting.md](troubleshooting.md) and the gap-closure plan.

### `noui tabby setup` (local, `cmd_tabby_setup`, `cli/main.py:4167`)

The heavier flow that produces a runnable profile:

1. Admin login → decode JWT for `tenant_id`.
2. Provision/rotate a **`noui` agent client** (`POST /admin/agent-clients` with `allowed_profiles`, or `rotate-secret`), write `TABBY_CLIENT_ID`/`TABBY_CLIENT_SECRET`/`TABBY_API_URL` to `noui/.env` (`cli/main.py:4297,4340`). *This is runtime auth, not provisioning.*
3. For each profile, `_ensure_service_profile` (`cli/main.py:3753`): `POST /apps` (`:3782`) → `POST /admin/profiles` (`:3825`) → **two `POST /admin/profiles/{id}/promote` calls** (`:3838`, `:3852`) walking `STAGING → CANARY → ACTIVE`, with a **direct Postgres `UPDATE`** between them to satisfy the canary gate (`_bypass_canary_gate`, `cli/main.py:3599`, sets `canary_request_count = 5` via `docker compose exec psql`).

### Endpoints NoUI calls to provision

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `POST` | `/login` | Get an Admin JWT (when `TABBY_ADMIN_TOKEN` unset) | email+password |
| `POST` | `/apps` | Create the Application (from `application_draft`) | Admin (or Operator) |
| `POST` | `/admin/profiles` | Create the ServiceProfile in `STAGING` (returns `profile_db_id`) | **Admin** |
| `POST` | `/admin/profiles/{id}/promote` | `STAGING→CANARY`, then `CANARY→ACTIVE` (`tabby setup` only) | **Admin** |
| `POST` | `/admin/agent-clients` | Register the `noui` agent client (runtime auth) | Admin |

**`execute_enabled` (the gap-closure plan A4):** NoUI now sets `execute_enabled: true` on every app payload (`application_draft`, `tabby setup`'s `_build_app_payload`, and the template emitter), and `noui tabby session ensure` warns if a resolved app row still has it `false`. Tabby's app `execute_enabled` defaults to `false`, so a pre-existing app (or one created by another tool) may still need re-provisioning. Locally, execute also works via the spawned worker's `EXECUTE_ENABLED=true` from `tabby/.env.local` + `LOCAL_WORKER_URL`, which bypass the app row. **Closed (Tabby-side, PR adoptai/tabby#90, merged to `dev`):** the App Template entity now has an `execute_enabled` column, the create DTO accepts it, it's in `PROPAGATED_FIELDS`, and `autoProvisionFromTemplate` copies `template.execute_enabled` onto each cloned per-user app — so auto-provisioned cloud apps inherit execute access. Verify the Tabby you target includes #90; templates created before it may still be `false`.

### Where identifiers are stored (file-based, no central DB)

- Bundle JSON `_provisioned`: `app_id`, `profile_db_id`, `profile_id`, `version_state`.
- `tabby/.tabby-noui-client.json` (the creds cache): `{client_id, client_secret, default_profiles, apps: {profile_id: {app_id, profile_db_id, credential_ref, username, login_url, login_config}}}`.
- `noui/.env`: `TABBY_API_URL`, `TABBY_CLIENT_ID`, `TABBY_CLIENT_SECRET`, (cloud) `ADOPT_*`, `NOUI_TABBY_AUTH_MODE`.
- `tabby/.env.local`: `ADMIN_BOOTSTRAP_EMAIL/PASSWORD`, `<PREFIX>_PASSWORD` per profile.

---

## 2. Local vs cloud setup

| | `noui tabby setup` (local) | `noui tabby setup --cloud` |
|---|---|---|
| Identity | Admin bootstrap user (human JWT) | Developer's **platform PAT** (`ADOPT_CLIENT_ID`/`ADOPT_CLIENT_SECRET` from `app.adopt.ai/dashboard#/admin-box/`) |
| What it does | Provisions `noui` agent client + ServiceProfiles (→ ACTIVE); writes `TABBY_*` | Verifies the PAT → `/v1/users/api-token` → `/auth/token-exchange` round-trip; writes `ADOPT_*`, `TABBY_API_URL`, `NOUI_TABBY_AUTH_MODE=platform_jwt`. With `--template-bundle <bundle.json>` it **also provisions a tenant-wide App Template** (the gap-closure plan A3) |
| Provisions a connection? | **Yes** (App + Profile, promoted ACTIVE) | With `--template-bundle`: **yes — an App Template** (the per-user auto-provisioning blueprint). Without it: no (auth-verification only) |
| Runtime token | `agent_token` (works with default `tabby` runtime) | `platform_jwt` — **now honored by the default `tabby` runtime** (the gap-closure plan A2); see [execute-and-runtime.md](execute-and-runtime.md) |
| Prereq | Local Tabby + admin token | The org's **tenant must already exist** in cloud Tabby, else token-exchange `401 Tenant not found` |

> **Both cloud dead-ends are closed (the gap-closure plan A1–A4).** `tabby setup --cloud --template-bundle` now provisions an App Template, *and* the default `tabby` runtime now consumes the `platform_jwt` it configured. The tenant-wide, per-user model is wired NoUI-side; the remaining end-to-end "hop 3" proof (a federated request that actually triggers `autoProvisionFromTemplate`) still needs a live cloud Tabby + the A4 Tabby-side `execute_enabled` change. The prior "one developer / service principal per tenant" framing in `plans/noui/noui-cloud-tabby-auth-plan.md` §F predates this work.

---

## 3. The App Template path (Tabby-built, NoUI now emits)

This is the mechanism that makes a connection reusable tenant-wide. Tabby has it fully built, and **NoUI now emits templates** (the gap-closure plan A1): `noui tabby template create <bundle.json>`, `noui login register --as-template`, and `noui tabby setup --cloud --template-bundle <bundle.json>`. All three derive the payload from the same drafts the login flow builds (see "How NoUI emits a template" below).

### How auto-provisioning works (Tabby side)

There is **no explicit "instantiate" endpoint**. Instantiation is **implicit and lazy**, driven from the credentials path:

1. A **federated** user (platform JWT, `owner_user_id` set) calls `POST /credentials/request {profile_id}`.
2. `resolveActiveProfile` finds no profile owned by them and no NULL-owner shared profile (`credentials.service.ts:228-240`).
3. It calls `autoProvisionFromTemplate(tenantId, profileId, ownerUserId)` (`credentials.service.ts:280`):
   - Look up the template by `{tenant_id, profile_name_pattern: profileId}` (`:286`). No match → `404`.
   - `appsService.create(...)` copying the template's config blocks, then `appRepo.update(app_id, {owner_user_id, template_id})` (`:314`).
   - `profilesService.create(...)` (STAGING), then `profileRepo.update(id, {owner_user_id, version_state: ACTIVE})` — **skips canary** (`:328`).
   - `sessionsService.scale(app_id, 1, ...)` → the controller spins a real worker pod, inheriting `owner_user_id` onto the session.
4. Each tenant user who requests that `profile_id` gets their **own private** App+Profile+Session cloned from the one shared template.

### Template API (Tabby side, `@Controller('admin/app-templates')`)

| Method | Path | Auth | Note |
|---|---|---|---|
| `POST` | `/admin/app-templates` | **Any authenticated user** (own tenant); Admin may override `tenant_id` | No `@Roles` guard (see security gap) |
| `GET` | `/admin/app-templates[?tenant_id=]` | Any authenticated user | |
| `GET` | `/admin/app-templates/:id` | Any authenticated user | |
| `PUT` | `/admin/app-templates/:id` | **Admin** | Triggers propagation to linked apps |
| `DELETE` | `/admin/app-templates/:id` | **Admin** | Linked apps' `template_id` set NULL |

### Template lineage and propagation

`applications.template_id` is the lineage FK (migration 020). On `PUT`, `propagateToLinkedApps` (`app-templates.service.ts:89`) copies `PROPAGATED_FIELDS = [browser_policy, login_config, keepalive_config, export_policy, notification_config]` onto all linked **applications** (chunked by 50). It does **not** touch the per-user `service_profiles` cloned at provision time, and `name`/`profile_name_pattern`/`credential_ref_default`/`idle_shutdown_seconds` are not propagated. So a template edit does not reach already-provisioned users' profiles (a real gap — see the Tabby hardening plan, C9).

### How NoUI emits a template

`build_app_template_payload(application_draft, service_profile_draft)` (`compiler/login/tabby_draft_generator.py`) derives the `/admin/app-templates` payload from the same drafts the login flow builds. The CLI surfaces it three ways: `noui tabby template create <bundle.json> [--upsert]`, `noui login register --as-template`, and `noui tabby setup --cloud --template-bundle <bundle.json>`. The builder enforces the four correctness rules that were the original design intent (`plans/noui/noui-cloud-tabby-auth-plan.md` "Caveat 3"):

1. **Emits a template, not raw entities** — `POST /admin/app-templates` with `{name, profile_name_pattern, login_config, keepalive_config, export_policy, browser_policy, notification_config, execute_enabled}`.
2. **`profile_name_pattern` == the runtime `profile_slug`** (`service_profile_draft.profile_id` / `PROFILE_SLUG`), or `autoProvisionFromTemplate` would never match. Enforced + unit-tested.
3. **Sets `execute_enabled: true`** (the gap-closure plan A4) — auto-provisioned apps default it to `false`, breaking `/execute/fetch` in real K8s. ✅ The Tabby-side change landed (PR adoptai/tabby#90, merged to `dev`): the template entity has the `execute_enabled` column, the create DTO accepts it (no longer dropped by the whitelist), it's in `PROPAGATED_FIELDS`, and `autoProvisionFromTemplate` copies it onto each cloned app. Confirm your target Tabby includes #90.
4. **`credential_types` are object-shaped (`[{name, volatility}]`) and folded into `export_policy`** — `autoProvisionFromTemplate` clones the per-user profile's `credential_types`/`target_domains` from `export_policy` (`credentials.service.ts`), not from a profile draft, so the builder folds them in. The object shape itself comes from `_cookie_credential_types` (the gap-closure plan D1).
5. **Uses the `platform_jwt` runtime** so the token carries `owner_user_id` and triggers auto-provisioning — now supported in the default `tabby` execute adapter (the gap-closure plan A2), not just legacy `http` mode.

### Migrating an existing profile to a template

If you still have the **login bundle**, `noui tabby template create <bundle.json>` emits a matching template directly (`profile_name_pattern = slug`). What's **not** automated is adopting a profile that exists only as a live Tabby row (no bundle on disk) — there's no `noui tabby template adopt --profile <slug>` that reads the existing app/profile config back out of Tabby and POSTs a template from it. Also note: a NULL-owner profile created directly does not auto-clone for other users (it is served as a single shared row, not reproduced per user), and the `COALESCE(owner_user_id,'')` unique index (migration 015) means a NULL-owner and an owner-stamped ACTIVE row cannot coexist for the same `profile_id` — so migrating a shared profile to the per-user template path requires de-duplication.

---

## 4. Profile lifecycle reference

```
STAGING ──promote──▶ CANARY ──promote(gated)──▶ ACTIVE ──(new ACTIVE retires old)──▶ RETIRED
   ▲                    │                                                              │
   └──── rollback ──────┘                                  rollback ◀──reactivate parent┘
```

- `STAGING → CANARY`: unconditional.
- `CANARY → ACTIVE`: **gated** — requires `canary_request_count ≥ 5` and error rate ≤ 20% (`packages/shared/src/constants.ts`), and retires the existing ACTIVE for that `(tenant, profile_id)` in a transaction. Canary counters are incremented by the **credentials request path** (`credentials.service.ts:175`), not the (unused) `recordCanaryResult`.
- **Auto-provisioned profiles skip the gate entirely** — created then immediately set `ACTIVE` (`credentials.service.ts:328`).
- `noui tabby setup` bypasses the gate locally by writing `canary_request_count = 5` directly in Postgres.

→ Next: **[execute-and-runtime.md](execute-and-runtime.md)** for how a provisioned profile is consumed at runtime.
