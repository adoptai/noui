"""Running a draft replay against a FRESH Tabby session for the profile.

Replay deliberately uses the ordinary profile session — the same one an
installed skill gets — rather than trying to inherit the recording's auth.

Inheriting it was the obvious idea and it does not survive contact with real
apps. A bank's session is not always in a cookie: ICICI's statement portal
carries it in the URL (``AuthenticationController;jsessionid=…``), and SPAs
routinely keep their access token in ``sessionStorage``. Seeding cookies into a
cold browser reproduces none of that, so replay would fail for reasons that have
nothing to do with the plan being tested.

Using a real profile session costs one extra sign-in and buys three things:

  - it is exactly how the skill will run once installed, so a pass means what it
    looks like it means;
  - it validates the PROFILE too — if its login or keepalive cannot sustain a
    session on this app, the skill fails in production however good its steps
    are, and that is worth learning now;
  - keepalive holds it, so the amend-and-replay loop costs nothing after the
    first sign-in.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from noui_core import tabby_client
from noui_core.capture.split import _is_login_flow_url
from noui_core.verify.replay import (
    SessionNotReadyError,
    build_report,
    plan_operations,
    recorded_controls,
    replay_step,
    substitute_parameters,
)

#: Tabby answers /execute/browser with 404/409 when there is no active profile or
#: no healthy session. noui's HTTP client folds the status into the message.
_NO_SESSION = re.compile(r"HTTP (404|409) from POST /execute/browser")


def _executor(profile_slug: str, token: str, *, timeout_ms: int = 30000) -> Any:
    def execute(command: str, params: dict) -> dict:
        try:
            return tabby_client.execute_browser(
                profile_slug, command, params, token=token, timeout_ms=timeout_ms
            )
        except RuntimeError as exc:
            if _NO_SESSION.search(str(exc)):
                raise SessionNotReadyError(str(exc)) from exc
            raise

    return execute


def session_is_ready(profile_slug: str, token: str) -> bool:
    """Is there a signed-in session to replay against?

    Probed with get_page_info, which reads nothing and changes nothing — asking
    the question must not itself be an action against the app.
    """
    try:
        _executor(profile_slug, token)("get_page_info", {})
    except SessionNotReadyError:
        return False
    except RuntimeError:
        # Reachable but unhappy (a transient worker error). Let the replay run
        # and report per step rather than refusing to start.
        return True
    return True


_MAX_BACK_STEPS = 8
"""How far to walk history home. Bounded: a replay that cannot find the entry
page in eight steps is lost, and pressing back forever would eventually leave
the app entirely."""


_STARTS_FROM_WAIT_S = 8.0
"""How long to let a hop land before deciding an operation is on the wrong page.

A click that causes a cross-origin navigation returns ok before the navigation
completes -- "download previous statement" reported success with the browser
still on /credit-card, and the statement portal appeared a moment later. The
first version of this guard sampled once and blocked the next operation on a
page that was in the middle of becoming the right one.
"""

_STARTS_FROM_POLL_S = 2.0
"""Seconds between checks while waiting for a hop to land. Four samples over the
eight-second window, not sixteen: this is watching for a navigation, not racing
it."""

_RESET_SETTLE_MS = 3000
"""How long to let the entry page paint after a reset MOVED it.

A history restore does not render instantly. The reset would land on /overview,
return success, and the very next step would ask whether the sidebar control was
visible -- on a page still coming up. It resolved and was not yet visible, so
every operation after the first failed at step 0 while the reset itself had
worked perfectly.

Only after an actual move: a reset that found the browser already at the entry
changed nothing, so there is nothing to wait for.
"""


_ARRIVAL_BUDGET_S = 20.0
"""Longest we will wait for an arriving page to produce its first control.

From the recording: the human's own gap between the click that caused a hop and
their next interaction was 17-29s on ICICI. That is an UPPER BOUND on when the
page became usable -- they could not have clicked before the control existed,
but they also spent some of it reading. So it is a ceiling, not a target: we
proceed the instant the control appears and only give up when this elapses.
"""


def _await_first_control(execute: Any, steps: list[dict], budget_s: float) -> None:
    """Wait for the arriving operation's first target, not for a fixed time.

    A fixed sleep was wrong twice: 3s was too short for ICICI's statement portal
    (the control was there when probed by hand a minute later) and long enough
    to be dead weight on every fast page. Polling for the thing we are about to
    click is both faster and more tolerant.

    Only when that first step names a selector. There is no wait-by-text, and
    guessing one would wait for the wrong element.
    """
    first = steps[0] if steps else None
    selector = ((first or {}).get("params") or {}).get("selector")
    if not selector:
        time.sleep(min(_RESET_SETTLE_MS / 1000.0, budget_s))
        return

    deadline = time.monotonic() + budget_s
    while True:
        try:
            execute(
                "wait_for_selector",
                {"selector": selector, "state": "visible", "timeout_ms": 1500},
            )
            return
        except SessionNotReadyError:
            raise
        except Exception:  # noqa: BLE001 — not there yet is the normal case
            if time.monotonic() >= deadline:
                return  # let the step itself report what it finds
            time.sleep(1.0)


def _settle_after_reset(operations: list[dict]) -> float:
    """Seconds to wait, preferring what the recording measured.

    The first step of an operation carries the settle its own page needed when
    recorded (4.3s on the ICICI nav). That is a real measurement of this app on
    this connection, which beats a constant -- the constant is only the floor.
    """
    observed = 0
    for op in operations or []:
        for step in op.get("steps") or []:
            if isinstance(step, dict):
                got = (step.get("expect") or {}).get("settle_ms")
                if isinstance(got, int):
                    observed = max(observed, got)
                break
    return min(max(observed, _RESET_SETTLE_MS), 10_000) / 1000.0


def _origin(url: str) -> str:
    m = re.match(r"^[a-z]+://[^/]+", url or "", re.I)
    return (m.group(0) if m else "").lower()


def _left_the_app(here: str, entry_url: str, known: set[str]) -> bool:
    """Has walking back taken us somewhere the journey never was?

    History does not stop at the landing page. On ICICI the stack below
    /overview is [about:blank, /login-page, /overview, ...], so one press too
    many lands on the SIGN-IN PAGE of a session that is still perfectly valid --
    which looks exactly like being logged out, and was very likely read as that.
    Pressing on from there reaches about:blank and abandons the app entirely.
    """
    if not here or here == "about:blank":
        return True
    # Known pages FIRST. The statement portal is part of the journey, on another
    # host, and served by a controller named AuthenticationController -- so the
    # login-word test below calls it a sign-in page and would stop the walk at
    # the very page it needs to walk back from. Same precedence trap as the
    # sign-in detector.
    bare = here.split("?")[0].split(";")[0].rstrip("/")
    if any(bare.startswith(k) for k in known):
        return False
    if _is_login_flow_url(here):
        return True
    return _origin(here) != _origin(entry_url)


def _page_identity(url: str) -> str:
    """A URL reduced to the page it names.

    Drops the query string and any path parameter. ICICI's statement portal
    carries its session as `;jsessionid=...`, so the page the browser is on and
    the page the recording named are the same page in different clothes -- and a
    comparison that stripped only `?` decided they were different, blocking the
    one operation that was being tested.
    """
    return url.split("?")[0].split(";")[0].rstrip("/")


def _same_page(a: str, b: str) -> bool:
    """Same page, ignoring a trailing slash, a query string, or a jsessionid."""
    return _page_identity(a) == _page_identity(b)


def _entry_path(entry_url: str) -> str:
    """The path part of the entry, which is what a same-app link carries."""
    path = re.sub(r"^[a-z]+://[^/]+", "", entry_url, flags=re.I)
    return (path.split("?")[0] or "/").rstrip("/")


def _home_control(controls: dict, entry_url: str) -> dict | None:
    """The app's own way back to the landing page, by where it POINTS.

    Not by what it says: the control is often a logo with no text at all, and
    matching words would need a list per language and per bank. A link whose
    href is the entry path is the same link a person clicks, on any app.
    """
    want = _entry_path(entry_url)
    if not want or want == "":
        return None
    for group in ("links", "buttons"):
        for c in controls.get(group) or []:
            if not isinstance(c, dict):
                continue
            href = str(c.get("href") or "")
            sel = str(c.get("selector") or "")
            target = href or sel
            if want and (target.split("?")[0].rstrip("/").endswith(want)):
                if sel:
                    return {"command": "click_element", "params": {"selector": sel}}
                text = str(c.get("text") or "").strip()
                if text:
                    return {"command": "click_by_text", "params": {"text": text}}
    return None


def _workflow_urls(ops: list[dict], entry_url: str) -> set[str]:
    """Pages the RECORDING visited, from the plan's own arrival expectations.

    A word list cannot tell a sign-in page from an app page: ICICI's statement
    portal is served by a controller literally named AuthenticationController,
    so "auth" in the path flagged a signed-in session as signed out and refused
    to replay against it. What the recording actually visited is not a guess.
    """
    # Every URL the plan mentions, not only arrival expectations: the deeper
    # hops of an SPA are often not recorded as navigations at all, so ICICI's
    # statement portal appeared in no expect.url and the page the replay had
    # just legitimately reached was still read as a sign-in screen. The
    # operation descriptions name it ("Read the rendered contents of ..."), and
    # anything the compiler wrote into the plan came from the recording.
    urls = {entry_url} if entry_url else set()
    urls.update(re.findall(r"https?://[^\s\"'\\]+", json.dumps(ops or [])))
    return {u.split("?")[0].split(";")[0].rstrip("/") for u in urls if u}


def _cannot_reset(entry_url: str, here: str, why: str) -> dict:
    return {
        "command": "return_to_entry",
        "params": {"url": entry_url},
        "status": "blocked",
        "error": (
            f"could not return to the start of the journey ({entry_url}): {why}. "
            f"The browser is on {here or 'an unknown page'}, which is not where these "
            "steps were recorded, so replaying them from here proves nothing. Sign in "
            "again for a session that starts at the landing page."
        ),
    }


def _recorded_way_back(operations: list[dict], entry_url: str) -> dict | None:
    """A control the RECORDING watched land on this entry page.

    The preferred way to reposition on a browser-driven app is a click, because
    navigate is banned there -- a full load destroys the session. But WHICH
    click matters: hunting the live page for something that looks like a way
    home is a guess, while a step whose recorded outcome arrived at this exact
    page is evidence.

    Often there is none. A journey that went forward and never returned never
    watched anyone go back, and then the caller falls through to the guess --
    which is fine, as long as it is not mistaken for something observed.
    """
    want = entry_url.split("?")[0].split(";")[0].rstrip("/")
    if not want:
        return None
    for op in operations or []:
        for step in op.get("steps") or []:
            if not isinstance(step, dict):
                continue
            got = str(((step.get("expect") or {}).get("url")) or "")
            if not got:
                continue
            if got.split("?")[0].split(";")[0].rstrip("/") == want:
                return {"command": step.get("command"), "params": dict(step.get("params") or {})}
    return None


def _entry_for_origin(here: str, entry_url: str, by_origin: dict | None) -> str:
    """The entry page on the host we are ALREADY on.

    Resetting across hosts means inventing a route the recording never took: the
    journey went forward only, so nothing was ever observed going back from the
    statement portal to the net-banking landing page. Whatever host the browser
    is on, the reset aims at the first page the recording reached there.
    """
    if not by_origin:
        return entry_url
    m = re.match(r"^[a-z]+://[^/]+", here or "", re.I)
    origin = (m.group(0) if m else "").lower()
    return str(by_origin.get(origin) or entry_url)


def _return_to_entry(
    execute: Any,
    entry_url: str,
    known_urls: set[str] | None = None,
    entry_by_origin: dict | None = None,
    operations: list[dict] | None = None,
    notes: list | None = None,
) -> dict | None:
    """Put the browser back at the start of the journey before replaying it.

    A replay does not get a fresh browser -- the session is deliberately kept
    warm so the amend-and-replay loop costs no sign-ins (see the module
    docstring). The cost of that is a browser sitting wherever the LAST run
    abandoned it, and these operations are one recorded journey cut into pieces:
    every one of them assumes the page its predecessor left behind, and the first
    assumes the landing page.

    Observed: a replay died mid-journey on ICICI, an amendment fixed the step it
    died on, and the re-run began on `/credit-card/add-card` -- where the
    already-passing early steps no longer matched anything, so the amendment
    looked no better than what it replaced. Re-running has to mean re-running,
    not resuming.

    Returns a step-shaped dict when the reset itself failed, so the caller can
    report it as what stopped the replay rather than blaming the first step.
    """
    try:
        info = execute("get_page_info", {})
        here = str((info.get("data") or info).get("url") or "")
    except SessionNotReadyError:
        raise
    except Exception:  # noqa: BLE001 — an unreadable url just means "reset anyway"
        here = ""

    entry_url = _entry_for_origin(here, entry_url, entry_by_origin)

    if here and _same_page(here, entry_url):
        return None  # already at the start; moving would only cost a load

    # Sitting on the sign-in flow is not a broken skill and not a page we can
    # find our way off: a login screen has no link to the landing page, so the
    # reset would report "no way back" and the member would read a plan failure
    # where the real answer is "sign in". SessionNotReadyError is what run_replay
    # turns into login_required, which is what puts a sign-in card in front of
    # them.
    known = known_urls or set()
    on_known_page = any(
        here.split("?")[0].split(";")[0].rstrip("/").startswith(k) for k in known
    )
    if here and not on_known_page and _is_login_flow_url(here):
        raise SessionNotReadyError(
            f"the browser is on the sign-in flow ({here}), so there is no signed-in "
            "session to replay against"
        )

    # A recorded control first: on a browser-driven app the way to reposition is
    # a click, and a click the recording watched arrive HERE is evidence rather
    # than a guess. Usually absent -- a journey that never went back never
    # watched anyone go back -- in which case we fall through.
    back = _recorded_way_back(operations or [], entry_url)
    if back and back.get("command"):
        try:
            execute(back["command"], back.get("params") or {})
            info = execute("get_page_info", {})
            landed = str((info.get("data") or info).get("url") or "")
            if _same_page(landed, entry_url):
                time.sleep(_settle_after_reset(operations or []))
                return None
            here = landed or here
        except SessionNotReadyError:
            raise
        except Exception:  # noqa: BLE001 — recorded once is not guaranteed now
            pass

    # NO history walk here, deliberately.
    #
    # go_back looked cheap and harmless and is neither. The SPA's back stack on
    # ICICI is [about:blank, /login-page, /overview, ...], so a press from a
    # shallow point lands the LIVE session on the sign-in page -- which is
    # exactly "your session has expired" from the member's side. Watched
    # happening: the session died the moment a replay started, every time, and
    # never while it sat idle. The overshoot guard stops the walk continuing;
    # it cannot undo the press that already landed.
    #
    # It also never once got home during a real replay. The only time it worked
    # was an isolated trace where the previous history entry happened to be the
    # entry page. A mechanism that cannot be relied on to help and can destroy
    # the session being tested does not belong in front of the ones that can.
    #
    # (The worker still exposes go_back; it is a legitimate primitive for a
    # caller that knows where it is. Nothing here guesses with it.)

    # navigate NEXT, because on an app that allows it this is one call and
    # lands exactly where we mean. It is not always allowed: a portal that
    # carries its session in the URL loses it on a full page load, and the
    # worker refuses the command outright -- which is how the first live run of
    # this reset failed, using the one command the app forbids.
    try:
        execute("navigate", {"url": entry_url})
        time.sleep(_settle_after_reset(operations or []))
        return None
    except SessionNotReadyError:
        raise
    except Exception as exc:  # noqa: BLE001 — may be the refusal, may be real
        if "navigate is disabled" not in str(exc):
            return _cannot_reset(entry_url, here, str(exc))

    # Move the way a person does: the app's own link back to the landing page.
    # Matched on where the link POINTS, not what it says, so this works on a
    # logo with no text and in any language.
    try:
        summary = execute("get_page_summary", {})
        controls = (summary.get("data") or summary) or {}
    except SessionNotReadyError:
        raise
    except Exception as exc:  # noqa: BLE001
        return _cannot_reset(entry_url, here, f"could not read the page: {exc}")

    home = _home_control(controls, entry_url)
    if home is None:
        return _cannot_reset(
            entry_url,
            here,
            "this app does not allow navigate, and no link back to the start was "
            "found on the page",
        )

    # Nobody watched a human click this. It is the live page's own link back,
    # matched on where it points -- a reasonable guess and still a guess, so it
    # is recorded as one rather than passing as observed behaviour.
    if notes is not None:
        notes.append(
            {
                "kind": "unobserved_reset",
                "entry_url": entry_url,
                "control": dict(home.get("params") or {}),
                "why": "no recorded control was seen arriving at this page, so the "
                "way back was taken from the live page",
            }
        )
    try:
        execute(home["command"], home["params"])
        info = execute("get_page_info", {})
        landed = str((info.get("data") or info).get("url") or "")
    except SessionNotReadyError:
        raise
    except Exception as exc:  # noqa: BLE001
        return _cannot_reset(entry_url, here, f"clicking the way back failed: {exc}")

    if not _same_page(landed, entry_url):
        return _cannot_reset(
            entry_url, landed, "clicking the way back did not land on the start"
        )
    time.sleep(_settle_after_reset(operations or []))
    return None


def run_replay(
    operations: list[dict],
    *,
    profile_slug: str,
    token: str,
    approvals: set[str] | None = None,
    parameter_values: dict[str, str] | None = None,
    timeout_ms: int = 30000,
    entry_url: str | None = None,
    entry_by_origin: dict | None = None,
) -> dict:
    """Replay a draft and report what happened.

    Returns the report from ``build_report``, or ``{"status": "login_required"}``
    when there is no session to replay against — the caller shows the sign-in
    card and calls again. Partial results are kept if the session dies mid-run:
    the steps that already ran are still evidence, and discarding them would make
    a timed-out session look like a plan that does nothing.
    """
    ops = plan_operations(operations)
    if not ops:
        return {
            "operations": [],
            "all_goals_reached": False,
            "needs_approval": False,
            "installable": False,
            "detail": "no browser operations to replay",
        }

    recorded = recorded_controls(ops)
    execute = _executor(profile_slug, token, timeout_ms=timeout_ms)

    known = _workflow_urls(ops, entry_url or "")
    reset_notes: list = []
    results: list[list[dict]] = []
    # Segments: run the recorded journey ONCE, in order, each operation
    # continuing where the last one ended.
    #
    # `steps` repeats the whole chain from the entry page into every operation,
    # so replaying operation 2 demands being back somewhere the recording left
    # once and never returned to -- and the reset had to invent a route there.
    # Watched failing: operations 2-5 reported "could not return to the start of
    # the journey" while the browser sat on /credit-card with the next recorded
    # click visible on screen.
    #
    # A skill compiled before segments existed has none, and runs exactly as it
    # did.
    # No count condition. `len(ops) > 1` was here to leave single-operation
    # skills on the old path, and it silently undid segments the moment --only
    # narrowed a run to one: download_statement fell back to the legacy branch,
    # which resets before every operation, and was sent to /overview from the
    # portal it had just reached. Carrying segments is the property that
    # matters, not how many operations happen to be running.
    segmented = bool(ops) and all(op.get("segment_steps") is not None for op in ops)

    for index, op in enumerate(ops):
        source = op if not segmented else {**op, "steps": op.get("segment_steps") or []}
        steps = substitute_parameters(source, parameter_values)
        step_results: list[dict] = []

        if segmented:
            # Only the first operation may reset: a fresh session lands on the
            # entry page, which is the one position the recording observed. The
            # rest continue from their predecessor, so a reset would undo it.
            starts_from = op.get("starts_from")
            # "Begins the journey" is a property of the operation, not its
            # position in the list. An operation with no starts_from is the one
            # a fresh session lands on; one WITH it continues from that page.
            #
            # Using the index broke as soon as --only ran a single operation:
            # download_statement became index 0 and was sent back to /overview
            # from the statement portal, where it had correctly just arrived.
            if not starts_from:
                if entry_url:
                    try:
                        # The JOURNEY's entry, not this host's.
                        #
                        # Per-origin is right for an operation that starts
                        # mid-journey -- never invent a cross-host route the
                        # recording did not take. It is wrong for the first one:
                        # a replay that ended on the statement portal leaves the
                        # browser there, "home" then resolves to the portal's own
                        # entry, the reset does nothing, and operation 0 runs its
                        # net-banking steps against the wrong host. That is what
                        # made runs alternate pass/fail with no change between:
                        # a passing run ends on the portal, so the next one
                        # starts there and cannot get back.
                        #
                        # Operation 0 is where the journey begins, and a fresh
                        # session lands there. Aim at it.
                        failed = _return_to_entry(
                            execute, entry_url, known, None, ops, reset_notes
                        )
                    except SessionNotReadyError as exc:
                        report = build_report([], [])
                        report["status"] = "login_required"
                        report["detail"] = str(exc)
                        return report
                    if failed is not None:
                        results.append([failed])
                        continue
            elif starts_from:
                # The predecessor was supposed to leave us here. When it did not,
                # say so instead of running steps against the wrong page -- an
                # operation that reads whatever loaded is how a skill claims to
                # have fetched a statement it never opened.
                # WAIT for it, do not sample it once.
                #
                # A click that causes a cross-origin hop returns ok before the
                # hop lands: "download previous statement" reported success with
                # the browser still on /credit-card, and the portal appeared a
                # moment later. Checking instantly blocked the next operation on
                # a page that was in the middle of becoming the right one.
                deadline = time.monotonic() + _STARTS_FROM_WAIT_S
                try:
                    while True:
                        info = execute("get_page_info", {})
                        here = str((info.get("data") or info).get("url") or "")
                        if not here or _same_page(
                            here, starts_from
                        ):
                            break
                        if time.monotonic() >= deadline:
                            break
                        # Gently. Polling twice a second across several
                        # operations tripped the API's rate limit (HTTP 429) and
                        # every later step failed on that instead of on
                        # anything real -- a check for a page turning into
                        # another page must not cost more than the wait itself.
                        time.sleep(_STARTS_FROM_POLL_S)
                except SessionNotReadyError as exc:
                    report = build_report(ops[: len(results)], results)
                    report["status"] = "login_required"
                    report["detail"] = str(exc)
                    return report
                except Exception:  # noqa: BLE001 — unreadable url: let the steps report
                    here = ""
                if here and not _same_page(here, starts_from):
                    results.append([{
                        "command": "starts_from",
                        "params": {"url": starts_from},
                        "status": "blocked",
                        "error": (
                            f"this operation continues from {starts_from}, but the "
                            f"browser is on {here}. The operation before it did not "
                            "arrive, so running these steps here would act on the "
                            "wrong page and report whatever it found."
                        ),
                    }])
                    continue
                # Arrived -- now let it PAINT.
                #
                # Measured from the step that CAUSED the arrival -- the
                # previous operation's last one. That is where the recorder
                # stamped how long this page took to come up; the arriving
                # operation's own first step describes what it does once here.
                #
                # The URL matches the moment the navigation resolves, while the
                # DOM is still coming up. ICICI's statement portal is reached by
                # a cross-origin hop that completes after the click returns, and
                # the next operation's first step asked for #PDF_Download while
                # the form was still building. Probed by hand a minute later,
                # that control was there. The reset already learned this; an
                # arrival needs the same courtesy.
                # The budget is the human's own gap after the click that caused
                # this arrival, buffered and capped at compile time. It sits on
                # the operation that CAUSED the arrival, which is the one the
                # gap was measured from. Falls back to the constant for skills
                # compiled before that was recorded.
                measured = (ops[index - 1] or {}).get("causes_arrival_budget_ms")
                budget = (
                    float(measured) / 1000.0
                    if isinstance(measured, int) and measured > 0
                    else _ARRIVAL_BUDGET_S
                )
                _await_first_control(execute, steps, budget)
            try:
                for step in steps:
                    step_results.append(
                        replay_step(execute, step, recorded=recorded, approvals=approvals)
                    )
            except SessionNotReadyError as exc:
                results.append(step_results)
                report = build_report(ops[: len(results)], results)
                report["status"] = "login_required"
                report["detail"] = str(exc)
                return report
            results.append(step_results)
            continue

        # Before EVERY operation, not once per replay.
        #
        # Each operation carries the whole chain from the entry page -- they are
        # one recorded journey cut into pieces, and every piece starts by walking
        # the nav again. So an operation leaves the browser wherever it ended,
        # and the next one opens by hovering a menu that only exists on the
        # landing page. Observed: two ICICI operations passed, the second ended
        # on the statement page, and the third timed out hovering a nav that was
        # no longer on screen.
        #
        # Cheap when it is already there: _return_to_entry reads the URL first
        # and does nothing when it matches.
        if entry_url:
            try:
                failed = _return_to_entry(
                    execute, entry_url, known, entry_by_origin, ops, reset_notes
                )
            except SessionNotReadyError as exc:
                results.append(step_results)
                report = build_report(ops[: len(results)], results)
                report["status"] = "login_required"
                report["detail"] = str(exc)
                return report
            if failed is not None:
                results.append([failed])
                continue
        try:
            for step in steps:
                step_results.append(
                    replay_step(execute, step, recorded=recorded, approvals=approvals)
                )
        except SessionNotReadyError as exc:
            results.append(step_results)
            report = build_report(ops[: len(results)], results)
            report["status"] = "login_required"
            report["detail"] = str(exc)
            return report
        results.append(step_results)

    report = build_report(ops, results)
    if reset_notes:
        # Surfaced, not buried: a member reviewing this should see which resets
        # were taken from the live page rather than from the recording.
        report["unobserved_resets"] = reset_notes
    return report
