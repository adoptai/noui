---
name: noui
description: "Author a new Tabby App Template + Skill from a website by recording the login and the workflow through the signed-in user's CLOUD Tabby session, then compiling them. Use when the user asks to \"create a skill / app template for <site>\", \"onboard <site> into Tabby\", \"record a login and a workflow\", \"turn this website into a tool\", \"noui capture/compile\". Runs inside THIS sandbox; Tabby auth is injected by the harness broker — no credentials. Recording is human-driven via a VNC link you surface to the user."
---

# NoUI (Agent Harness edition) — record → compile → author

You are inside the Agent Harness sandbox. This skill lets you onboard a website into
Tabby and produce a runnable Skill, end to end, on the **signed-in user's** identity:

1. **Record the LOGIN** → compile into a Tabby **App Template + ServiceProfile** (the
   reusable, per-user login).
2. **Record the WORKFLOW** (seeded with that login) → compile into a **harness Skill**
   (`call_web_api` operation cards).

You drive the scripts with `bash`; **the human drives the browser** through a VNC link
you surface to them. Never ask the user for a Tabby token, never write a `.env`.

## ⚠️ Critical rules — this is the Harness, not a local CLI

The toolkit zip ships its own `SKILL.md` for *local CLI* use. **Ignore its setup/auth
instructions.** In the harness:

1. **Cloud Tabby via the broker.** `TABBY_API_URL` is already set to the control-plane
   **broker** (fronting cloud Tabby). Never use `localhost`, never change it.
2. **No credentials.** Do not create a `.env` or set `TABBY_CLIENT_ID/SECRET/ADMIN_TOKEN`.
   The broker injects the user's federated bearer (`owner_user_id` = the real user) on
   every call. Profiles/templates you create are **owned by that user** automatically.
3. **Recording is human-in-the-loop.** `capture_record.py` returns a **VNC URL**. You
   cannot open it — you must **show it to the user as a markdown link, tell them what to
   do, and STOP** (end your turn). When they reply that they've finished, continue with
   `capture_import.py`. Recording spans multiple turns; that is expected.
4. **Harness execution mode for the skill.** Compile workflows with
   `--execution-mode harness` (never `tabby`/`http`).

## Step 0 — set up automatically (do this first, silently)

1. Call `read_skill_file(skill_name="noui", path="noui-bundle.zip")` to stage the toolkit.
2. Then one `bash` call:
   ```bash
   set -e
   mkdir -p /workspace/noui && cd /workspace/noui
   # NOTE: the slim sandbox image has no `unzip` — use Python's stdlib zipfile.
   python -c "import zipfile; zipfile.ZipFile('/workspace/skill_assets/noui/noui-bundle.zip').extractall('/workspace/noui')"
   pip install -e . -q
   python - <<'PY'
   import os
   assert os.environ.get("NOUI_TABBY_AUTH_MODE")=="broker", "harness broker not configured — tell the user the authoring path is unavailable"
   assert os.environ.get("NOUI_BROKER_TOKEN"), "missing broker capability token"
   print("noui ready; broker auth active; Tabby via", os.environ.get("TABBY_API_URL"))
   PY
   ```
   If the asserts fail, stop and tell the user the broker isn't configured for this turn.

Ask the user up front for: the **site** (login URL + the workflow to capture) and a short
**skill/app name**.

## Part A — create the App Template (record the LOGIN)

> **Skip Part A entirely** if the site already has a Tabby profile / App Template
> (the user will say so, e.g. *"the profile `adopt-bank` is ready"*). Go straight
> to **Part B** and provision the workflow recording with `--profile <profile_id>`
> — the recorder starts already authenticated via that profile, no login needed.

> **Static API-key apps also skip Part A** (there is no login session to record).
> If the user says the app authenticates with a static API key, do **only** Part B
> (record the workflow, no `--profile`/`--from`) and compile it with
> `--auth-type api-key --api-key-header <header>` (default `Authorization`). The
> compile prints a `${SECRET:...}` name; an admin registers the key value in the
> harness secret store. **Otherwise leave `--auth-type` at its default** (`session`).

> **Combined capture collapses Part A + Part B into one session.** When the user
> prefers to sign in and drive the workflow in a single sitting, provision with
> `capture_record.py --mode combined --url "<LOGIN_URL>"`, have them sign in **and
> then** drive the workflow before *Finish & export*, and import once with
> `capture_import.py <session_id> --combined --as skill --execution-mode harness
> --name "<app>"`. NoUI splits the one capture at the login boundary, registers the
> login App Template, and compiles the workflow (session-auth) bound to it — no
> separate `--from`/`--profile-slug` step. Prefer the split A/B flow when you want to
> confirm the login registered before recording the workflow.

### A1. Provision the login recording, surface the VNC link, STOP
```bash
cd /workspace/noui
python scripts/capture_record.py --mode login --url "<LOGIN_URL>"
```
This prints `session_id` and a short, redaction-safe **`login_url`** (`.../s/<id>`) that
opens the **recording** viewer (with the **Finish & export** toolbar). **Show the user
the `login_url` as a clickable markdown link.** Say: *"Open this, sign in to the site,
then click **Finish & export** in the viewer, and tell me when you're done."* Record the
`session_id`. **End your turn here** — do not poll, do not continue until the user
confirms.

### A2. Import the login → App Template (after the user confirms)
```bash
cd /workspace/noui
python scripts/capture_import.py <login_session_id> --name "<app-name>"
# same-origin apps: add --post-login-url-pattern '<glob the logged-in URL matches>'
```
Registration is **template-first**: this creates a tenant-wide **App Template**
only — it does NOT create an App/ServiceProfile directly. Tabby auto-provisions a
private, per-user App+Profile (straight to ACTIVE) on each member's first
`call_web_api`, so there is no `--promote` step and the credential model defaults
to `manual:` (the member logs in via VNC; nothing is stored). Note the **profile
slug** it reports — you need it for Part B. The drained bundle is saved under
`workbench/bundles/`.

## Part B — author the Skill (record the WORKFLOW)

### B1. Provision the workflow recording, surface the link, STOP

Pick the variant by how the profile got set up:

```bash
cd /workspace/noui
# (a) Existing profile / App Template already set up (Part A skipped) — RECOMMENDED
#     when the user says the profile is ready. Auth comes from the profile.
python scripts/capture_record.py --mode workflow --profile <profile_id> --url "<START_URL>"

# (b) You just recorded the login in this same flow (Part A above): seed from it.
python scripts/capture_record.py --mode workflow --from <login_session_id> --url "<START_URL>"
```
`--profile <profile_id>` starts the recorder authenticated via that Tabby profile (skip
login); `--from <login_session_id>` instead reuses cookies from a login you just captured.
There is **no `--from-profile`** flag. Again surface the printed **`login_url`** (the short,
recording-viewer link): *"Open this and drive the exact workflow you want as a tool (e.g.
run the search / open the report), then click **Finish & export** and tell me when
done."* **End your turn.**

### B2. Import the workflow → harness Skill (after the user confirms)
```bash
cd /workspace/noui
python scripts/capture_import.py <workflow_session_id> \
    --as skill --execution-mode harness \
    --profile-slug <profile-slug> --name "<skill-name>"
```
`<profile-slug>` is the slug from A2, or — if you skipped Part A — the existing profile
id the user gave you (e.g. `adopt-bank`). The compiled skill lands under
`workbench/skills/<app>/` as `call_web_api` cards + `operations.json` (no transport code).

For a **static API-key app** (Part A skipped, no profile), drop `--profile-slug` and add
`--auth-type api-key --api-key-header <header>` instead — the skill authenticates from a
`${SECRET:...}` the admin registers, not a session. Leave `--auth-type` unset otherwise.

> If the profile already has a HEALTHY session, you may instead drive the workflow
> **agent-side** with `capture_autopilot.py <slug> --steps steps.json --as skill
> --execution-mode harness` (no human VNC) — see the toolkit reference. For a brand-new
> profile, the VNC workflow recording above is the reliable path.

## Step B3 — generalize: prune the noise, then test until it works

The B2 compile is a **raw** mirror of the recording — it includes calls that aren't
part of the task (analytics / config / keepalive pings, typeahead/prefetch, third-party
hosts) and names lifted straight from the API. **Before installing**, clean it up (this
is an LLM-driven step you do — no script):

1. **Prune noise.** Read `workbench/skills/<app>/operations.json`; remove any operation
   that doesn't serve the workflow goal (telemetry/analytics/consent/keepalive,
   typeahead/prefetch, duplicates, any non-app host) — delete its entry from
   `operations.json` and its card from the skill's `SKILL.md`.
2. **Test the survivors.** For each remaining operation, invoke the **`call_web_api`**
   tool with its recipe from `operations.json` (pass any `${SECRET:...}` verbatim). Run
   each **2–3 times** and confirm it returns the expected data — not just a non-error.
   You can do this now: `call_web_api` is a catalog tool, not part of the skill.
3. **Fix or drop.** Empty creds / 401 → the profile's session isn't healthy (recheck
   Part A/B); 429 → retry; wrong/empty body → recompile from the saved bundle
   (`compile_workflow.py <bundle.json> --as skill --execution-mode harness`). If an op
   can't be made to work and isn't essential, drop it.
4. **Make it reusable.** Rename cryptic tool/param names to natural language and
   parameterize hardcoded recorded values, in `operations.json` + the `SKILL.md` cards.
5. **Loop** until every remaining operation passes 2–3 clean runs. Only then install.

Full playbook: `skills/noui/references/generalize.md`.

## Step C — install the skill into the harness catalog

Once compiled **and generalized (Step B3)**, **install it** so it becomes a usable tool on future turns. Call the
**`install_skill`** harness tool (NOT a bash command — it's a tool in your catalog)
with the compiled directory:

```
install_skill(skill_dir="/workspace/noui/workbench/skills/<app>")
```

It writes the skill to the org catalog (listable on a new turn, ~30s). A skill whose
auth is the signed-in user's **session** (the usual login-recording case — e.g. a
Tabby `profile_slug` was bound) needs **nothing more**: it is runnable as soon as it's
listed. Do **not** invent a secret-registration step for these.

`install_skill` returns a `${SECRET:...}` name list, but it is populated **only** when
the API needs an *extra static secret* (an API key sent on every request, on top of —
or instead of — the session). **Only if that list is non-empty**, relay it to the user:
an org admin must register each named secret in the org secret store before the skill
can run. **If it's empty (the common session-auth case), there is no secret step — say
nothing about secrets.**

Then report the deliverable: the profile slug, the compiled skill path, and the
installed skill name — plus the required secret(s) only if `install_skill` returned any.

## Troubleshooting

- **`login_required` / session not healthy on import** — the login wasn't completed; have
  the user redo the VNC sign-in and click *Finish & export*.
- **403 from Tabby** — the profile isn't owned by / reachable for this user; re-record so
  it's created under the user's identity.
- **VNC link won't load** — it expires; re-run the `capture_record.py` step to get a fresh
  one.
