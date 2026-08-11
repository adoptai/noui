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


def _same_page(a: str, b: str) -> bool:
    """Same page, ignoring a trailing slash."""
    return a.rstrip("/") == b.rstrip("/")


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


def _return_to_entry(
    execute: Any, entry_url: str, known_urls: set[str] | None = None
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

    # History FIRST: same tab, same cookies, and for an in-app SPA hop a
    # client-side pop rather than a load. A recorded journey can cross origins
    # -- ICICI's statement portal is a different host from the net-banking SPA
    # -- and from there nothing links back to the landing page and navigate is
    # refused, so every operation after the first had no way home.
    for _ in range(_MAX_BACK_STEPS):
        try:
            res = execute("go_back", {})
        except SessionNotReadyError:
            raise
        except Exception:  # noqa: BLE001 — no history, or the app refuses it
            break
        data = (res.get("data") or res) or {}
        here = str(data.get("url") or here)
        if _same_page(here, entry_url):
            return None
        if _left_the_app(here, entry_url, known):
            # Overshot. Stop pressing -- every further press goes further from
            # the app -- and let navigate or the home link try from here. The
            # session itself is usually still valid; only the page is wrong.
            break
        if not data.get("moved"):
            break  # history exhausted; walking further only wastes calls

    # navigate NEXT, because on an app that allows it this is one call and
    # lands exactly where we mean. It is not always allowed: a portal that
    # carries its session in the URL loses it on a full page load, and the
    # worker refuses the command outright -- which is how the first live run of
    # this reset failed, using the one command the app forbids.
    try:
        execute("navigate", {"url": entry_url})
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
    results: list[list[dict]] = []
    for op in ops:
        steps = substitute_parameters(op, parameter_values)
        step_results: list[dict] = []
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
                failed = _return_to_entry(execute, entry_url, known)
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

    return build_report(ops, results)
