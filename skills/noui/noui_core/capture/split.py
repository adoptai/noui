"""Split a merged (login + workflow) recording bundle into two slices.

A single Tabby recording session can capture BOTH the login and the subsequent
authenticated workflow (see
`plans/noui/noui-single-session-login-workflow-capture-investigation.md`), and
that is now the default (`capture_record.py` with no `--mode`): one viewer link,
one sign-in. Tabby stamps such a session `recording_mode='login'`, which is
inert server-side, so NoUI provisions combined sessions as 'login' and does the
login/workflow differentiation here, with zero Tabby changes.

NoUI never *reads* that stamp to decide what a bundle is — it cannot be trusted
at all (a warm-pool session reports 'login' whatever was provisioned). See
`noui_core.capture.classify` for how the decision is actually made.

The boundary is the moment the login completes: the first *stable* navigation
after the last credential-field interaction. Everything up to and including that
landing navigation is the login (so `compile_login_bundle` derives the right
`post_login_url` and credential types); everything after is the authenticated
workflow (fed to `compile_workflow_bundle`). Splitting BEFORE tool generation is
what keeps the login form-submit request out of the workflow's tool set — that
request carries the login body and must never become an operation.

Both returned slices are ordinary bundles that the existing compilers consume
unchanged (`{session_id, recording_mode, started_at, stopped_at, har,
click_events, url_events, cookies?}`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from noui_core.compile.login_assets import _is_redirect_hop
from noui_core.event_order import event_seq, merged_order

# Field roles that mark a credential-entry interaction (login-only signal).
CREDENTIAL_FIELD_ROLES = frozenset({"username", "password", "otp", "unknown_sensitive"})

# An event's position on whichever axis the bundle supports: its `seq` ordinal
# (int) when numbered, else its `timestamp` (ISO-8601 UTC str, lexicographically
# comparable). Deliberately heterogeneous — hence `Any`: the two are never mixed
# within one split, so values are only ever compared against their own kind.
_PositionKey = Callable[[dict[str, Any]], Any]


def _position_key(by_seq: bool) -> _PositionKey:
    """The axis to place events on: ordinals when numbered, else wall clock."""
    if by_seq:
        return event_seq
    return lambda e: e.get("timestamp")


def _placed(events: list[dict[str, Any]], key: _PositionKey) -> list[tuple[Any, dict[str, Any]]]:
    """(position, event) for each event that can be placed on the axis.

    Events with no position are dropped — the caller decides which slice those
    belong to (see `_keep`). Positions are computed once here rather than
    recomputed per comparison.
    """
    out: list[tuple[Any, dict[str, Any]]] = []
    for ev in events:
        pos = key(ev)
        if pos is not None:
            out.append((pos, ev))
    return out


def _boundary_event(bundle: dict[str, Any]) -> dict[str, Any] | None:
    """The recorded event at which the login ends, or None if there is no login.

    The boundary is the first stable (non-redirect) URL transition that occurs
    after the last credential-field interaction; if the login never navigates
    afterwards, it falls back to that last interaction.

    "After" is decided by ``seq`` when clicks and URL transitions are BOTH fully
    numbered — they share one counter, assigned at interaction time. ``timestamp``
    on a debounced credential fill is its flush, up to 500ms late, which can drag
    the boundary past the landing navigation and hand the login's own form-submit
    request to the workflow slice, where it would be compiled into an operation.
    Bundles without ``seq`` keep the timestamp comparison — the previous
    behaviour, so pre-``seq`` recordings still compile.

    Returns ``{"seq": int|None, "timestamp": str|None}``: both are carried
    because the events are sliced on ``seq`` while the HAR — whose entries have
    no ordinals — is always sliced on wall clock.
    """
    clicks = [c for c in (bundle.get("click_events") or []) if isinstance(c, dict)]
    url_events = [u for u in (bundle.get("url_events") or []) if isinstance(u, dict)]
    by_seq = merged_order(clicks, url_events)
    key = _position_key(by_seq)

    creds = _placed([c for c in clicks if c.get("field_role") in CREDENTIAL_FIELD_ROLES], key)
    if not creds:
        return None
    last_cred_pos, last_cred = max(creds, key=lambda placed: placed[0])

    navs = [
        (pos, u)
        for pos, u in _placed(url_events, key)
        if u.get("to_url")
        and pos > last_cred_pos
        and not _is_redirect_hop(u.get("from_url", "") or "", u.get("to_url", ""))
    ]
    boundary = min(navs, key=lambda placed: placed[0])[1] if navs else last_cred
    return {
        "seq": event_seq(boundary) if by_seq else None,
        "timestamp": boundary.get("timestamp") or None,
    }


def find_login_boundary(bundle: dict[str, Any]) -> str | None:
    """The ISO timestamp separating login from workflow, or None.

    None means the bundle has no login segment (no credential-field
    interactions) — e.g. a workflow recorded against an already-authenticated
    profile. The caller then treats the whole bundle as workflow-only.

    This is the boundary's wall clock, for display and for HAR slicing. Event
    slicing goes through :func:`_boundary_event`, which also carries the ordinal
    — see there for why the ordinal is the one that decides "after".
    """
    boundary = _boundary_event(bundle)
    return boundary["timestamp"] if boundary else None


def split_diagnosis(bundle: dict[str, Any]) -> str:
    """One line explaining what the splitter saw, and what it concluded.

    The split turns on ONE thing -- whether any interaction was tagged as a
    credential field -- and when that tagging fails the bundle is declared
    login-free however plainly its URL timeline shows a sign-in. The member is
    then asked to record a login they already recorded, and nothing anywhere
    says why. This is the sentence that says why.
    """
    clicks = [c for c in (bundle.get("click_events") or []) if isinstance(c, dict)]
    urls = [u for u in (bundle.get("url_events") or []) if isinstance(u, dict)]
    creds = [c for c in clicks if c.get("field_role") in CREDENTIAL_FIELD_ROLES]
    roles = sorted({str(c.get("field_role")) for c in clicks if c.get("field_role")})
    login_urls = [
        u
        for u in urls
        if any(
            w in str(u.get("to_url") or "").lower() for w in ("login", "signin", "sign-in", "logon")
        )
    ]

    boundary = _boundary_event(bundle)
    if boundary is not None:
        return (
            f"split: login ends at seq={boundary.get('seq')} "
            f"({boundary.get('timestamp')}); {len(creds)} credential interaction(s) "
            f"of {len(clicks)} clicks, {len(urls)} url transitions."
        )

    detail = (
        f"split: NO login segment. {len(clicks)} clicks, {len(urls)} url transitions, "
        f"0 tagged as credential fields"
    )
    if roles:
        detail += f" (field roles seen: {', '.join(roles)})"
    if login_urls:
        detail += (
            f"; but {len(login_urls)} url(s) look like a sign-in "
            f"(e.g. {str(login_urls[0].get('to_url'))[:70]}). The recorder did not tag the "
            f"credential inputs -- a virtual keyboard, a masked custom control, or fields "
            f"inside a frame will do that -- so the login cannot be sliced off even though "
            f"it was recorded."
        )
    return detail


def split_bundle(bundle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Split a merged bundle into (login_bundle, workflow_bundle) at the boundary.

    Returns None when there is no login segment (see `find_login_boundary`), so
    the caller can fall back to compiling the bundle as workflow-only.

    Slicing rule: the login slice keeps events at or before the boundary (and any
    event that cannot be placed, so a login request never leaks into the
    workflow); the workflow slice keeps only events strictly after it. Events are
    cut on ``seq`` when the bundle carries it and on timestamp otherwise; HAR
    entries are always cut on `startedDateTime` (ISO-8601 UTC, lexicographically
    comparable) since they carry no ordinal. Top-level `cookies` (captured at
    drain, representing the authenticated state) go to the login slice, which is
    the only compiler that reads them.
    """
    boundary = _boundary_event(bundle)
    if boundary is None:
        return None
    return _slice(bundle, boundary, "login"), _slice(bundle, boundary, "workflow")


def _keep(value: Any, boundary: Any, side: str) -> bool:
    """Which slice an event belongs to, given its position key and the boundary's.

    An unplaceable event (no key, or no boundary key to compare against) goes to
    the login slice — see the leak rule in `split_bundle`.
    """
    if value is None or boundary is None:
        return side == "login"
    if side == "login":
        return value <= boundary
    return value > boundary


def _slice(bundle: dict[str, Any], boundary: dict[str, Any], side: str) -> dict[str, Any]:
    # Ordinals when the boundary was resolved on them, wall clock otherwise. The
    # two must not be mixed: cutting events on seq against a timestamp boundary
    # (or vice versa) compares unrelated scales and drops the whole slice.
    by_seq = boundary["seq"] is not None
    key: _PositionKey = _position_key(by_seq)
    cut = boundary["seq"] if by_seq else boundary["timestamp"]

    passthrough = {
        k: v for k, v in bundle.items() if k not in ("click_events", "url_events", "har", "cookies")
    }
    passthrough["click_events"] = [
        c
        for c in (bundle.get("click_events") or [])
        if isinstance(c, dict) and _keep(key(c), cut, side)
    ]
    passthrough["url_events"] = [
        u
        for u in (bundle.get("url_events") or [])
        if isinstance(u, dict) and _keep(key(u), cut, side)
    ]

    har = bundle.get("har") or {}
    log = dict(har.get("log") or {})
    entries = log.get("entries") or []
    log["entries"] = [
        e
        for e in entries
        if isinstance(e, dict) and _keep(e.get("startedDateTime"), boundary["timestamp"], side)
    ]
    passthrough["har"] = {**har, "log": log}

    if side == "login":
        passthrough["cookies"] = bundle.get("cookies") or []
    return passthrough
