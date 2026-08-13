"""Replaying a DRAFT skill before anything is installed.

A recording is a claim about how an app works. Compiling it produces a plan that
looks right on paper — and every failure this project has hit looked right on
paper. `a.mb-0` reads like a selector. A missing download operation looks like a
skill with three operations. A hidden radio looks clickable. The only question
that matters is whether a step resolves against the live page, and the only way
to answer it is to try.

So: compile a draft, replay it against the real app in the session the human just
authenticated, show them what happened, and install NOTHING until they approve.

Three rules shape the design.

**Nothing is installed until a human approves.** The draft is compiled in place,
replayed, and either approved or amended. No App Template is registered, no
profile bound, no catalog entry written before that.

**Risky steps stop and ask.** The replay agent is allowed to improvise around a
blocked step, and an improvising agent on a bank portal can reach Pay, Transfer
or Block Card while hunting for a statement. Anything that moves money, is
irreversible, or touches a control the human never touched is refused here and
handed back for explicit approval — even when it looks like the way forward.

**Replay starts where a real run starts.** Not where the recording happened to
end. An installed skill gets a session provisioned from its profile, loads the
app with stored cookies, and lands on the post-login page; replay does the same.
A plan that only works from mid-flow is the exact false pass this gate exists to
prevent.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any


class SessionNotReadyError(RuntimeError):
    """Tabby has no healthy session for this profile — the human must sign in.

    Deliberately its own type so it can pass THROUGH replay_step rather than
    becoming a blocked step. "The session is not signed in" is not a defect in
    the plan, and recording it as one would blame the skill for the operator not
    having signed in yet. The caller shows the sign-in card and replays again.
    """


#: Step outcomes, as rendered to the human.
OK = "ok"
BLOCKED = "blocked"
RECOVERED = "recovered"
NEEDS_APPROVAL = "needs_approval"
SKIPPED = "skipped"

#: Commands that only read. Safe to replay without asking, always.
_READ_ONLY_COMMANDS = frozenset(
    {"get_page_summary", "get_page_info", "screenshot", "wait_for_selector", "list_downloads"}
)

#: Words that mean this control does something to the account, not just shows it.
#: Deliberately broad: a false stop costs one question, a false proceed can move
#: money.
_RISKY_TEXT = (
    "pay",
    "transfer",
    "remit",
    "send money",
    "fund",
    "block",
    "close",
    "cancel",
    "delete",
    "remove",
    "deactivate",
    "confirm",
    "authorise",
    "authorize",
    "submit",
    "update",
    "change",
    "reset",
    "modify",
)


def _text_of(step: dict) -> str:
    params = step.get("params") or {}
    parts = [
        params.get("text") or "",
        params.get("label") or "",
        params.get("selector") or "",
        (step.get("locator") or {}).get("recorded_text") or "",
    ]
    return " ".join(str(p) for p in parts).lower()


def classify_risk(step: dict, *, recorded_controls: set[str]) -> str | None:
    """Why this step must not be replayed unattended, or None if it is safe.

    Three grounds, in the order they catch things:

    1. It moves money or is irreversible — pay, transfer, block, delete, submit.
    2. It touches a control the human never touched. This is the one that
       actually catches the unexpected: the plan only departs from the recording
       when the agent is improvising, and that is exactly when nobody has
       verified what the control does.
    3. It is a form submission. `submit` operations are compiled from a real
       submit the human made, but replaying one on a bank portal repeats
       whatever it did.

    Read-only commands are never risky, whatever their text says — reading a page
    that happens to contain the word "Transfer" is not a transfer.
    """
    command = step.get("command") or ""
    if command in _READ_ONLY_COMMANDS:
        return None

    text = _text_of(step)
    for word in _RISKY_TEXT:
        if re.search(rf"\b{re.escape(word)}", text):
            return f"looks like it {word}s something rather than reading it"

    identity = _control_identity(step)
    if identity and identity not in recorded_controls:
        return "the human never touched this control during the recording"

    return None


def _control_identity(step: dict) -> str:
    """A stable key for "the same control", for comparing against the recording."""
    params = step.get("params") or {}
    for key in ("selector", "label", "text"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return f"{key}:{value.strip().lower()}"
    return ""


def recorded_controls(operations: list[dict]) -> set[str]:
    """Every control the compiled draft addresses — i.e. what the human did.

    The draft is compiled from the recording, so its steps ARE the human's
    actions. A step outside this set can only have come from the agent
    improvising.
    """
    out: set[str] = set()
    for op in operations or []:
        for step in op.get("steps") or []:
            identity = _control_identity(step)
            if identity:
                out.add(identity)
    return out


def plan_operations(operations: list[dict], *, browser_only: bool = True) -> list[dict]:
    """The operations replay should run.

    Browser skills only, by decision: a HAR-replay skill is validated by its
    existing test loop, and replaying one means firing recorded requests, which
    is a different risk profile entirely.
    """
    if not browser_only:
        return list(operations or [])
    return [op for op in operations or [] if (op.get("tool") or "") == "call_web_browser"]


_ABSENT = ("nothing on the page matches", "unknown command", "not visible")


def _satisfied_already(step: dict, detail: str) -> bool:
    """Is this an optional step whose control is simply not there?

    ICICI's statement portal has its own sign-in, INSIDE the workflow. The
    recording had to do it -- click Log In, and the URL gains a jsessionid. A
    replay whose session already satisfies it arrives past that point, so the
    button is absent and the compiled step matched nothing. The step is
    unnecessary, not broken.

    Only for a step the compiler marked optional, and only when the control is
    ABSENT. A login button that is present and fails to click is a real failure
    and must stay one.
    """
    if not step.get("optional"):
        return False
    low = str(detail or "").lower()
    return any(marker in low for marker in _ABSENT)


_RETRY_PAUSE_S = 2.5
"""How long to let a transiently-missing control appear before trying again."""

_INVISIBLE = "on the page but not visible"


def _unactionable(detail: str) -> bool:
    """Could this control not be acted on, whether it is hidden or simply gone?

    ICICI's GO button showed up both ways on consecutive runs -- once present
    but hidden, once absent entirely -- because how far the portal has re-rendered
    when the step fires varies. Both mean the same thing: the step cannot be
    performed here. Whether that is a FAILURE is decided by `arrived_past`, which
    looks at what comes next.
    """
    low = (detail or "").lower()
    return any(marker in low for marker in _ABSENT) or _INVISIBLE in low


def _selector_of(step: Any) -> str:
    if not isinstance(step, dict):
        return ""
    return str((step.get("params") or {}).get("selector") or "")


def arrived_past(step: dict, following: Any, execute: Any) -> bool:
    """Has the page already moved beyond this step, leaving its control behind?

    ICICI's annual statement is chosen with a radio. The human then pressed GO,
    because their selection did not submit the form. The replay's `set_checked`
    DOES submit it, so the page is already on the annual view and that GO button
    is hidden -- present, unclickable, and permanently so: it stayed hidden
    through a full 30s wait. The step is unnecessary, not broken.

    "Invisible" alone cannot mean "skip". The very first step of this same
    replay -- the credit-card link inside a hover menu -- is invisible until its
    menu opens, and blocking there is exactly right. What separates the two is
    what comes NEXT: the menu item's successor is not reachable yet either,
    while the GO button's successor is already on screen. A control we cannot
    see, followed by one we can, means the page went past this step.

    Looks ahead to the next step that addresses a CONTROL, not merely the next
    step. A read operation ends with `get_page_summary`, which names nothing, so
    anchoring on the immediate successor found no evidence and blocked -- on the
    very case this rule was written for.

    Requires that successor to name a CSS selector, because that is the only
    thing that can be probed without acting on the page. Anything else is
    treated as a real failure, which is the safe direction.
    """
    selector = ""
    for candidate in following if isinstance(following, list) else [following]:
        selector = _selector_of(candidate)
        if selector:
            break
    if not selector:
        return False
    try:
        # As patient as the step that follows.
        #
        # A 4s probe judged the page while the portal was still re-rendering:
        # ICICI's GO button was reported BLOCKED, and the very next step -- the
        # one whose control the probe had just failed to find -- succeeded
        # seconds later. Evidence gathered less patiently than the thing it is
        # evidence about is worse than none.
        result = execute(
            "wait_for_selector",
            {"selector": selector, "state": "visible", "timeout_ms": 12000},
        )
    except Exception:  # noqa: BLE001 — cannot prove it, so do not skip
        return False
    return bool(result.get("success", True)) if isinstance(result, dict) else False


def _target_identity(step: dict) -> str:
    """What a step acts on, for recognising a control we have already failed."""
    params = step.get("params") or {}
    for key in ("selector", "text", "role"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return f"{key}={value}"
    return ""


def replay_step(
    execute: Any,
    step: dict,
    *,
    recorded: set[str],
    approvals: set[str] | None = None,
    known_downloads: set[str] | None = None,
    following: Any = None,
    unactionable_targets: set[str] | None = None,
) -> dict:
    """Run one step, timing it, and stamp elapsed_ms on the result.

    Wraps the real work so every one of its exits is measured, whichever branch
    returns -- a fast-fail, an approval gate, a success, a block. `elapsed_ms` is
    what makes "the replay felt slow" answerable: without it, where a run spends
    its time is guesswork, and a step that fails after a full attach-plus-click
    timeout looks identical in the report to one that failed instantly.
    """
    started = time.monotonic()
    result = _replay_step_inner(
        execute,
        step,
        recorded=recorded,
        approvals=approvals,
        known_downloads=known_downloads,
        following=following,
        unactionable_targets=unactionable_targets,
    )
    if isinstance(result, dict):
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


def _replay_step_inner(
    execute: Any,
    step: dict,
    *,
    recorded: set[str],
    approvals: set[str] | None = None,
    known_downloads: set[str] | None = None,
    following: Any = None,
    unactionable_targets: set[str] | None = None,
) -> dict:
    """Run one step and report what happened, without ever raising.

    ``execute(command, params)`` performs the command and returns the worker's
    response; anything it raises becomes a BLOCKED result rather than ending the
    replay. A blocked step is information — it is the thing the human needs to
    see — so it must never abort the run and lose everything after it.

    ``approvals`` holds control identities the human has already approved this
    run, so an approved risky step executes on the next pass instead of asking
    again.
    """
    approvals = approvals or set()
    identity = _control_identity(step)
    result: dict[str, Any] = {
        "command": step.get("command"),
        "params": step.get("params") or {},
        "expect": step.get("expect"),
        "locator": step.get("locator"),
    }

    # A control already proven unactionable on this page is not worth waiting for
    # again.
    #
    # ICICI's statement operations repeat a selector -- #DUMMY1 twice, then
    # #DOWNLOAD_ESTATEMENT_PDF twice -- and when the control is present but
    # hidden, each attempt spends the attach wait AND the click timeout before
    # reporting the same thing it reported a moment ago. That was roughly three
    # minutes of a replay sitting on the statement page, none of it doing
    # anything.
    #
    # Reported as blocked, not skipped: the step DID fail, and the run should say
    # so. It simply says it immediately, on the evidence it already has.
    repeat_target = _target_identity(step)
    if unactionable_targets is not None and repeat_target and repeat_target in unactionable_targets:
        result["status"] = BLOCKED
        result["error"] = (
            f"{repeat_target} was already found on the page but not actionable earlier in "
            "this run, so this repeat was not attempted -- waiting again would cost the "
            "same timeout to learn the same thing."
        )
        return result

    risk = classify_risk(step, recorded_controls=recorded)
    if risk and identity not in approvals:
        result["status"] = NEEDS_APPROVAL
        result["detail"] = risk
        return result

    try:
        response = execute(step.get("command"), step.get("params") or {})
    except SessionNotReadyError:
        # Not a step failure. Let it out so the caller can ask for a sign-in
        # instead of recording the plan as broken.
        raise
    except Exception as exc:  # noqa: BLE001 — a blocked step is data, not a crash
        if _satisfied_already(step, str(exc)):
            result["status"] = SKIPPED
            result["detail"] = (
                f"{step.get('optional_reason') or 'optional step'}: its control is not "
                "on the page, which means this was already satisfied before the "
                "replay arrived."
            )
            return result
        result["status"] = BLOCKED
        result["detail"] = str(exc)
        return result

    if isinstance(response, dict) and response.get("success") is False:
        detail = str(response.get("error") or "the command reported failure")
        if unactionable_targets is not None and _unactionable(detail):
            identity = _target_identity(step)
            if identity:
                unactionable_targets.add(identity)
        if _satisfied_already(step, detail):
            result["status"] = SKIPPED
            result["detail"] = (
                f"{step.get('optional_reason') or 'optional step'}: its control is not "
                "on the page, which means this was already satisfied before the "
                "replay arrived."
            )
            return result
        # One retry, because a control can be transiently unreachable.
        #
        # The first step of the ICICI replay opens a hover menu, and its item is
        # not rendered the instant the pointer lands. That made the run a coin
        # flip: it failed at step 0 about half the time and passed on an
        # immediate retry with nothing changed. Waiting for the page to stop
        # navigating did NOT fix it -- the page was not navigating; the menu was
        # simply not up yet -- so the retry is the fix rather than a symptom of
        # a missing one.
        #
        # Before deciding the page has moved past this step, since a control
        # that appears on the second attempt has not been moved past at all.
        if _unactionable(detail):
            time.sleep(_RETRY_PAUSE_S)
            try:
                retry = execute(step.get("command"), step.get("params") or {})
            except SessionNotReadyError:
                raise
            except Exception:  # noqa: BLE001 — keep the FIRST failure's detail
                retry = None
            if isinstance(retry, dict) and retry.get("success") is not False:
                result["status"] = OK
                result["data"] = retry.get("data")
                result["detail"] = "succeeded on a second attempt"
                unmet = expectation_unmet(
                    step.get("expect"), execute, known_downloads=known_downloads
                )
                if unmet:
                    result["status"] = BLOCKED
                    result["detail"] = unmet
                return result

        if _unactionable(detail) and arrived_past(step, following, execute):
            result["status"] = SKIPPED
            result["detail"] = (
                "its control is on the page but not visible, and the control this "
                "operation needs NEXT already is — so the page has moved past this "
                "step and performing it is neither possible nor needed."
            )
            return result
        result["status"] = BLOCKED
        result["detail"] = detail
        return result

    result["status"] = OK
    result["data"] = response.get("data") if isinstance(response, dict) else None

    # Check the postcondition the recorder observed.
    #
    # Until now `expect` was carried into the result and never evaluated, so a
    # replay could execute every step, end up on a completely different page,
    # and report success -- "no step was blocked" was the whole test. That is
    # how a run clicked its way onto /discover and still called itself passing.
    # The compiler already states what the recording observed after each click;
    # this is the enforcer that was missing.
    unmet = expectation_unmet(step.get("expect"), execute, known_downloads=known_downloads)
    if unmet:
        result["status"] = BLOCKED
        result["detail"] = unmet
    return result


def _is_new_download(record: Any, known: Any) -> bool:
    """A file that arrived HERE, and actually finished arriving.

    `list_downloads` reports every download the browser context has taken, for
    the life of the session -- so once any operation downloads anything, every
    later download check passes on that same file. The annual statement replay
    "succeeded" on the monthly PDF fetched two operations earlier.

    A record also counts as a download while it is still in flight and after it
    has failed. Neither is evidence that the step did what it was recorded
    doing.
    """
    if not isinstance(record, dict):
        return False
    # Only an explicit verdict counts against it. A record with no state at all
    # is not evidence of failure, and refusing those would fail every download
    # against a worker that does not report one.
    if record.get("state") in ("in_progress", "failed"):
        return False
    rid = record.get("id")
    # Untrackable, so it cannot be shown to be old. Real records always carry an
    # id; treating its absence as "already seen" would reject every download.
    return rid is None or str(rid) not in known


def expectation_unmet(expect: Any, execute: Any, *, known_downloads: set[str] | None = None) -> str:
    """Why the recorded postcondition does not hold, or "" if it does.

    Deliberately forgiving about HOW a page is reached and strict about WHERE it
    ends up: a URL matches if the recorded one is a prefix of it, ignoring the
    query AND any path parameter -- both are where session tokens churn between
    the recording and the replay -- so a portal that appends its own still
    passes.
    """
    if not isinstance(expect, dict) or not expect:
        return ""

    want_url = str(expect.get("url") or "")
    if want_url:
        try:
            info = execute("get_page_info", {}) or {}
            here = str((info.get("data") or info).get("url") or "")
        except Exception:  # noqa: BLE001 — an unreadable url is not a failed expectation
            here = ""
        if here:
            # Path parameters go too, not just the query. ICICI carries its
            # portal session as `;jsessionid=...` in the PATH, so a recorded URL
            # and a live one name the same page with different tokens -- and
            # neither is a prefix of the other. Left in, the download's own
            # page assertion would fail every replay for the wrong reason.
            bare = lambda u: (  # noqa: E731
                u.split("?")[0].split("#")[0].split(";")[0].rstrip("/")
            )
            if not bare(here).startswith(bare(want_url)) and not bare(want_url).startswith(
                bare(here)
            ):
                return f"expected to be on {want_url} after this step, but the page is {here}"

    if expect.get("download"):
        try:
            listed = execute("list_downloads", {}) or {}
            files = ((listed.get("data") or listed) or {}).get("downloads") or []
        except Exception:  # noqa: BLE001
            files = []
        known = known_downloads if known_downloads is not None else set()
        fresh = [f for f in files if _is_new_download(f, known)]
        # Everything on disk is accounted for from here on, whether or not it
        # satisfied THIS step.
        if known_downloads is not None:
            known_downloads.update(str(f.get("id")) for f in files if f.get("id") is not None)
        if not fresh:
            if files:
                return (
                    "this step downloaded a file when it was recorded; no NEW completed "
                    f"file arrived (the {len(files)} already here came from earlier steps)"
                )
            return "this step downloaded a file when it was recorded; no file arrived"

    return ""


def downloads_listed(steps: list[dict]) -> list[dict]:
    """Every download record this operation's own `list_downloads` reported."""
    for s in steps:
        if s.get("command") == "list_downloads":
            listed = (s.get("data") or {}).get("downloads")
            return [r for r in (listed or []) if isinstance(r, dict)]
    return []


def goal_reached(
    operation: dict, steps: list[dict], *, already_downloaded: Any = frozenset()
) -> bool:
    """Did this operation actually achieve what it exists for?

    Not "did every step pass" — an operation whose steps all ran but which never
    produced its file has not reached its goal, and that distinction is the whole
    reason `kind` is recorded. For a download the evidence is a file; for
    everything else it is that no step was left blocked.

    The file has to be THIS operation's. `list_downloads` reports the whole
    session's downloads, so the annual statement operation was reporting success
    on the monthly PDF a previous operation had fetched — the exact failure the
    replay exists to catch, passing the replay. ``already_downloaded`` carries
    what the operations before it had already produced.
    """
    # An operation that ran NOTHING reached no goal.
    #
    # Seen live: a replay that executed zero steps reported goal_reached true
    # and all_goals_reached true, because "no step was blocked" is trivially
    # satisfied by an empty list. That is the most misleading answer this field
    # can give -- a report nobody would question.
    if not steps:
        return False

    # An unanswered approval is a hard stop whatever else happened: the human
    # has not agreed to the thing being asked about.
    if any(s.get("status") == NEEDS_APPROVAL for s in steps):
        return False

    if (operation.get("kind") or "") == "download":
        # The file is the evidence, and it is stronger than the step list.
        #
        # The annual replay produced the right statement -- "Statement Period
        # 01/04/2025 TO 31/03/2026" -- through `#PDF_Download`, while a later
        # click on a control the page had already moved past was recorded as
        # blocked. Reporting that run as goal-not-reached describes the plan, not
        # the outcome, and the outcome is what this field is for. The blocked
        # steps stay in the report, and `installable` still waits for a human.
        listed = downloads_listed(steps)
        if not listed:
            return False
        return any(_is_new_download(r, already_downloaded) for r in listed)

    # Everything else has only its steps to go on.
    return not any(s.get("status") == BLOCKED for s in steps)


def operations_fingerprint(operations: list[dict]) -> str:
    """A stable digest of what the draft actually DOES.

    An approval is only meaningful for the plan that was replayed. Amend a step
    and recompile and this changes, so the stale approval no longer matches and
    the installer refuses — which is what makes "user confirmation takes
    precedence" enforceable rather than a convention.

    Keyed on the operations' names, kinds and steps. Descriptions are excluded
    deliberately: renaming an operation or rewording its description changes
    nothing about its behaviour, and forcing a fresh replay for a typo fix would
    make the gate something to work around.
    """
    material = [
        {
            "name": op.get("name"),
            "kind": op.get("kind") or "read",
            "steps": op.get("steps") or [],
            "parameters": [
                {"name": p.get("name"), "default": p.get("default")}
                for p in op.get("parameters") or []
            ],
        }
        for op in operations or []
    ]
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def build_report(
    operations: list[dict],
    results: list[list[dict]],
    *,
    already_downloaded: Any = None,
) -> dict:
    """The whole replay, in the shape the confirmation card renders.

    ``results`` is one list of step-results PER operation, positionally matched
    to ``operations``.

    Reports per operation AND overall, because "11 of 12 steps passed" is the
    kind of summary that hides exactly the failure that matters. What the human
    needs to decide on is whether each GOAL was reached.
    """
    ops_out = []
    # What was already on disk before this operation ran -- both what earlier
    # operations produced AND what was there before the replay started.
    #
    # Seeded only from earlier operations, a file left by a PREVIOUS replay
    # counted as this one's evidence: a run that downloaded nothing reported the
    # goal reached, on the strength of a statement fetched minutes earlier. The
    # session accumulates downloads for its whole life, so the baseline has to
    # be taken when the run starts.
    already: set[str] = set(already_downloaded or ())
    for op, steps in zip(operations, results, strict=False):
        reached = goal_reached(op, steps, already_downloaded=already)
        already.update(str(r["id"]) for r in downloads_listed(steps) if r.get("id") is not None)
        ops_out.append(
            {
                "name": op.get("name"),
                "kind": op.get("kind") or "read",
                "description": op.get("description"),
                "goal_reached": reached,
                "steps": steps,
                "blocked_count": sum(1 for s in steps if s.get("status") == BLOCKED),
                "needs_approval_count": sum(1 for s in steps if s.get("status") == NEEDS_APPROVAL),
            }
        )
    return {
        "operations": ops_out,
        "all_goals_reached": bool(ops_out) and all(o["goal_reached"] for o in ops_out),
        "needs_approval": any(o["needs_approval_count"] for o in ops_out),
        # Ties the approval to the exact plan that was replayed.
        "fingerprint": operations_fingerprint(operations),
        # The gate itself. Nothing downstream should install on anything else.
        "installable": False,
    }


def approve(report: dict) -> dict:
    """Mark a replayed draft as approved by the human.

    Separate from build_report on purpose: `installable` is never something the
    replay concludes on its own, however well it went. It is set only here, by an
    explicit human decision, so no code path can install a skill nobody looked at.
    """
    approved = dict(report)
    approved["installable"] = True
    return approved


def substitute_parameters(operation: dict, values: dict[str, str] | None = None) -> list[dict]:
    """The operation's steps with ``{{placeholders}}`` filled in.

    Replay runs the recorded defaults unless told otherwise, because that is the
    run we have evidence for. Typing a literal "{{from_date}}" into a bank's date
    field would fail for a reason that has nothing to do with the plan.
    """
    values = dict(values or {})
    for param in operation.get("parameters") or []:
        values.setdefault(param["name"], param.get("default", ""))

    def fill(text: str) -> str:
        for name, value in values.items():
            text = text.replace("{{" + name + "}}", str(value))
        return text

    out: list[dict] = []
    for step in operation.get("steps") or []:
        if not step_applies(step, values):
            continue
        params = {
            k: (fill(v) if isinstance(v, str) else v) for k, v in (step.get("params") or {}).items()
        }
        # Carry the recorded settle to the worker. It decides how long a control
        # that exists but is not yet visible may take -- a hover-revealed menu
        # measured at 4.3s was failing against a flat 3s grace. The measurement
        # is in the recording; the worker cannot see the expect block.
        settle = (step.get("expect") or {}).get("settle_ms")
        if isinstance(settle, int) and settle > 0:
            params.setdefault("settle_ms", settle)
        out.append({**step, "params": params})
    return out


def step_applies(step: dict, values: dict) -> bool:
    """Does this step run for these parameter values?

    Two operations recorded from one page often differ by WHICH STEPS RUN rather
    than by a value: ICICI's monthly and annual statements share four steps to
    reach the page, then diverge -- annual clicks a radio and picks a financial
    year, monthly does not. Substitution cannot express that, so the compiler had
    to emit both as separate operations, each replaying the whole journey from
    the landing page.

    ``when`` is how a step says which variant it belongs to::

        {"command": "click_by_text", "params": {...}, "when": {"timeframe": "annual"}}

    A step with no ``when`` always runs -- every operation compiled before this
    existed keeps behaving exactly as it did. All named keys must match, so a
    step can belong to a combination of choices, and an unknown parameter never
    silently matches: a ``when`` naming something the operation does not declare
    means the step is skipped rather than run by accident.
    """
    cond = step.get("when")
    if not isinstance(cond, dict) or not cond:
        return True
    for name, wanted in cond.items():
        if name not in values:
            return False
        if str(values.get(name)) != str(wanted):
            return False
    return True
