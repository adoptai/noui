---
name: noui
description: "Author a new Tabby App Template + Skill from a website by recording the login and the workflow through the signed-in user's CLOUD Tabby session, then compiling them. Use when the user asks to \"create a skill / app template for <site>\", \"onboard <site> into Tabby\", \"record a login and a workflow\", \"turn this website into a tool\", \"noui capture/compile\". Runs inside THIS sandbox; Tabby auth is injected by the harness broker — no credentials. Recording is human-driven via a VNC link you surface to the user."
---

# NoUI (Agent Harness edition) — record → compile → author

You are inside the Agent Harness sandbox. This skill lets you onboard a website into
Tabby and produce a runnable Skill, end to end, on the **signed-in user's** identity:

1. **One recording** — the human signs in *and* drives the workflow in the same browser
   session (a single VNC link).
2. **One import** → NoUI splits that capture into a Tabby **App Template + ServiceProfile**
   (the reusable, per-user login) *and* a **harness Skill** (`call_web_api` operation cards).

You drive the scripts with `bash`; **the human drives the browser** through a VNC link
you surface to them. Never ask the user for a Tabby token, never write a `.env`.

## ⚠️ Critical rules — this is the Harness, not a local CLI

The toolkit zip ships its own `SKILL.md` for *local CLI* use. **Ignore its setup/auth
instructions.** In the harness:

1. **Cloud Tabby via the broker.** `TABBY_API_URL` is already the control-plane broker
   (fronting cloud Tabby): never `localhost`, never change it, and **never build a
   user-facing URL from it** — it is an internal API, not a browsable site. Show only links
   a script printed verbatim; copy them, never compose them.
2. **No credentials.** Do not create a `.env` or set `TABBY_CLIENT_ID/SECRET/ADMIN_TOKEN`.
   The broker injects the user's federated bearer (`owner_user_id` = the real user) on
   every call. Profiles/templates you create are **owned by that user** automatically.
3. **Recording is human-in-the-loop, one link at a time.** `capture_record.py` prints a VNC
   URL you cannot open: show it as a markdown link, say what to do, and **end your turn**.
   Never put two links in one message — the user can't tell which to open. Recording spans
   multiple turns; that is expected.
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

## Agree on the goal first — one line, then record

A skill's goal is what the member asked for ("download the annual statement"), NOT one
badge per operation. Establish it **before** recording:

- **Clear ask** → state it in ONE line together with the recording link and go — no
  separate wait: *"Recording a skill to **download the annual statement**. In that window,
  do this…"* Don't over-ask; a clear ask needs a single confirming sentence.
- **Vague ask** ("onboard ICICI") → ask first, then record: *"What should this skill
  produce — a downloaded file, a value read off a page?"* Wait for the answer. Don't guess.
- **Multiple goals are fine** — they can name several ("download the statement **and** the
  last 3 months' transactions"); record to cover them all.

When you give the recording link, tell the member to **demonstrate reaching each goal** —
actually download the file, actually submit the form — so the recording carries evidence
the goal was met.

## Part A — capture login + workflow in ONE session (the default)

```bash
cd /workspace/noui
python scripts/capture_record.py --url "<LOGIN_URL>" --name "<app-name>"
```

No `--mode` needed — with no `--profile`/`--from` it defaults to **combined**: the human
signs in *and* drives the workflow in the same browser, then clicks *Finish & export*
once. NoUI splits the capture at the login boundary afterwards.

It prints `session_id` and a short, redaction-safe **`login_url`** (`.../s/<id>`) that
opens the recording viewer (with the *Finish & export* toolbar). **Show the user that one
link as a clickable markdown link** and tell them, in this order:

1. Sign in to the site.
2. **Then keep going in the same window** and drive the exact workflow you want as a
   tool (run the search, open the report, browse the listings).
3. Click **Finish & export**.
4. Come back and say "done".

Then **end your turn**. Do not poll. Do not mention any other link yet.

> **Skip the login half** when the site already has a Tabby profile / App Template (the
> user will say so, e.g. *"the profile `adopt-bank` is ready"*): pass
> `--profile <profile_id>` and the mode defaults to workflow-only — the recorder starts
> already authenticated, so there is no login to record.
>
> **Apps with no login at all** — unauthenticated, or authenticated by a static API key —
> need `--mode workflow` **explicitly**, because the default would tell the user to sign in
> to nothing. For a static key, record only the workflow (no `--profile`/`--from`) and
> compile with `--auth-type api-key --api-key-header <header>` (default `Authorization`);
> the compile prints a `${SECRET:...}` name for an admin to register. **Otherwise leave
> `--auth-type` at its default** (`session`).
>
> **Record the halves separately** (`--mode login`, then `--mode workflow --from
> <login_session_id>`) only when you have a specific reason — e.g. you must confirm the
> App Template registered before spending the user's time on the workflow. It costs an
> extra link and an extra sign-in, so it is not the default.

## Part B — import: App Template + Skill from that one capture

```bash
cd /workspace/noui
python scripts/capture_import.py <session_id> --as skill --execution-mode harness \
    --name "<app-name>"
# same-origin apps: add --post-login-url-pattern '<glob the logged-in URL matches>'
```

No `--combined` flag and no `--profile-slug`: `capture_record.py` recorded the mode in the
provision ledger, so the import splits the capture, registers the login App Template, and
compiles the workflow (session-auth) bound to that new profile in one step. It prints the
**profile slug** — note it.

Registration is **template-first**: a tenant-wide **App Template** only, never a direct
App/ServiceProfile. Tabby auto-provisions a private, per-user App+Profile (straight to
ACTIVE) on each member's first `call_web_api`, so there is no `--promote` step and the
credential model defaults to `manual:` (the member signs in via VNC; nothing is stored).
The drained bundle is saved under `workbench/bundles/`.

### Confirm the goal, and heed the coverage warning

`capture_import` prints the recording's **endpoint goal** and, when the recording produced
no download and no submit, a **coverage warning**. Act on both — all one-liners, never hard
gates:

- **Member never stated a goal** → confirm the inferred one in ONE line before installing:
  *"I read your goal from the recording as **download annual statement** — correct?"* Then
  proceed. An explicit ask always wins; this is only the floor.
- **Stated goal and the endpoint disagree** — they asked for the annual statement but the
  recording ended on the monthly one → **say so** and let them decide. The stated goal is
  the intent; the mismatch is worth a sentence.
- **Coverage warning** (nothing produced a result — a flaky server, a walkthrough that
  stopped short) → **relay it, don't block.** The skill still compiles; the member decides
  whether to re-record or proceed.

### The sign-in comes next — let it happen

The profile's runtime session needs one sign-in (see below). Go straight to B3: the first
`call_web_api` returns `login_required` and **the platform prompts the user itself**. Say what
it's for, wait, re-run. Don't pre-check the session (it sits in `STARTING` for minutes) and
don't build a card or link of your own — you have no sign-in URL (rule 1).

## Workflow-only capture (existing profile, or the split flow)

When the login half is already covered — the user has a profile, or you deliberately
recorded the login separately — record just the workflow:

```bash
cd /workspace/noui
# (a) Existing profile / App Template. Auth comes from the profile.
python scripts/capture_record.py --url "<START_URL>" --profile <profile_id>

# (b) You recorded the login separately in this same flow: seed cookies from it.
python scripts/capture_record.py --mode workflow --url "<START_URL>" --from <login_session_id>
```

Either way the mode resolves to workflow-only. There is **no `--from-profile`** flag.
Surface the printed `login_url` — *"open this and drive the exact workflow you want as a
tool, then click Finish & export and tell me when done"* — and **end your turn**. Import
with an explicit profile binding:

```bash
python scripts/capture_import.py <session_id> --as skill --execution-mode harness \
    --profile-slug <profile-slug> --name "<skill-name>"
```

The compiled skill lands under `workbench/skills/<app>/` as `call_web_api` cards +
`operations.json` (no transport code).

For a **static API-key app** (no profile at all), drop `--profile-slug` and add
`--auth-type api-key --api-key-header <header>` instead — the skill authenticates from a
`${SECRET:...}` an admin registers, not a session.

> If the profile already has a HEALTHY session, you may instead drive the workflow
> **agent-side** with `capture_autopilot.py <slug> --steps steps.json --as skill
> --execution-mode harness` (no human VNC) — see the toolkit reference. For a brand-new
> profile, the VNC recording above is the reliable path.

## Two sessions, two sign-ins (say this up front)

Two browser sessions exist: the **recording** (Part A) and the profile's **own runtime
session**, which Tabby provisions per user on first `call_web_api`. The second stores nothing
— reusing the recording's cookies would share one user's session with the whole org — so it
asks for one sign-in. Correct, not a bug, but tell the user it's coming or it won't look that
way. Recording the halves separately adds a third sign-in; that's why it isn't the default.

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
3. **Fix or drop.** `login_required` / empty creds / 401 → expected on the *first* call: the
   platform prompts the user itself — describe it, wait, re-run. Never build a card or URL
   (rule 1).
   429 → retry; wrong/empty body → recompile from the saved bundle
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

## Diagnosing a surprising compile — `bundle_inspect.py`

If an import produces the wrong kind of asset, or operations you didn't expect, **don't
write a throwaway script** — inspect the bundle:

```bash
cd /workspace/noui
python scripts/bundle_inspect.py workbench/bundles/<name>.json
```

It prints the **mode block** (Tabby's `recording_mode` vs NoUI's classification vs the
mode the session was provisioned as, with a warning on any mismatch), the URL timeline
with the login boundary marked, credential interactions (roles + redaction only), and the
API endpoint table built with the compiler's own filter and naming — i.e. a preview of the
operation set, useful for the B3 prune.

**On modes:** Tabby's `recording_mode` is unreliable (a warm-pool session always reports
`login`), and NoUI ignores it. `capture_import.py` decides from `--mode` → the provision
ledger written by `capture_record.py` → the capture's content, and prints which won. If it
ever picks wrong, pass `--mode {login,workflow,combined}` explicitly.

## Troubleshooting

- **`login_required` on the first live `call_web_api`** — expected, not a failure: the
  profile's own session needs its one sign-in (see *Two sessions, two sign-ins* above). The
  platform prompts the user; nothing for you to run or build. Describe it, wait, re-run. If no
  prompt appears, say so. Only a problem if it repeats *after* they've signed in.
- **`login_required` / session not healthy on import** — the login wasn't completed; have
  the user redo the VNC sign-in and click *Finish & export*.
- **The compiled asset is the wrong kind** (a workflow recording registered as an App
  Template, or vice versa) — run `bundle_inspect.py` on the saved bundle and re-import
  with an explicit `--mode`.
- **403 from Tabby** — the profile isn't owned by / reachable for this user; re-record so
  it's created under the user's identity.
- **VNC link won't load** — it expires; re-run the `capture_record.py` step to get a fresh
  one.
