# Replay decision card + goal model — design spec

**Status:** design locked, not yet built · **Updated:** 2026-08-14

A reference for building the goal-driven, one-replay-then-decide flow across four
repos. Every decision below is settled; the "Deferred" section lists what is
intentionally out of v1.

---

## The problem this fixes

When a replay doesn't fully pass, the agent is currently free to **iterate on its
own** — amend, `--approve-step`, force-click, re-record, re-run — and each guess
is often wrong and makes things worse (session drift, bundle desync, session
expiry). Observed on staging: install loops of 14 and ~50 tool calls.

Separately, the card reports **operations** as if each were a goal ("2 of 3
operations reached their goal"). The user asked for one thing. The verdict should
be about that one thing.

Two fixes, one flow: **replay runs once, then a decision card is handed to the
human**, and the card is **headed by the user's goal(s)**, not the compiler's
operations.

---

## Goal model (the spine)

A **goal** is a user-stated outcome ("download the annual statement"), not a
per-operation badge. Operations are the *mechanism* that reaches goals; the
terminal operation (the download/submit that produces the artifact) *is* the
goal, and the read/nav operations are its path.

### Precedence for establishing goal(s)
1. **Clear ask** → one-line **chat confirm** → goal = the ask.
   *"Recording a skill with 1 goal: download the annual credit-card statement. Add or change goals before we start?"*
2. **Vague ask** ("onboard ICICI") → **ask the user to state the outcome(s)
   plainly** — don't guess.
3. **Vague ask AND nothing stated** (they just recorded) → **infer one goal from
   the recording's tail**, **one-line chat confirm before compile**, and tag it
   `inferred` on the card.

Explicit intent always wins; inference is only the floor.

### Multi-goal
- **Stated asks support N goals** — "download the annual statement **and** last 3
  months' transactions" = 2 goals → 2 terminal operations. The card shows N goal
  headlines, each reached/not, with that goal's operations as diagnostics beneath.
- **Inference collapses to exactly one goal** (a silent recording can't be
  multi-goal-inferred reliably) — but it's surfaced at the confirm line, so the
  user can correct it or add a second goal there.

### Inference rule (case 3)
Pick the goal from the tail, preferring the **last terminal** (download/submit)
over the **last navigation** — a completed artifact is a far stronger intent
signal than wherever the cursor stopped.
- Last terminal near the end → that's the goal ("download annual statement"),
  even if idle clicks followed it.
- No terminal, only navigation → the final page reached is the goal.
- Name it from the terminal control's label (reuse `_op_name`).

### Coverage checks — **warn, never block**
Compare the goal (stated or inferred) against what the recording actually
demonstrated:
- **Goal not demonstrated** (server failed mid-record, no file arrived) → warn,
  mark the goal "not demonstrated," proceed.
- **Stated goal ≠ recording tail** (asked annual, ended on monthly) → warn. The
  stated goal stays the intent; the mismatch is flagged. (This is the
  "compiled a monthly statement while every check passed" trap, caught early.)

### Success criterion is per goal *type*
The ask names the goal; the goal type defines "reached":
- **download** → a **fresh** file arrived (existing `list_downloads` + the
  download-freshness baseline).
- **read** → the value was extracted.
- **submit** → the result page/response came back.

---

## Replay flow

- Replay runs **once**. After it, the harness **mechanically emits the card and
  pauses the turn** (the way the sign-in card pauses). The agent does **not**
  loop, amend, or force on its own — its only post-replay move is "the card is
  shown; wait for the human." Guidance alone won't hold; this must be mechanical.
- Every re-run afterward is **human-initiated** (one per action), so the loop
  never reopens.

```
ask ──clear?──▶ confirm goal (chat line) ─┐
   └─vague──▶ ask for goal ──stated?──▶  ─┤
                          └─no──▶ record, then INFER 1 goal from tail,
                                          confirm before compile ────────┤
                                                                          ▼
                                            record to cover goal(s)
                                                     │
                                      coverage check (warn, never block)
                                                     │
                                       replay ONCE → emit card + PAUSE
                                                     │
                          card headed by GOAL(s) reached/not; ops = diagnostics
```

---

## The card

Headed by the **goal(s)**; operations demote to expandable diagnostics (which
step, why, and a category cue — blocked vs transient-server vs login).

| Element | Behavior |
|---|---|
| Goal headline | the user's words, reached/not; `inferred` tag if it wasn't stated |
| Optional note | **one field, available for all actions**. The agent reads it on a re-run. If it implies changing what a step targets, that becomes an **amendment** re-surfaced for approval — it can't silently rewrite the skill, and can't bypass the provenance/risk gates. |
| **Re-run replay** | one human-initiated replay (with the note) |
| **Install as-is** | installs despite failing steps via `verify_approve --accept-failing` + the member-approval token. **Whole-skill for v1**, with per-goal / per-step flags recorded in provenance and surfaced to the operator at runtime. Styled as a caution (amber), not the primary. |
| **Request changes** | the **closed set** (use-recorded-path / drop-step / wrong-target) — deterministic, provenance-tracked structural edits. Coexists with the free-text note (structural vs advisory). |

### The note vs Request changes
Kept as two channels on purpose: **Request changes** is the closed, deterministic
path for structural edits (becomes part of the skill, provenance-tracked); the
**note** is the softer "here's what's going on / try this" advisory that only
becomes a structural change by routing through the amendment-approval loop.

---

## Cross-repo ownership

| Repo | Builds |
|---|---|
| **noui** | goal capture + inference from the recording tail; coverage warnings; the replay report keyed to **goals** (not operations) with per-step category/reason |
| **adoptai-workflows** (harness) | **emit-card-and-pause** after one replay; pass the note to the agent; install gate honors "install as-is / accept-failing" (whole-skill + flags) |
| **design-system** (genui-components) | the card: goal headline, `inferred` tag, note field, three buttons, per-step reason/category |
| **adoptwebui** | wire the note + the two new decisions (re-run / install-as-is) through `onSkillReplaySubmit`, same token path as approve |

Leans on machinery that already exists: **terminal detection *is* goal detection**,
and the **download-freshness check *is* the download-goal's success criterion**.

---

## Build order

1. **Harness: emit-card-and-pause after one replay.** This is what actually kills
   the 14/50-call flailing — land it first; everything else is refinement.
2. **Card + note + the two buttons** (design-system + webui).
3. **Goal model** (noui): chat-confirm for clear asks, inference for silent ones,
   coverage warnings, report keyed to goals.

---

## Deferred (not in v1)

- **Re-run failing-only** (`verify_replay --only`) — can't prove end-to-end and
  interacts badly with the cross-origin reset limitation; a naive "failing steps
  went green → install" would be a false green.
- **Per-goal install-as-is** — v1 is whole-skill-with-flags; per-goal granularity
  (ship the passing goals verified, accept only the failing ones) is a later
  refinement if partial skills are actually needed.

---

## Decisions log

- Replay runs **once**, then card + pause — **mechanical**, not agent guidance.
- Card actions: **Re-run replay / Install as-is / Request changes** (+ optional note).
- Note is **optional and available for all actions**; structural effects → amendment → re-approval.
- Goal = **user ask**, not per-operation; align **before recording**.
- **Chat-line confirm** for a clear ask; **one-line confirm before compile** when a goal is **inferred**, plus an `inferred` tag on the card.
- **Warn-not-block** on coverage.
- **Multi-goal** supported for stated asks; **inference → one goal** from the tail (prefer last terminal over last navigation).
- **Install as-is = whole-skill-with-flags** for v1.
