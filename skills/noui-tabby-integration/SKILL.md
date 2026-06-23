---
name: noui-tabby-integration
description: Use this skill to understand how NoUI integrates with Tabby — how Apps, ServiceProfiles, and App Templates are created and scoped, why a profile ends up "creator-only" vs tenant-wide, how owner_user_id and the agent-token vs platform-JWT auth modes drive sharing, and how the /execute/fetch and /execute/browser runtime depends on a live session. Triggers on "how does NoUI use Tabby", "create an App/Profile/Template on Tabby", "Tabby App Templates", "auto-provisioning", "profile is creator-only", "make a connection scalable / tenant-wide", "owner_user_id", "share a Tabby connection across the tenant", "tabby provisioning", "execute/fetch", "execute/browser", "tabby integration gaps", "why does my profile 404", "STAGING vs ACTIVE", "agent_token vs platform_jwt", or "cloud Tabby provisioning". Reference skill — read the linked docs in reference/ on demand.
---

# NoUI ↔ Tabby Integration

NoUI's authenticated workflows do not run by extracting cookies and replaying them. They run **inside a live Tabby browser session**: the generated tool calls Tabby's `POST /execute/fetch` (or `/execute/browser`), and Tabby executes the request from a worker that holds a real, logged-in Chromium page. This means NoUI has a hard runtime dependency on Tabby for anything authenticated — provisioning the connection, keeping a session alive, and routing execute calls to the right worker.

This skill documents that integration surface, with a specific focus on **how connections (Apps + ServiceProfiles) are created and scoped**, the **App Templates** mechanism that is supposed to make a connection reusable across a tenant, and the **end-to-end gaps** in that path today.

> **Ground truth + version caveat.** Everything here is grounded in the pinned Tabby submodule (`noui/tabby`, branch `tabby-noui`) and the NoUI Python code, cited as `path:line`. Several Tabby plan/spec docs are **stale** relative to the code — where they disagree, this skill follows the code and flags the mismatch. The **deployed cloud Tabby may differ** from the pinned submodule; treat cloud-specific claims as "confirm against the running deployment."

---

## Read this when

- You need to create or document how Apps / Profiles / Templates get made on Tabby for a NoUI workflow.
- A teammate asks "why can only I use this profile?" or "how do we make this connection work for everyone in the tenant?"
- A generated tool fails at runtime with `404 No active profile`, `404 No healthy session`, `409`, or `502`, and you need to know whether the cause is provisioning, session lifecycle, scoping, or auth mode.
- You are scoping work to close the cloud / multi-user provisioning gap (the "emit App Templates" follow-on).

---

## The model in 90 seconds

Four Tabby entities, all tenant-scoped:

| Entity | What it is | Key fields |
|---|---|---|
| **Application** | The running unit — reconciles live worker sessions to `desired_session_count`. | `login_config` (carries `credential_ref`), `keepalive_config`, `export_policy`, `target_urls`, `owner_user_id?`, `template_id?`, `execute_enabled` |
| **ServiceProfile** | A *versioned* definition of what to extract + how to validate. Bound to an app via `app_id`. | `profile_id` (semantic slug), `version_state` ∈ `STAGING→CANARY→ACTIVE→RETIRED`, `credential_types` (shape, **no secrets**), `owner_user_id?` |
| **Session** | A live worker pod running an authenticated Playwright page. Routes execute via `pod_name`. | `state` ∈ `STARTING/HEALTHY/UNHEALTHY/LOGIN_NEEDED/.../TERMINATED`, `owner_user_id?` (inherited from app) |
| **App Template** | A tenant-scoped **blueprint** that auto-clones a per-user App+Profile+Session on demand. | `profile_name_pattern` (the join key matched against `profile_id`), the same config blocks as an App |

Two separate "credential" concepts (don't conflate them):

- **Login credentials (input)** — the username/password to log into the site. Referenced by `login_config.credential_ref`, only two forms: `k8s:secret/{name}` or `manual:`. Resolved by the **worker** from a mounted K8s secret. Never in the DB.
- **Extracted credentials (output)** — cookies/headers/CSRF harvested from the live session, AES-256-GCM encrypted in tenant-scoped MinIO, returned by `POST /credentials/request`. `credential_types` declares only the *shape*.

**The one field that controls sharing: `owner_user_id`.**

- `owner_user_id = NULL` → **tenant-shared**: any user in the tenant resolves this profile/session.
- `owner_user_id = <user>` → **per-user / "creator-only"**: strict match; never resolves for another user.

And the one field that controls *who gets which kind of token*: the **auth mode**.

- **Agent token** (`client_id/secret` → `/auth/agent-token`) carries **no** `owner_user_id` → **tenant-wide** reach (within `allowed_profiles`).
- **Platform JWT** (PAT → `/v1/users/api-token` → `/auth/token-exchange`) carries `owner_user_id` → **per-user** scoping, and is the only token that can trigger **template auto-provisioning**.

→ Full detail in **[reference/concepts.md](reference/concepts.md)**.

---

## Reconciling the premise: "creator-only" vs "tenant-wide" templates

The common framing is: *"creating Apps/Profiles directly makes the profile usable only by its creator; App Templates generalize it so all users share the same connection."* The **conclusion is correct** — direct creation does not scale to all tenant users, and App Templates are the mechanism that does. But the **mechanism** is more precise than "share the same connection," and getting it exactly right is load-bearing for every downstream decision:

1. **A directly-created App/Profile is `owner_user_id = NULL` — i.e. tenant-*shared* at the data-model level**, not creator-locked. `apps.service.ts:90` and `profiles.service.ts:56` set only `tenant_id`; any user in the tenant resolves a NULL-owner profile via the `IsNull()` fallback (`credentials.service.ts:236`). The "only the creator can use it" effect today is **operational**, not a row flag: NoUI's local session is a **worker process on the creator's machine** (`noui tabby session ensure` spawns it via `pnpm`, `cli/main.py:4543`), and the default runtime uses an **agent token with no `owner_user_id`**, so it resolves exactly that one shared/local session — there is no per-user reproduction and no real cloud worker pod.

2. **App Templates make the *recipe* tenant-wide, and auto-clone a *private per-user* connection from it** — they do **not** make users share one connection. On a federated user's first `POST /credentials/request` for a profile with no existing profile, `autoProvisionFromTemplate` (`credentials.service.ts:280`) creates a **new** App+Profile+Session **stamped with that user's `owner_user_id`** (`credentials.service.ts:314,328`), promotes it straight to ACTIVE, and scales a session. Each tenant user gets their **own** isolated session (their own login, their own cookies) seeded from the shared blueprint. That is strictly better than one shared login: per-user attribution, no shared-account lockout, no everyone-acts-as-one-identity.

3. **So the scalable model is: one tenant template → each user lazily self-provisions a private connection.** This is exactly the already-decided design intent (`plans/noui/noui-cloud-tabby-auth-plan.md`, "Caveat 3"): *NoUI's cloud-provisioning value is to emit/update App Templates, not raw apps/profiles.* **That intent is planned-but-not-built** — NoUI emits zero templates today (verified: grep across `cli/ backend/ runtime/ compiler/` returns nothing for app-templates).

→ The precise scoping rules, with the `resolveActiveProfile` precedence, are in **[reference/concepts.md](reference/concepts.md)**. What NoUI actually does today vs the template path is in **[reference/provisioning.md](reference/provisioning.md)**.

---

## Reference docs (read on demand)

| Doc | Covers |
|---|---|
| **[reference/concepts.md](reference/concepts.md)** | The data model (App/Profile/Session/Template), the two credential systems, `owner_user_id` scoping and `resolveActiveProfile` precedence, auth modes & multi-tenancy, the shared-vs-per-user model. |
| **[reference/provisioning.md](reference/provisioning.md)** | How NoUI provisions (raw `POST /apps` + `POST /admin/profiles`, `login register` vs `tabby setup`), what is stored where, STAGING→ACTIVE, local vs cloud setup, and the **App Template path** — now emitted by NoUI via `tabby template create` / `login register --as-template` / `tabby setup --cloud --template-bundle`. |
| **[reference/execute-and-runtime.md](reference/execute-and-runtime.md)** | `POST /execute/fetch` + `/execute/browser` contracts, the profile→session→pod resolution chain, the live-session-worker requirement, `execute_enabled`, runtime auth (default `tabby`/agent-token vs legacy `http`/platform-JWT), and the auth-mode × execution-mode matrix. |
| **[reference/troubleshooting.md](reference/troubleshooting.md)** | Symptom → cause → fix for the common runtime/provisioning failures (STAGING 404, no-session 404, 409, 502 execute_enabled, empty credentials, port split-brain, cloud-mode auth). |

---

## Local bring-up & port map

There are two local Tabby topologies, and conflating them is the #1 source of "why can't I get a session" friction:

| Topology | How | Gives you | API at |
|---|---|---|---|
| **Compose API-only** | `noui tabby start` | Docker-compose infra (postgres/redis/nats/minio) + the **API process only** — **no controller, no worker** | `http://localhost:8080` |
| **Kind full stack** | (in `tabby/`) `make kind-create` → `make kind-reload-all` → `kubectl port-forward -n browser-hitl svc/browser-hitl-api 18080:8000` | Controller + per-session **worker pods** (real Playwright browsers, VNC/CDP) — the only local way to get a **live browser session** | `http://localhost:18080` |
| **Cloud / staging** | point env at the hosted Tabby | Full managed stack | e.g. `https://tabby-api.adoptai.dev` |

**Key consequence:** `noui tabby start` (compose) **cannot produce a live browser session on its own** — no controller/worker. Anything that needs `/execute/*`, `session ensure`, VNC recording, or autopilot Tabby mode needs the **Kind full stack** or **cloud**. (Locally, the spawned worker + `LOCAL_WORKER_URL=http://localhost:8091` can also bridge a single worker to the compose API — see [execute-and-runtime](reference/execute-and-runtime.md).)

**Port reference (so the scattered numbers stop surprising you):**

| Port | Service | Context |
|---|---|---|
| `8002` | NoUI backend (`noui start`) | always |
| `8080` | Tabby API | local **compose** (`noui tabby start`); also `tabby/.env.local` `API_PORT` |
| `18080` | Tabby API | local **Kind** (via `k8s-port-forward`) — this is what `TABBY_API_URL` should be for Kind |
| `8000` | Tabby API | in-cluster / cloud (generated runtime default) |
| `8090` | Controller (health) | Kind/cloud |
| `8091` | Worker `/execute/*` + health | the execute surface; `noui status` reports it as `Execute :8091` |
| `9222` / `9223` | Worker CDP (DevTools / relay) | the streaming surface |

> The `TABBY_API_URL` in `.env` must match your topology: `:8080` for compose, `:18080` for Kind, the hosted URL for cloud. A mismatch makes `noui tabby start`'s readiness probe and the autopilot/CLI calls hit the wrong port.

**Kind gotchas (one-time, real):** the `egress-proxy` pod needs the stock `node:20.18.1-alpine` image loaded into the node (`docker pull` it, then `kind load docker-image node:20.18.1-alpine --name tabby-dev`) or it sits in `ImagePullBackOff`; and a `credential_ref: k8s:secret/<name>` app needs that k8s secret to actually exist (e.g. a no-auth app needs `kubectl create secret generic no-auth -n browser-hitl --from-literal=username=x --from-literal=password=x`) or the worker pod hangs in `ContainerCreating` on a `FailedMount`.

---

## Critical rules (don't get these wrong)

- **`owner_user_id` is the scoping switch, not "direct vs template."** NULL = tenant-shared; set = per-user. Templates *produce* per-user (set) connections; raw `POST /apps`/`/profiles` produce NULL (shared) ones.
- **The login flow leaves the profile in `STAGING`. The runtime resolves only `ACTIVE`/`CANARY`.** `login register` → `login credentials` → `login validate` → `session ensure` never promotes (`cli/main.py` validate only waits for a HEALTHY *session*). `/execute/fetch` will `404 No active profile` until something promotes it to ACTIVE — `noui tabby setup` does this; the login path does not. See [troubleshooting](reference/troubleshooting.md).
- **A profile has no "HEALTHY" state.** `HEALTHY` is a *Session* state. Profile `version_state` is `STAGING/CANARY/ACTIVE/RETIRED`. "Wait for the profile to become HEALTHY" is shorthand for "wait for a HEALTHY session for the profile's app."
- **`HEALTHY` ≠ authenticated/extracted.** A HEALTHY session only means the keepalive check passed; the login DSL may not have completed or no credential bundle may exist yet.
- **The default `tabby` runtime now supports both auth modes (the gap-closure plan A2).** It honors `NOUI_TABBY_AUTH_MODE=platform_jwt` (auto-detected from `ADOPT_*`, else explicit), so it *can* carry `owner_user_id` and trigger template auto-provisioning — no longer agent-token-only. `agent_token` stays the default for local/self-host.
- **`noui tabby setup --cloud` can now provision a template (the gap-closure plan A3).** Bare `--cloud` still only verifies the round-trip + writes env, but `--cloud --template-bundle <bundle.json>` (or `noui tabby template create` / `noui login register --as-template`) emits a tenant-wide App Template.
- **NoUI now sets `execute_enabled: true` on every app payload (the gap-closure plan A4).** `session ensure` warns if a pre-existing app row still has it `false`. The Tabby-side half is now **closed** (PR adoptai/tabby#90, merged to `dev`): the App Template entity has an `execute_enabled` column, the create DTO accepts it, it's in `PROPAGATED_FIELDS`, and `autoProvisionFromTemplate` copies `template.execute_enabled` onto each per-user app — so cloud auto-provisioned apps inherit execute access. Only caveat: a pre-existing app/template created before that change may still be `false`; verify the Tabby you point at includes #90.
- **`credential_ref` has exactly two forms:** `k8s:secret/{name}` or `manual:`. The secret name is mounted verbatim from a shared worker namespace — choose it carefully (see the security notes in the Tabby hardening plan).

---

## Related

- **/noui-record-login** — records a login and provisions the App + STAGING ServiceProfile (the raw path documented here).
- **/noui-record-workflow** — records a workflow and exports it; the generated tools call `/execute/fetch`.
- **/noui-generalize** — fixes execution/runtime issues (the credential_types bug, bot detection, promotion); several of its sections are corrected (see the gap-closure plan).
- **/noui-setup** — environment + `tabby setup` / `tabby setup --cloud`.
- **Gap analysis & status** (the former `gaps.md`, moved to the planning vault): **"the gap-closure plan"** = `plans/noui/noui-tabby-integration-gap-closure-plan.md` (NoUI-side items A1–A4, B1–B6, D1–D8 + remaining work); **"the Tabby hardening plan"** = `plans/tabby/tabby-noui-integration-hardening-plan.md` (security Topic C + the Tabby halves of A4/B3/B5/B7 + D8). Prose references to those two names point here.
- Workspace plans: `plans/noui/noui-cloud-tabby-auth-plan.md` (the "emit App Templates" intent), `plans/noui/noui-agent-friction-retro-plan.md` (the 17-item friction backlog), `plans/tabby/tabby-noui-compatibility-contract.md` (the locked HTTP surface — predates platform-JWT and `/execute/*`), `plans/tabby/tabby-execute-endpoint-plan.md` (now shipped, doc still `status: proposed`).
