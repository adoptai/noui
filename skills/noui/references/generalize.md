# Generalize — prune the noise, then test until it works

**This is an agent step, not a script.** The initial compile is a faithful, *raw*
mirror of everything the browser did during the recording. It therefore includes
calls that aren't part of the task, and names lifted straight from the site's API.
Generalization is the LLM-driven pass **you** (the agent that captured/compiled the
skill) run after compile and **before** installing/delivering it: cut the workflow
down to the operations that actually accomplish the goal, prove they work, and make
them legible so any agent can call them.

Do this every time immediately after Compile (Pillar 2). The saved capture bundle
(`workbench/bundles/<name>.json`) is the source of truth — recompile from it for
deep changes (`python scripts/compile_workflow.py <bundle.json> --as …`); never
delete it.

## 1. Inventory the compiled operations
Read what compile produced:
- **harness** skills → `workbench/skills/<app>/operations.json` (+ the operation cards in `SKILL.md`).
- **tabby/http** skills/servers → `operations/<tool>.py` + `API.md` (+ `manifest.json`'s `operations`/`tools.json`).

For each operation note: method, path, what it appears to fetch, and whether it is
plausibly part of the user's stated workflow goal.

## 2. Prune the noise (useless calls)
Compile already drops static assets, obvious analytics paths, and redirects, but a
raw workflow still carries calls that are **not part of the task**. Remove any
operation that does not contribute to the goal:
- telemetry / analytics / metrics / consent / feature-flag / session-keepalive / config pings that slipped through;
- typeahead / autocomplete / prefetch / "suggestions" calls that aren't the end result;
- intermediate calls whose data the final call already returns (duplicates / stepping stones);
- anything to a host that is **not the app's own API** (third-party widgets, CDNs, ad/marketing pixels).

Delete pruned operations from the asset:
- **harness**: remove the entry from `operations.json` and its card from `SKILL.md`.
- **tabby/http**: delete `operations/<tool>.py`, and remove it from `manifest.json` `operations` (and `tools.json` for MCP).

When in doubt, keep it for now and let the test in step 3 decide — a call that
returns nothing useful is noise.

## Before you test: three sessions, up to three sign-ins

Testing hits a **different browser session** from the ones you just recorded. Three are
involved:

1. the **login recording** session (the human drove it),
2. the **workflow recording** session (a second one, usually cookie-seeded from #1),
3. the **profile's own session** — what `call_web_api` / `/execute/fetch` actually resolve.
   Tabby auto-provisions it per user from the App Template, on first use.

Session #3 starts `LOGIN_NEEDED`: the template registers `credential_ref: manual:`, so
nothing is stored. Recording cookies are deliberately **not** reused for it — the template
is tenant-wide, so seeding them would hand the recorder's live session to every member of
the org. Per-user auth with nothing stored costs one sign-in.

So expect **one activation sign-in** before the first live call, even though the human just
signed in during capture. Get it out of the way before step 3 rather than discovering it as
a `login_required` mid-test:

```bash
python scripts/activate_session.py <profile-slug>
# or, at import time:
python scripts/capture_import.py <session_id> --name <app> --activate-session
```

Either prints a sign-in link when one is needed, or confirms the session is already
HEALTHY.

## 3. Test the survivors — a couple of times each
**Actually run every remaining operation against the live site** and confirm it
returns what the workflow needs (right status, non-empty, the expected fields) —
not just that it doesn't error. Run each **2–3 times** to confirm it's stable, not a
one-off or intermittently blocked.

- **harness**: invoke the **`call_web_api`** tool with the operation's recipe from `operations.json` (you can do this before install — it's a catalog tool, not part of the skill). Pass `${SECRET:...}` placeholders verbatim; the harness substitutes them.
- **tabby/http**: `python operations/<tool>.py <args>` (needs a HEALTHY Tabby session for the profile). Run `python scripts/activate_verify.py <server_dir>` first for a deterministic auth dry-run.

## 4. Fix or drop failures
- **`login_required` / empty credentials / 401 / profile 404** → the profile's own session
  needs its one activation sign-in (see the section above) or the slug is wrong. Run
  `python scripts/activate_session.py <profile-slug>`, have the human open the link it
  prints, then re-test.
- **429 / bot detection** → prefer browser-side execution (`--execution-mode tabby`, `/execute/fetch`) over `http`; retry.
- **Wrong or empty payload** → the first compile may have dropped/duplicated a request body (e.g. a GraphQL query lost to `/graphql` dedup). Recover it from the saved bundle and recompile (`compile_workflow.py <bundle.json>`), then re-test.
- **Can't be made to work and isn't essential** → drop it (step 2).

## 5. Make it reusable (rename + parameterize)
Once the survivors pass, give them a legible interface so any agent can call them
without site knowledge:
- **Rename** cryptic tool and parameter names (`get_v3_rpt`, `f_sid`, `bl`, `reqid`) to natural-language ones (`get_report`, `origin`, `destination`, `departure_date`).
- **Parameterize** hardcoded recorded values that should be inputs (dates, ids, search terms) into named params with descriptions.

Small renames can be edited in place in `operations.json` / `SKILL.md` cards (harness)
or the operation files + `manifest.json` (tabby/http). Deeper reshaping is easiest by
recompiling from the bundle and re-applying — the bundle is the durable source.

## 6. Re-verify and loop
After any edit, re-run step 3 for the affected operations. **Iterate until every
remaining operation passes 2–3 consecutive clean runs** and returns the expected
data. Only then install/deliver the skill (Pillar 3).

## Definition of done
- The profile's own session is HEALTHY (its activation sign-in is done).
- No operation to a non-app / tracking / telemetry host remains.
- Every remaining operation has been run against the live site and fetched the expected data, repeatably.
- Tool and parameter names are natural-language; task inputs are parameters, not baked-in values.
- The saved bundle is intact (so the asset can be regenerated later).

See also: [pillar-2-compile](pillar-2-compile.md), [pillar-3-activate](pillar-3-activate.md), [pillar-1-capture](pillar-1-capture.md).
