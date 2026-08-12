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


#: Path words that mean "still proving who you are".
LOGIN_FLOW_WORDS = (
    "login",
    "log-in",
    "password",
    "passcode",
    "credential",
    "signin",
    "sign-in",
    "logon",
    "auth",
    "sso",
    "saml",
    "oauth",
    "otp",
    "mfa",
    "2fa",
    "verify",
    "challenge",
)


def _is_login_flow_url(url: str) -> bool:
    """Is this URL part of the sign-in flow rather than the app proper?"""
    low = str(url or "").lower()
    if not low:
        return False
    path = low.split("://", 1)[-1]
    path = path[path.find("/") :] if "/" in path else ""
    return any(w in path for w in LOGIN_FLOW_WORDS)


def _origin_of(url: str) -> str:
    low = str(url or "")
    if "://" not in low:
        return ""
    rest = low.split("://", 1)[1]
    return rest.split("/", 1)[0].lower()


def _boundary_from_login_exit(
    url_events: list[dict[str, Any]], key: Any, by_seq: bool
) -> dict[str, Any] | None:
    """The last hop OUT of the sign-in flow, for logins that type nothing.

    The LAST such hop, not the first: a sign-in commonly bounces through an OTP
    or consent screen and back, and only the final exit leaves the human inside
    the app.

    Bounded to the origin the recording STARTED on. ICICI's statement portal
    lives at infinity.icici.bank.in/corp/AuthenticationController -- a deep app
    route whose path contains "auth", so it read as a sign-in page, and the last
    "exit" from it fell at the end of the workflow. The whole statement journey
    was then sliced into the login half and the workflow half came out empty.
    A sign-in bounces within its own host; a different host reached by clicking
    around inside the app is the app.
    """
    placed = _placed(url_events, key)
    start_origin = ""
    for _pos, u in placed:
        start_origin = _origin_of(u.get("to_url", "") or u.get("from_url", "") or "")
        if start_origin:
            break

    exits = [
        (pos, u)
        for pos, u in placed
        if u.get("to_url")
        and _is_login_flow_url(u.get("from_url", "") or "")
        and not _is_login_flow_url(u.get("to_url", "") or "")
        and not _is_redirect_hop(u.get("from_url", "") or "", u.get("to_url", "") or "")
        and (not start_origin or _origin_of(u.get("from_url", "") or "") == start_origin)
    ]
    if not exits:
        return None
    boundary = max(exits, key=lambda placed: placed[0])[1]
    return {
        "seq": event_seq(boundary) if by_seq else None,
        "timestamp": boundary.get("timestamp") or None,
    }


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
        # No credentials were typed -- which is not the same as no login.
        #
        # ICICI offers a QR sign-in: the human scans it with the bank's mobile
        # app and the web session becomes authenticated without a single field
        # being filled. SSO redirects, magic links and biometric approval are
        # the same shape. Defining "a login happened" as "credentials were
        # typed" made those recordings permanently unsplittable, and the member
        # was asked to record a login they had already recorded and could never
        # record in the expected way.
        #
        # What every one of them DOES leave is the transition out of the sign-in
        # page into the app. That is the boundary, and it is observable without
        # knowing how the human proved who they were.
        return _boundary_from_login_exit(url_events, key, by_seq)
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


class SplitError(RuntimeError):
    """The split ran and produced a result that cannot be compiled.

    Distinct from `split_bundle` returning None, which means "there is no login
    segment here" -- an ordinary answer the caller handles by compiling the
    bundle workflow-only. This is the other case: a boundary was found, and
    applying it destroyed the workflow half.
    """


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

    login, workflow = _slice(bundle, boundary, "login"), _slice(bundle, boundary, "workflow")

    # A workflow slice with NOTHING in it is a failed split, not a split.
    #
    # Not "no clicks": a workflow half can legitimately be navigations only.
    # What cannot happen is a half with neither.
    #
    # `_keep` sends anything it cannot PLACE to the login side, deliberately, so
    # a login request never leaks into the workflow. But a boundary carrying a
    # null position places nothing -- every event goes left, the workflow slice
    # comes out empty, and the compiler is handed a capture of a journey that
    # was never sliced off. It compiled to nothing and said nothing, and the
    # agent reading that concluded the RECORDING was wrong: it asked a member to
    # sign in and drive the whole journey again, twice, to route around a bug
    # that had already thrown their capture away.
    #
    # The recording is intact in these cases -- only the slicing lost it -- so
    # refusing here costs a re-run of the import, not a re-run of the human.
    placed = (workflow.get("click_events") or []) or (workflow.get("url_events") or [])
    if not placed:
        raise SplitError(
            "the login/workflow split produced an empty workflow half: the boundary "
            f"({boundary!r}) placed every interaction on the login side. The recording "
            "itself is intact -- this is the split, not the capture. Import it as "
            "workflow-only, or record the halves explicitly with --mode."
        )
    return login, workflow


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
