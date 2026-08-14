# Browser-Skill Evidence: Compile & Prove-by-Replay — Status & Gaps

**PR:** [adoptai/noui#139](https://github.com/adoptai/noui/pull/139) ·
**Branch:** `feat/browser-skill-evidence` · **Updated:** 2026-08-14

Companion feature in tabby:
[adoptai/tabby#163](https://github.com/adoptai/tabby/pull/163) (records the
evidence this branch compiles from). See that PR's
`docs/workflow-recording-capture-STATUS.md` for the capture side.

A living reference: what the feature does, what we hardened, and the gaps still
worth fixing. Update it as gaps close.

---

## What this feature delivers

Turn a **recording of a human doing a workflow** into a **browser skill**
(`operations.json` + `SKILL.md`), then **prove it by replaying against a live
session** before anything installs — so a skill is backed by observed evidence,
not hand-written selectors.

- **Compile from evidence** — each recorded page becomes a read operation;
  downloads/submits become terminal operations; the click chain to reach a page
  is reused so the session is never reloaded.
- **Parameters from recorded inputs** — recorded dates/years/amounts/selects
  become operation parameters (recorded value as default), so "last month"'s
  capture can fetch "last year". Credentials and placeholder `<select>` values are
  never parameters.
- **Locator provenance** — the compiled skill is bound to the recording by digest;
  `unobserved_locators` refuses a skill whose steps target controls the recording
  never saw (catches hand-edited/fabricated operations). Digest vectors are pinned
  in **both repos**.
- **Replay = the real thing** — replay starts where a real run starts (session
  provisioned from profile, post-login landing), runs each operation, and shows
  the human what happened.
- **Risk gate** — `classify_risk` stops a step that moves money / is irreversible
  / touches a control the human never touched, and hands it back for explicit
  approval, so an improvising agent can't reach Pay/Transfer while hunting a
  statement.
- **Goal check** — `goal_reached` judges a download by its *file*, not by "every
  step passed" (an all-green download that produced no file has not succeeded).
- **Member approval + amendments** — install is gated on a member approving the
  replay; `check_installable` refuses unapproved amendments, matching the harness
  gate.
- **Segments** — each operation replays as its own segment (no full-chain
  duplication).

Honours tabby recording **`schema_version 5`**; pre-5 bundles must compile exactly
as they did.

---

## What we hardened (this review round → `c26680c`)

| Ref | Area | Fix |
|----|------|-----|
| N1 | `compile/login_assets.py` | `_before_first_click` dropped the post-login landing for the **standard form login** (the "Log In" click itself triggers the redirect). Now extends the login segment to that redirect when nothing landed before the first click; SSO/auto-submit unchanged. |
| N2 | `verify/replay.py` | `classify_risk` **failed open** on a control it couldn't identify — `click_at` (coords) / `press_key` (focused element) carry no selector/label/text, so both risk grounds skipped them. Now fail-closed for any control action with no identity; page/browser commands (navigate, scroll, har, get_download) are allow-listed. |
| N3 | `verify/replay.py` | `_RISKY_TEXT` widened: `withdraw, payee, beneficiary, loan, purchase, buy, sell, invest, redeem, wire`. |
| N4 | `compile/parameters.py` | `_CREDENTIAL_ROLES` was `{password, otp}`, narrower than canonical `CREDENTIAL_FIELD_ROLES` — an unredacted `unknown_sensitive` field (a re-entered PIN) could compile to a plaintext default. Now reuses the canonical set (adds `username`, `unknown_sensitive`). |
| N5 | `compile/browser_skill.py` | `_terminal_kind` gated `download` on `outcome` but not `submit`, so a pre-schema-5 non-login submit compiled a **new** operation, breaking backward-compat. Now gates `submit` on `outcome` presence too. |
| N6 | `compile/provenance.py`, `scripts/verify_replay.py` | Added `unbacked_control_steps`: a control-acting step with **no locator** would contribute nothing to `unobserved_locators` and "verify" against any bundle. Now refused. (Latent — the compiler never emits such steps — defense-in-depth.) |

Earlier hardening already on the branch (highlights): dropdown = chosen not
clicked (`dba2795`, `5a68999`), radio bound to the submitting gesture (`2586503`,
`5d7decd`), account numbers refused into skills (`cf4af67`), hover-reveal folded
into one step (`954b966`, `4ca4ff4`), replay judged by the file and this-run
freshness (`84d68e4`, `655fbdc`), segments replacing full-chain duplication
(`98fe50b`, `6d34e4c`), Tabby-unreachable no longer crashes/loses amendments
(`fb0e395`, `ea8327f`), `check_installable` refuses unapproved amendments
(`c8a45c1`).

---

## Open gaps that need attention

| ID | Sev | Location | Issue | Recommended direction | Status |
|----|-----|----------|-------|-----------------------|--------|
| **G1** | Med / correctness | `compile/browser_skill.py` (terminal ops for the ICICI statement) | **Inter-operation statement toggling.** The ICICI infinity flow reloads the statement page (defaults to *Monthly*) and sets *Annual* in `submit_..._2` and again in `download_...`, so replay cycles monthly→annual→monthly→annual. An intra-op de-dupe was tried and **abandoned** (validated only against *stale* accumulated downloads; it does **not** stop the *inter-operation* toggling). | Needs a fresh-download validation and a cross-operation approach — likely collapsing the redundant reload+set when a later terminal subsumes an earlier one, or checking `_truncate_reads_at_terminals` for the subsumed case. Not a clean intra-op fix. | **OPEN** (experiment reverted; not on branch) |
| **G2** | Med / limitation | `verify/session.py` `_return_to_entry` | **Cross-origin replay reset.** On navigate-forbidden apps (ICICI) the replay can't reset between operations: `navigate` is blocked, `go_back` kills the session, and there's no way-back link from corp/Finacle to corp/AuthenticationController. Download works because it starts where the browser already sits. | Fundamental for such portals. Options: replay operations in an order that never needs a reverse jump, or accept per-portal "cannot reset" and segment accordingly. (User deferred: "ignore" for now.) | **OPEN / deferred** |
| **G3** | Med / cross-repo | install path (workflows/webui/design-system) | **Member-approval token not produced in Studio Playground.** The install gate (`_member_approved_replay`, workflows #1552) requires a fingerprint-bound approval token in the member's turn, minted by the **skill-replay card** (design-system #17, wired in webui #1631). In the Studio Playground run, the card was **never emitted** (0 in transcript) and the local frontend pins `genui-components 0.1.76` while the card shipped in `0.1.78`, so approval fell back to a plain-text "yes install" → no token → install refused ~24×. | Ensure the Studio/Playground path emits the skill-replay card and the frontend ships the card version, OR provide an alternate token path for that flow. This blocks install end-to-end in that environment even when record→compile→replay→approve all pass. | **OPEN** (RCA done this session) |
| **G4** | Low / follow-up | amendments | **Amendment-baking follow-up.** Tracked separately (scheduled). | Land the amendment-baking PR. | **OPEN / scheduled** |

---

## Known limits (deliberate, not bugs)

- **N2 fail-closed scope.** The risk gate only allow-lists `navigate, scroll_page,
  wait_for_url, har_*, get_download` as legitimately control-less. A genuinely new
  control-less command added later would be treated as risky until added to the
  list — intentional (fail closed), but worth remembering when adding commands.
- **N6 is latent.** The shipped compiler never emits keyless control actions, so
  `unbacked_control_steps` only ever fires on hand-authored operations. Kept as a
  guard, not a hot path.

---

## Cross-repo dependency chain

Record (tabby #163) → **compile + replay (this PR)** → replay card
(design-system #17) → card wiring (adoptwebui #1631) → install gate
(adoptai-workflows #1552). The provenance **fingerprint/digest vectors are pinned
in both this repo and adoptai-workflows** — if the two implementations drift,
every real skill is refused. See G3 for the current end-to-end install blocker.

---

## Verify

```bash
poetry run python -m pytest tests/test_replay.py tests/test_parameters.py \
  tests/test_login_landing_url.py tests/test_browser_skill.py \
  tests/test_provenance.py tests/test_compile_is_stable.py \
  tests/test_nothing_recorded_is_silently_dropped.py tests/test_migrate_provenance.py -q
poetry run ruff check skills/noui/ && poetry run ruff format --check skills/noui/ tests/
poetry run mypy skills/noui/noui_core/          # optional group
```
