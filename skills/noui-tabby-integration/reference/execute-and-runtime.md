# Execute & runtime: how a provisioned profile is consumed

NoUI's authenticated tools don't replay extracted cookies — they ask Tabby to run the request **inside the live browser session**. This doc covers the two execute endpoints, the resolution chain, the live-session requirement, and how the generated runtime authenticates.

Paths: NoUI runtime is `noui/compiler/runtime/...` and the generated `noui_runtime/...`; Tabby is `noui/tabby/apps/api/src/...` and `noui/tabby/apps/worker/src/...`.

---

## 1. The two execute endpoints

Both are `@HttpCode(200)`, guarded by `JwtAuthGuard + RolesGuard`, and require role `Admin`/`Operator`/`Agent` (`execute.controller.ts`). Neither streams — the API does a single `fetch()` to the worker and returns one JSON object.

### `POST /execute/fetch` — the default runtime data path
Runs `fetch(url, {credentials:'include', headers})` via `page.evaluate()` **inside the authenticated Chromium page** (`apps/worker/src/execute-handler.ts:60`), so it inherits the browser's real cookies and TLS/JA3 fingerprint. This is the documented bypass for Akamai/Cloudflare/PerimeterX 429s — there is **no** CloakBrowser or TLS-spoofing library; the anti-bot property is purely structural.

- Request: `{profile_id, url, method?, headers?, body?, timeout_ms?}` (`profile_id` is the **slug**).
- Response: `{status, headers, body}` (body is text, truncated to 5 MB).
- Limits: body ≤ 1 MB, ≤ 50 headers, schemes http/https only, `timeout_ms` ≤ 60 000; rate **60/min per profile** (fail-open if Redis errors). No session lock (concurrent fetches allowed).

### `POST /execute/browser` — Playwright commands
Dispatches one of 15 commands (`navigate`, `click_element`, `click_by_text`, `type_text`, `get_page_summary`, `screenshot`, `wait_for_selector`, `scroll_page`, `har_start`, `har_stop`, `har_status`, …).

- Request: `{profile_id, command, params?, timeout_ms?}` → `{success, data?, error?}`.
- **Acquires a per-session Redis lock** `execute_browser_lock:{sessionId}` (TTL 65s) → returns **409** "driven by another consumer" on contention. Rate 120/min.
- **Who uses it:** the NoUI autopilot capture driver (`har_start`/`har_stop` via `backend/elicitation/browser_bridge.py`). Compiled MCP/Skill tools do **not** emit `/execute/browser` today (only `/execute/fetch`), though an `execute_browser()` helper exists and the Expedia demo hand-wires it.

> **Stale doc flag:** `/noui-generalize` still calls `/execute/browser` a "future/unavailable" endpoint and points at a `localhost:9222` CDP-WebSocket workaround. **Both are obsolete.** Use `execute_browser(profile_id, command, params)`. The client-side CDP-WebSocket adapter has been removed entirely.

---

## 2. Resolution chain (identical for both routes)

The caller passes a **`profile_id` slug** — never an app_id or session_id. The server resolves the session itself (`execute.service.ts:69`):

```
profile_id (slug)
  → resolveActiveProfile(tenant, profile_id, owner_user_id)   # ACTIVE/CANARY row; owner-scoped if federated
  → findHealthySession(tenant, profile.app_id, owner_user_id) # one HEALTHY session for the app
  → session.pod_name
  → buildWorkerUrl(pod_name, '/execute/fetch')                # K8s: {pod_name}-worker.<ns>.svc:8091; dev: LOCAL_WORKER_URL
  → forward, signed with a 2-min HS256 service token {tenant_id, profile_id, token_type:'service'}
  → worker runs it in the live page
```

Error map (surface these in tooling):

| Status | Cause | Where |
|---|---|---|
| `404` | No ACTIVE/CANARY profile for the slug (or wrong tenant) | `credentials.service.ts:268` |
| `404` | No HEALTHY session for the app | `credentials.service.ts:363` |
| `409` | Session has no `pod_name` (worker not assigned) | `execute.service.ts:77` |
| `409` | `/execute/browser` session lock held by another consumer | `execute.service.ts:174` |
| `429` | Rate limit (fetch 60/min, browser 120/min) | `execute.service.ts` |
| `502` | Worker unreachable / fetch failed | `execute.service.ts` |
| `504` | Worker timeout (`timeout_ms` + 5 s) | `execute.service.ts` |

> **Execute does NOT auto-provision or rescale.** Unlike `/credentials/request` (which catches "no healthy session" and re-scales an idle app to 1, and triggers `autoProvisionFromTemplate`), the execute paths call `resolveActiveProfile`/`findHealthySession` with **no recovery** (`execute.service.ts:69`). A cold profile, an idle-shutdown app, or a never-provisioned template-backed profile will simply `404` on execute. `/execute/fetch` also does **not** update `last_credential_request_at`, so an execute-only tool never refreshes the idle timer. *(NoUI-side mitigation, opt-in: set `NOUI_EXECUTE_WARMUP=1` and the generated runtime issues one `POST /credentials/request` — which rescales/auto-provisions — then retries execute once before surfacing the error; the gap-closure plan B5. The Tabby-side rescale-in-execute remains a documented follow-up.)*
>
> **Template vs direct makes no difference at execute time.** `resolveActiveProfile` never reads `template_id`; once a profile is ACTIVE, its provenance is invisible to execute.

---

## 3. The live-session-worker requirement

A profile being `ACTIVE` (and even a session row saying `HEALTHY`) is **not** sufficient. `/execute/*` needs an actual running worker process holding an authenticated page, addressed by `session.pod_name`.

`noui tabby session ensure --profile <slug>` (`cli/main.py:4409`) is what makes one exist locally:

- It treats a **DB-`HEALTHY` session whose local worker PID is dead (or CDP unreachable) as STALE**, marks it TERMINATED, and restarts (`cli/main.py:4470`). This proves DB state alone is insufficient.
- To start one it seeds a session row, spawns the worker as a subprocess (`pnpm --filter @browser-hitl/worker start`, env `SESSION_ID/APP_ID/TENANT_ID/STREAMING_MODE=cdp` + a credentials mount, `cli/main.py:4543`), and polls `GET /sessions/{id}` until `state==HEALTHY`.

Two reliability traps here (see the gap-closure plan):

- **`session ensure` validates the wrong surface.** Its readiness check is DB state + local PID + CDP reachable on **:9222** — but `/execute/fetch` is served on **:8091** and only mounts when `EXECUTE_ENABLED=true`. A session can be "✓ HEALTHY" yet have no execute routes.
- **`HEALTHY` ≠ authenticated/extracted.** HEALTHY means the keepalive passed (possibly a `url_check` that passes pre-login on an SPA). On the credentials path a missing bundle yields **empty** cookies silently; on the execute path `fetch(credentials:'include')` may run unauthenticated and return the target's 401/403 wrapped as a 200 body.

---

## 4. `execute_enabled` — the K8s landmine

In K8s, the per-session worker Service (`{pod_name}-worker`) and the pod's `EXECUTE_ENABLED` env are both gated on `application.execute_enabled` (`apps/controller/src/reconcile.service.ts:224`, `pod-manager.service.ts:365`), which defaults to **`false`**. NoUI now sets `execute_enabled: true` on every app payload (`application_draft`, `_build_app_payload`, and the template emitter), and `session ensure` warns on a false app row (the gap-closure plan A4). **Cloud-side now fixed (PR adoptai/tabby#90, merged to `dev`):** the App Template entity has an `execute_enabled` column, the create DTO accepts it, it's in `PROPAGATED_FIELDS`, and `autoProvisionFromTemplate` copies `template.execute_enabled` onto each cloned per-user app — so auto-provisioned apps get a worker Service and `/execute/fetch` works. (A template created before #90 may still be `false`.) Locally the spawned worker reads `EXECUTE_ENABLED=true` from `tabby/.env.local` and the API uses `LOCAL_WORKER_URL=http://localhost:8091`, which bypass the app row entirely.

---

## 5. Runtime auth — execution mode vs auth mode (two orthogonal axes)

The compiler emits one of two runtimes based on `--execution-mode` (default `tabby`). **Auth mode is independent of execution mode:** both `tabby` (`execute.py`) and `http` (`auth.py`) now support `agent_token` *and* `platform_jwt` (the gap-closure plan A2). Execution mode chooses *how* the request runs (inside the browser via `/execute/fetch`, or in-process httpx with extracted credentials); auth mode chooses *which token* authenticates to Tabby.

> **Note on "CDP".** This mode was **formerly named `cdp`** and is now `tabby` — the old name implied a client-side CDP connection that no longer exists. Two things keep the "CDP" label and are unrelated to the execution mode:
> - **The old client-side CDP-WebSocket fetch** (`cdp_adapter.py` → `noui_runtime/cdp.py`, `Runtime.evaluate` over `localhost:9222`) was **removed** — replaced by `execute_adapter.py` → `/execute/fetch` (plain HTTP; commits `ed0d393`, `e924000`).
> - **CDP inside the Tabby worker** is still live: the session runs with `STREAMING_MODE=cdp`, exposes CDP on `:9222` (what `session ensure` probes for liveness/VNC), and runs the fetch via Playwright `page.evaluate()` (CDP under the hood) — all server-side, invisible to the NoUI client.

### Default: `tabby` mode → `noui_runtime/execute.py` (from `compiler/runtime/execute_adapter.py`)
- Each generated op calls `execute_fetch(PROFILE_SLUG, url, method=…, headers=…, body=…)`.
- `PROFILE_SLUG` and `BASE_URL` are **baked into the op at compile time** from `auth_plan.profile_slug` (`server_generator.py:363`, `operation_generator.py`). Not read from env or re-read from `auth_plan.json` at runtime.
- Auth: supports **both** modes (as of the gap-closure plan A2). `_resolve_auth_mode()` honors explicit `NOUI_TABBY_AUTH_MODE`, else auto-detects `platform_jwt` when `ADOPT_*` are set, else `agent_token` (the default). `agent_token` → `POST /auth/agent-token` with `TABBY_CLIENT_ID`/`TABBY_CLIENT_SECRET`; `platform_jwt` → the two-step `POST /v1/users/api-token` → `POST /auth/token-exchange`, yielding a federated Tabby JWT that carries `owner_user_id`. Bearer tokens are cached per mode (`_get_tabby_bearer`), not re-fetched every call.
- Surfaces both `409` and a `404` carrying a no-session marker (`"no healthy session"` / `"no active profile"`) as the actionable "run `tabby session ensure --profile <slug>`" error (`execute_adapter.py`, the gap-closure plan B2). A bare `404`, or an upstream `404` wrapped in a `200` body, is left untouched.

### Legacy: `http` mode → `noui_runtime/auth.py` (from `compiler/runtime/auth_adapter.py`)
- In-process `httpx`; `resolve_auth()` → `POST /credentials/request` for headers/cookies, merged into the outgoing request.
- Supports **both** auth modes: `_resolve_auth_mode()` (`auth_adapter.py:100`) honors explicit `NOUI_TABBY_AUTH_MODE`, else auto-detects `platform_jwt` when `ADOPT_*` are set, else `agent_token`. The `platform_jwt` two-step (`/v1/users/api-token` → `/auth/token-exchange`) is here, with per-mode bearer caching.

### The auth-mode × execution-mode matrix

| Export mode | Runtime module | Token | Carries `owner_user_id`? | Can trigger template auto-provision? |
|---|---|---|---|---|
| `tabby` (default) + `agent_token` | `execute.py` | agent_token (requires `TABBY_CLIENT_ID/SECRET`) | **No** | **No** |
| `tabby` (default) + `platform_jwt` | `execute.py` | platform JWT (federated) | **Yes** | **Yes** (per-user) |
| `http` (legacy) + `agent_token` | `auth.py` | agent_token | No | No |
| `http` (legacy) + `platform_jwt` | `auth.py` | platform JWT (federated) | **Yes** | **Yes** (per-user) |

> **Cloud + default `tabby` now works (the gap-closure plan A2/A3).** The default `tabby` runtime honors `NOUI_TABBY_AUTH_MODE=platform_jwt` (the value `tabby setup --cloud` writes): it runs the platform→Tabby token-exchange and uses the federated, `owner_user_id`-carrying JWT, so cloud users no longer need `--execution-mode http`. `agent_token` remains the default for local/self-host. Pair this with a tenant-wide App Template (`tabby setup --cloud --template-bundle <bundle.json>`, or `noui tabby template create`, or `noui login register --as-template`) so the first federated request for a slug auto-provisions a per-user profile. The remaining end-to-end "hop 3" proof needs a live cloud Tabby — see the gap-closure plan.

### Two profile-selection mechanisms (don't conflate)
- **Compiled tools** bake `PROFILE_SLUG` at export time — changing the profile means re-exporting (or hand-editing the op + `auth_plan.json`), **not** an env var.
- **Autopilot / recording-time** browser driving keys on the `TABBY_PROFILE_ID` env var (`browser_bridge.py` `_use_tabby_driver` needs `TABBY_API_URL` + `TABBY_CLIENT_ID` + `TABBY_PROFILE_ID`). `TABBY_PROFILE_ID` has **no effect** on generated tools.

---

## 6. Ports

| Thing | Value | Source |
|---|---|---|
| NoUI backend | `:8002` (`NOUI_PORT`) | `cli/main.py:95` |
| Tabby API (current) | `:8000` | `tabby/CLAUDE.md:47` ("ALL services on 8000; 8080/3000 are legacy") |
| CLI/`.env` default `TABBY_API_URL` | `http://localhost:8080` ⚠️ legacy | `cli/env.py`, `.env.example` |
| Generated runtime default | `http://localhost:8000` | `execute_adapter.py:90` |
| Worker execute HTTP | `:8091` | `LOCAL_WORKER_URL` / K8s Service |
| Worker CDP/streaming | `:9222` | what `session ensure` probes |

**Set `TABBY_API_URL` explicitly.** The CLI default (`8080`) and the generated-runtime default (`8000`) disagree, and Tabby actually runs on `8000` — relying on defaults causes a CLI-hits-8080 / runtime-hits-8000 split-brain.

→ Next: **[troubleshooting.md](troubleshooting.md)** for symptom→fix. *(The end-to-end gap analysis + status now live in the plans — see SKILL.md → Related.)*
