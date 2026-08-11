"""Browser-driven skill compilation.

A HAR-replay skill (the default) fails for apps that mint per-request encryption
or per-session headers in the page's JavaScript: the recorded request bodies are
opaque `{data, key}` blobs that only the live page can produce, and the recorded
`session`/`req-id` headers are dead the moment the recording ends. ICICI is the
canonical case — 40 replayed operations that all 403.

For those apps the workable path is to drive the PAGE: navigate to where the
data renders, then read the DOM the page already fetched and decrypted. Tabby
exposes this via `/execute/browser`, and the harness surfaces it to the model as
the `call_web_browser` tool. This module compiles a recording into a skill that
uses that tool instead of `call_web_api`.

The "operations" of a browser skill are the readable data PAGES observed in the
recording — each compiled as: reach the page via the recorded in-app
`click_by_text` chain (NEVER a full-page `navigate`/goto — that reloads the SPA
and expires the session), then `get_page_summary`. There is no request replay,
so no encryption or frozen-header problem.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from noui_core.compile.locators import AMBIGUOUS, UNKNOWN, choose_locator
from noui_core.compile.login_assets import (
    _LOGIN_FLOW_SEGMENTS,
    _first_path_segment,
    _url_origin,
)
from noui_core.compile.parameters import derive_parameters, fill_steps
from noui_core.event_order import event_seq, order_events


def _slug_from_path(url: str) -> str:
    """A stable, readable operation-name stem from a URL's path.

    /credit-card -> credit_card ; /accounts/summary -> accounts_summary ;
    the bare origin -> home.
    """
    # Include the hash route: on a hash-router SPA every screen shares one path,
    # so a path-only slug names them all the same (read_landing, read_landing, …).
    path = (urlparse(url).path + _route_fragment(url)).strip("/")
    if not path:
        return "home"
    slug = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")
    return slug or "home"


def _route_fragment(url: str) -> str:
    """The hash-ROUTE part of a URL (``#/accounts`` -> ``/accounts``), or "".

    Only ``#/…`` counts. A bare ``#section`` anchor scrolls within one page and is
    not a route, so treating it as one would split a single page into many.
    """
    fragment = urlparse(url).fragment
    return fragment.rstrip("/") if fragment.startswith("/") else ""


def _page_key(url: str) -> str:
    """origin + path + hash route (query dropped) — the identity of a page.

    The fragment matters because hash-router SPAs put EVERY screen on one path:
    HSBCnet serves /uims/portal/HSBCnet/Landing#/accounts, #/statements, … so a
    path-only key collapsed the whole portal into a single page. That compiled
    without error (generate_browser_skill only raises on an EMPTY page list) and
    shipped a skill that could read nothing but the landing screen. Playwright
    does fire framenavigated on hash changes, so the routes are in the recording —
    they were simply discarded here.
    """
    return _url_origin(url) + urlparse(url).path.rstrip("/") + _route_fragment(url)


def _is_login_flow_url(url: str) -> bool:
    seg = _first_path_segment(url)
    seg_tokens = [t for t in re.split(r"[^a-z0-9]+", seg) if t]
    return seg in _LOGIN_FLOW_SEGMENTS or any(t in _LOGIN_FLOW_SEGMENTS for t in seg_tokens)


# A human reaches a submenu item by a short burst of clicks (expand the parent
# group, then click the child that routes). Clicks this long before the route
# change are treated as part of the same navigation gesture; earlier clicks are
# unrelated. The chain is capped so a noisy recording can't emit a long run.
_NAV_GESTURE_WINDOW_S = 20.0
_NAV_MAX_CLICKS = 3


def _parse_ts(ts: str) -> datetime | None:
    """Parse a recording timestamp (ISO 8601, trailing 'Z' allowed). None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _same_target(a: dict, b: dict) -> bool:
    """Do two recorded clicks address the same control?

    Only consulted for TEXT-LESS clicks, where equal (empty) labels are no
    evidence at all: a bank nav's hamburger and its chevrons all have empty text,
    and collapsing them as duplicates would drop steps out of the gesture.
    """
    la, lb = a.get("locator"), b.get("locator")
    if la and lb:
        return la.get("kind") == lb.get("kind") and la.get("value") == lb.get("value")
    return bool(a.get("selector")) and a.get("selector") == b.get("selector")


#: How far AFTER a navigation its driving click may still be recorded.
#:
#: An SPA route change is observed when the URL changes, which can be stamped
#: before the click handler that caused it. Small on purpose: this absorbs an
#: ordering inversion, not a gap between two human actions.
_NAV_SEQ_SLACK = 3
_NAV_TIME_SLACK_S = 1.5


def _opener_seqs(click_events: list[dict] | None) -> set[int]:
    """Seqs of clicks that OPENED whatever the next click used.

    The recorder asserts this on the SECOND click (``opened_by_previous``),
    because that is when it becomes true; here it is read back onto the first.

    Shared by every call site on purpose. It was computed inside the nav-chain
    builder alone, so a dropdown opened in a terminal operation's LEAD-IN never
    carried the flag -- the value was derived and then dropped one layer above
    the code that uses it, which is how ICICI's year trigger kept compiling to
    click_by_text on a label that is really the widget's current value.
    """
    out: set[int] = set()
    ordered = [c for c in (click_events or []) if isinstance(c, dict)]
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        if nxt.get("opened_by_previous"):
            pseq = event_seq(prev)
            if pseq is not None:
                out.add(pseq)
    return out


def _nav_clicks_for(from_url: str, nav_ev: dict, click_events: list[dict]) -> list[dict]:
    """The recorded click CHAIN that drove an in-app navigation FROM from_url.

    A browser skill must NOT reach a data page with a full-page navigate/goto:
    that is a reload, and refresh-sensitive portals (ICICI) expire the session on
    it — the skill's own read then lands on /session-expire. The recording already
    captured how a human got there.

    A single click is often not enough: portal navs are accordions. A human clicks
    the parent group ("Cards") to expand the submenu, THEN the child ("Credit
    Card") that actually routes — and only the child click changes the URL. Keying
    off the route-change timestamp alone captures just the child, so the compiled
    skill can never open the menu (the observed ICICI failure: the model clicked
    "Cards" → strict-mode chaos, never reaching the statement). This returns the
    full ordered gesture — parent-expand … child-navigate — so the skill can
    reproduce the whole path. Falls back to the single navigating click when the
    recording carries neither ordinals nor reliable timestamps.

    Causality and ordering come from ``seq`` when the bundle carries it: clicks
    and URL transitions are numbered from one counter assigned at interaction
    time, which timestamps are not (see noui_core.event_order). The gesture
    WINDOW stays in wall-clock — "clicks within 20s of each other" has no
    ordinal equivalent — and simply does not apply when timestamps are absent;
    the trailing-``_NAV_MAX_CLICKS`` cap below bounds the chain in that case.
    """
    from_key = _page_key(from_url)
    nav_dt = _parse_ts((nav_ev or {}).get("timestamp") or "")
    nav_seq = event_seq(nav_ev)
    opener_seqs = _opener_seqs(click_events)

    cands: list[dict] = []
    late_cands: list[dict] = []
    prev_was_hover = False
    for c in click_events or []:
        # A hover that opened a menu is part of the gesture, not noise: the
        # click after it targets something that does not exist until the pointer
        # is over the parent. Dropping it left the compiled path with no step
        # that opens the menu.
        if (c.get("event_type") or "click") not in ("click", "hover"):
            continue
        cu = c.get("url") or ""
        if not cu or _page_key(cu) != from_key:
            continue
        text = (c.get("text_content") or "").strip()
        locator = choose_locator(c.get("candidates"))
        # A click with no visible text used to be discarded outright, which threw
        # away every icon/SVG control — the hamburger toggle and the chevrons that
        # open a bank nav's accordions. With recorded candidates such a control is
        # addressable (test-id, aria-label, role+name), so it is only dropped when
        # there is no way to address it at all.
        if not text and locator is None:
            continue
        cdt = _parse_ts(c.get("timestamp") or "")
        cseq = event_seq(c)
        # Drop clicks made AFTER the navigation — they cannot have caused it.
        # A click and the navigation it causes are near-simultaneous, and their
        # recorded order can INVERT: an SPA route change is observed when the
        # URL changes, which for ICICI's overview -> credit-card hop landed at
        # seq 4 while the "Credit Cards" click that drove it landed at seq 6.
        # A strict "before the navigation" rule found no driving click, so the
        # credit-card page compiled with an empty chain -- the one hop that has
        # failed in every run -- while later hops, whose clicks happened to be
        # recorded first, compiled correctly.
        #
        # Allow a small window on the far side. Wide enough to absorb the
        # inversion, far narrower than the gap to the human's next action, so a
        # click belonging to the NEXT page is still never claimed by this one.
        # A hover is exempt, and so is the click it revealed.
        #
        # The window guards against claiming a LATER, unrelated click -- a "Log
        # out" back on the same page. A hover is never that: it cannot cause a
        # navigation, so it is not the thing being guarded against, and the
        # click it reveals is not a separate action but the second half of one
        # gesture. On ICICI the route change is stamped at seq 4 while the human
        # hovers at 7 and clicks at 9; with a slack of 3 the window closed at 7,
        # so the click that does the work was dropped outright and the menu-
        # opening hover was all that survived.
        is_hover = (c.get("event_type") or "click") == "hover"
        in_gesture = is_hover or prev_was_hover
        prev_was_hover = is_hover
        if nav_seq is not None and cseq is not None:
            if cseq > nav_seq + _NAV_SEQ_SLACK and not in_gesture:
                continue
            late = cseq > nav_seq
        elif nav_dt and cdt:
            delta = (cdt - nav_dt).total_seconds()
            if delta > _NAV_TIME_SLACK_S:
                continue
            late = delta > 0
        else:
            late = False

        (late_cands if late else cands).append(
            {
                "text": text,
                # Carried, or a hover becomes indistinguishable from a click the
                # moment it passes through here -- and _step_for_click emitted
                # click_element for ICICI's nav hover, on the same selector the
                # real click used. Third field this function has been caught
                # dropping, after candidates and is_opener.
                "event_type": (c.get("event_type") or "click"),
                "is_opener": event_seq(c) in opener_seqs,
                # The OTHER ways this control was seen. choose_locator collapses
                # them to one winner above; the runtime needs the rest to fall
                # back on when that winner stops matching.
                "candidates": c.get("candidates"),
                "selector": c.get("selector") or "",
                "locator": locator,
                "outcome": c.get("outcome") if isinstance(c.get("outcome"), dict) else None,
                "dt": cdt,
                "seq": cseq,
            }
        )
    if not cands and late_cands:
        # Nothing preceded the navigation, so the click that caused it was
        # recorded just after: an SPA route change is observed when the URL
        # changes, and ICICI stamped the overview -> credit-card hop at seq 4
        # while the "Credit Cards" click that drove it landed at seq 6. Taking
        # these only as a last resort keeps an ordinary later click -- a "Log
        # out" back on the same page -- out of the gesture.
        cands = late_cands
    if not cands:
        return []

    # Neither ordinals nor timestamps means the causality filter above could not
    # run, so the "clicks before the nav" set may actually be clicks made AFTER
    # it — a recorded "Log out" would then be compiled into the read recipe. Fall
    # back to the documented single-click behaviour rather than trusting an
    # unbounded window.
    ordered = None
    if nav_seq is not None and all(c["seq"] is not None for c in cands):
        ordered = sorted(cands, key=lambda c: c["seq"])
    elif nav_dt is not None and all(c["dt"] is not None for c in cands):
        ordered = sorted(cands, key=lambda c: c["dt"])

    if ordered is None:
        chain = cands[-1:]
    elif all(c["dt"] is not None for c in ordered):
        anchor = ordered[-1]["dt"]  # the navigating click
        chain = [c for c in ordered if (anchor - c["dt"]).total_seconds() <= _NAV_GESTURE_WINDOW_S]
    else:
        # Ordered by seq but undated (e.g. an agent-driven capture): the order is
        # trustworthy, so keep the chain and let the trailing cap bound it.
        chain = ordered

    # Collapse consecutive identical labels (double-clicks, re-renders) and cap
    # to the trailing N so only the immediate expand→navigate steps are emitted.
    out: list[dict] = []
    for c in chain:
        # Collapse consecutive identical labels — but only when they are really
        # the same control. Two different icon buttons both have empty text, and
        # merging them would silently drop a step in the gesture.
        # A hover and the click it revealed are NOT a repeat, even though they
        # share a target and both have empty text: ICICI's nav hover and the
        # submenu click both resolve to #scroll-container > div:nth-of-type(5).
        # Collapsing them left a gesture that opens the menu and clicks nothing
        # in it -- or, once event_type was lost below, one click_element that
        # never opened anything.
        same_kind = out and out[-1].get("event_type") == c.get("event_type")
        if same_kind and out[-1]["text"] == c["text"] and (c["text"] or _same_target(out[-1], c)):
            continue
        out.append(
            {
                "text": c["text"],
                # Carried through the projection, not just built above it. This
                # is where the hover lost the one field that made it a hover --
                # the same class of gap that had already cost candidates and
                # is_opener at the other builder.
                "event_type": c.get("event_type") or "click",
                # seq, because the arrival budget is measured from WHEN this
                # click happened. Fourth field this projection has been caught
                # dropping, after candidates, is_opener and event_type -- each
                # time the loss showed up somewhere far away, as a compiled
                # skill quietly missing something it had recorded.
                "seq": c.get("seq"),
                "is_opener": c.get("is_opener"),
                "candidates": c.get("candidates"),
                "selector": c["selector"],
                "locator": c["locator"],
                "outcome": c["outcome"],
            }
        )
    if len(out) <= _NAV_MAX_CLICKS:
        return out
    # Keep the FIRST click plus the most recent ones. A plain trailing slice drops
    # the entry-nav gesture: "Menu -> Banking -> Cards -> Credit Card" became
    # ["Banking","Cards","Credit Card"], so the first click targeted an element
    # still hidden behind the un-opened hamburger. Three-deep accordions behind a
    # menu toggle are normal on HSBCnet/ICICI.
    return [out[0]] + out[-(_NAV_MAX_CLICKS - 1) :]


def app_origins_from(
    url_events: list[dict], login_url: str, click_events: list[dict] | None = None
) -> set[str]:
    """Every origin that is part of THIS app, discovered from the recording.

    The compiler used to keep only pages on the exact login origin, to drop the
    third-party telemetry that poisoned earlier HAR-replay compiles. That is too
    strict for a bank: ICICI serves its portal from
    ``retailnetbanking.icici.bank.in`` and its statement download from
    ``infinity.icici.bank.in``. The entire e-Statements page — the one that
    actually produces the PDF — was discarded before any operation could be built
    from it, so the compiled skill had no way to reach the file at all.

    Decided from evidence rather than by matching domains. A registrable-domain
    heuristic is guesswork (``bank.in`` is a public suffix, so "share the last two
    labels" would make every Indian bank the same app), but the recording already
    knows: an origin the human reached BY NAVIGATING FROM a page of this app is
    part of this app. Telemetry, analytics and consent widgets never appear as a
    main-frame navigation a human clicked into; a bank's second host always does.

    TWO pieces of evidence are required, not one. Being navigated to from the app
    is not enough by itself: a consent wall, an SSO hop or a payment gateway is
    reached exactly that way, and so is any third party that redirects the main
    frame. The origin must ALSO be somewhere the human then did something — a
    click or an input recorded on it. A beacon has no interactions; the page
    where you pick "Annual" and press download has plenty.

    Seeded with the login origin and grown in interaction order, so a two-hop
    path (portal -> statements host -> a page within it) is picked up too.
    """
    interacted = {
        _url_origin(c.get("url") or "")
        for c in click_events or []
        if (c.get("url") or "").startswith(("http://", "https://"))
    }
    origins: set[str] = set()
    if login_url:
        origins.add(_url_origin(login_url))
    for ev in order_events(url_events):
        to_url = (ev or {}).get("to_url") or ""
        from_url = (ev or {}).get("from_url") or ""
        if not to_url.startswith(("http://", "https://")):
            continue
        to_origin = _url_origin(to_url)
        if to_origin in origins:
            continue
        if _url_origin(from_url) in origins and to_origin in interacted:
            origins.add(to_origin)
    origins.discard("")
    return origins


def derive_browser_pages(
    url_events: list[dict],
    click_events: list[dict] | None = None,
    *,
    login_url: str,
    max_pages: int = 12,
) -> list[dict]:
    """Pick the readable data pages from a recording's URL history, and — for each
    — the in-app CLICK that reaches it (so the skill navigates without a reload).

    Keeps distinct pages on the app's own origin, in first-seen order, dropping
    login/auth-flow pages and pages whose query string is volatile.

    Each page is {name, url, nav}. ``nav`` is:
      - None for the post-login LANDING page (the session lands there after login;
        the skill just reads it — no navigation, no reload), and
      - a non-empty list of {"text": ..., "selector": ...} clicks for a page
        reached by clicking within the app — the full gesture (e.g. expand
        "Cards" → click "Credit Card"), each emitted as click_by_text (client-side
        route changes that preserve the session).

    Empty list means the recording never left the login flow — the caller must
    treat that as "nothing to compile".
    """
    # Interaction order, not recording order — pages are picked in FIRST-SEEN
    # order and each page's nav gesture is read off the click list, so both sides
    # must be sorted before anything positional is read from them.
    click_events = order_events(click_events)
    url_events = order_events(url_events)
    # Every origin this app spans, not just the login one — see app_origins_from.
    app_origins = app_origins_from(url_events, login_url, click_events)
    seen: set[str] = set()
    pages: list[dict] = []
    for ev in url_events:
        url = (ev or {}).get("to_url") or ""
        if not url or not url.startswith(("http://", "https://")):
            continue
        # Belongs to this app — the login origin, or an origin the human
        # navigated to FROM this app. Third-party widget/telemetry origins (the
        # DevRev/Dynatrace noise that poisoned the HAR-replay skill's identity)
        # never appear that way and are still dropped.
        if app_origins and _url_origin(url) not in app_origins:
            continue
        if _is_login_flow_url(url):
            continue
        # NOT filtered on _has_volatile_query. A one-time token in the recorded
        # URL makes it unreplayable by NAVIGATION — but a browser skill never
        # navigates: it reaches the page by replaying the in-app click chain, and
        # _page_key ignores the query anyway. Discarding here meant a portal whose
        # data screens all carry e.g. ?ticket= compiled to zero readable pages.

        key = _page_key(url)
        if key in seen:
            continue
        seen.add(key)
        # How did the human reach this page? If the transition came FROM a login
        # page, it's the post-login landing (auto-redirect) — no nav click. If it
        # came from another app page, replay the click that drove the SPA route.
        from_url = (ev or {}).get("from_url") or ""
        # Three distinct states, kept apart deliberately:
        #   None  — the post-login LANDING page; the session already lands here.
        #   [...] — the click chain ON from_url that routed here.
        #   []    — an in-app hop whose driving click could not be recovered
        #           (icon/SVG button with no text — common in bank navs).
        # Collapsing [] into None (the old ``or None``) made an unreachable page
        # look like the landing page: its recipe became a bare get_page_summary,
        # so the operation claimed to read /accounts and actually returned the
        # landing DOM. Confidently wrong data is worse than a missing operation.
        is_landing = not from_url or _is_login_flow_url(from_url)
        nav: list[dict] | None = None
        if not is_landing:
            nav = _nav_clicks_for(from_url, ev or {}, click_events)
        pages.append(
            {
                "name": f"read_{_slug_from_path(url)}",
                "url": url,
                "nav": nav,
                "_from_key": None if is_landing else _page_key(from_url),
            }
        )
        if len(pages) >= max_pages:
            break
    return _resolve_nav_chains(pages)


def _resolve_nav_chains(pages: list[dict]) -> list[dict]:
    """Rewrite each page's ``nav`` to the FULL click chain from the landing page,
    and drop pages whose chain cannot be resolved.

    ``_nav_clicks_for`` only recovers the clicks made ON the immediately preceding
    page, so a two-hop path (landing → accounts → statements) compiled to just
    ["Statements"]. At run time the session lands on the landing page, where that
    control does not exist — the operation failed, or worse clicked something else
    with the same label. Walking the from-page graph back to the landing page
    yields the whole gesture.
    """
    by_key = {_page_key(p["url"]): p for p in pages}
    resolved: list[dict] = []
    for page in pages:
        chain: list[dict] = []
        cur: dict | None = page
        visited: set[str] = set()
        ok = True
        while cur is not None:
            own = cur.get("nav")
            if own is None:  # reached the landing page — chain is complete
                break
            if not own:  # an undeterminable hop: the whole path is unreliable
                ok = False
                break
            chain = list(own) + chain
            parent_key = cur.get("_from_key")
            if not parent_key or parent_key in visited:
                # No recorded parent (or a cycle): treat what we have as reached
                # from the landing page rather than inventing more hops.
                break
            visited.add(parent_key)
            # A parent that isn't itself a readable page (login flow, filtered
            # out) means we are already at the start of the in-app path.
            cur = by_key.get(parent_key)
        out = {k: v for k, v in page.items() if not k.startswith("_")}
        out["nav"] = chain if chain else None
        # The SEGMENT: only the hop from the previous page, and the page it
        # starts on. The full chain above is the whole journey repeated into
        # every operation, which is why a replay of operation 2 demands being
        # back at a landing page the recording left once and never returned to.
        # Kept alongside rather than instead of `nav` so nothing downstream
        # moves until it opts in.
        own_hop = page.get("nav")
        out["segment"] = list(own_hop) if own_hop else ([] if own_hop == [] else None)
        parent = by_key.get(page.get("_from_key") or "")
        out["starts_from"] = parent["url"] if parent else None
        if not ok:
            # Keep the page with the PARTIAL chain instead of discarding it.
            #
            # Dropping was meant to avoid a worse bug: a page with no chain
            # looked like the landing page, so its recipe became a bare
            # get_page_summary and the operation claimed to read /accounts while
            # returning the landing DOM. Confidently wrong data is worse than a
            # missing operation, and that reasoning still holds.
            #
            # But on a bank it discards the whole workflow. An ICICI recording
            # captured 15 clicks -- Cards, Credit Cards, Past, download previous
            # statement -- crossed to the Finacle host, and every hop there was an
            # unlabelled control whose driving click could not be recovered. One
            # unresolvable hop dropped every statement page, the compile emitted
            # a single overview operation, and three re-recordings could not fix
            # data that was already correct.
            #
            # So keep what was resolved and make the uncertainty explicit: the
            # page is marked partial, and the operation asserts the URL it is
            # supposed to reach. If the partial chain does not arrive, replay
            # blocks on that expectation -- which is the honest failure the drop
            # was protecting against, without throwing the workflow away.
            out["nav_partial"] = True
        resolved.append(out)
    return resolved


#: Selectors that name the document rather than a control.
_DOCUMENT_SELECTORS = frozenset({"body", "html", ":root", "body *", "html body"})


def _same_control(a: dict, b: dict) -> bool:
    """Do two steps drive the same control?"""
    pa, pb = (a.get("params") or {}), (b.get("params") or {})
    return any(pa.get(k) and pa.get(k) == pb.get(k) for k in ("selector", "text", "label"))


def _collapse_repeats(steps: list[dict]) -> list[dict]:
    """One gesture, one step.

    A click on a submit control fires the form's submit too -- ICICI recorded a
    click and a submit on #DOWNLOAD_ESTATEMENT_PDF four milliseconds apart. The
    download was attributed to the submit, making it the terminal step, while the
    click became a lead-in: one press of one button compiled as two clicks, and a
    replay would ask the portal for the file twice.

    Only ADJACENT repeats collapse. The same control clicked again later in a
    workflow is a real second action -- a paging control, a retry -- and must
    survive.
    """
    out: list[dict] = []
    for step in steps:
        if out and out[-1].get("command") == step.get("command") and _same_control(out[-1], step):
            # Keep whichever carries more for the runtime (expectations, frame).
            if len(step) > len(out[-1]):
                out[-1] = step
            continue
        out.append(step)
    return out


def _is_css_kind(kind: str) -> bool:
    """Kinds whose value is a CSS selector rather than human-visible text."""
    return kind in ("css", "testid", "id", "name", "css_path")


_LOGIN_CONTROL = re.compile(r"\|\s*(log ?in|sign ?in|login|signin|continue to login)\s*$", re.I)


def _is_login_control(click: dict) -> bool:
    """Does this click operate a sign-in control?

    A workflow can contain a SECOND login, to another origin, inside the slice
    the splitter already separated the first one from. ICICI's statement portal
    has its own: the recording clicked "Log In" and the URL gained a jsessionid.
    A replay whose session already satisfies it arrives past that point, the
    button is absent, and the compiled step matches nothing -- unnecessary, not
    broken.

    Matched on the accessible NAME, not on a selector: #DEH_LOGIN says nothing,
    while `role_name: button|Log In` is the control announcing what it is.
    """
    for cand in click.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        if str(cand.get("kind") or "") == "role_name" and _LOGIN_CONTROL.search(
            str(cand.get("value") or "")
        ):
            return True
    return False


def _stamp_source(step: dict | None, click: dict) -> dict | None:
    """Remember which recorded interaction a step was compiled from.

    Only folding reads this, and only to place a boundary between two variants
    recorded one after the other. It is provenance, never behaviour -- see
    `_behaviour`, which keeps it from making two identical actions look
    different.
    """
    if step is not None and click.get("seq") is not None:
        step["_seq"] = click["seq"]
    return step


def _step_for_click(click: dict) -> dict | None:
    """One compiled step for one recorded click.

    Prefers the recorded locator CANDIDATE that matched exactly one node, which
    is the only kind known to identify the control. CSS-expressible candidates
    become `click_element`; semantic ones (role+name, label, visible text) become
    `click_by_text`, which is what the runtime can resolve.

    `exact: true` is set on text clicks. The recorder tells us the text matched a
    single control, so a substring match can only widen that back into the
    ambiguity we just eliminated — the HSBCnet misclick.

    Falls back to the recorded text when there is no usable candidate, which is
    what every pre-schema-4 recording will hit.
    """
    # Fall back to resolving the candidates here. Click events arrive with a
    # pre-resolved "locator", hover events do not -- they carry candidates and
    # nothing else -- so the hover branch below read None and returned None for
    # every hover ever recorded. The recorder captured ICICI's nav hover, the
    # runtime had a hover command, and the step still never reached the skill.
    locator = click.get("locator") or choose_locator(click.get("candidates"))
    text = (click.get("text") or "").strip()

    # A radio or checkbox is SET, not clicked.
    #
    # Portals style these as images or spans over a hidden input, so a click
    # lands on the decoration and the input never changes -- ICICI's Monthly /
    # Annual period is exactly that, and a replay that "clicked Annual" was
    # still asking for the monthly statement. set_checked drives the control and
    # verifies the state actually changed, which a click cannot promise.
    element = click.get("element") or {}
    role = str(element.get("role") or "").lower() if isinstance(element, dict) else ""
    # The ROLE_NAME candidate announces it too: "radio|Annual". ICICI's period
    # control reports no element role and its unique candidate is that
    # role_name, so both halves of the old test failed -- the step compiled to
    # click_by_text "Annual", which clicks the LABEL. The click reported ok, the
    # radio stayed on Monthly, and the replay downloaded a monthly statement
    # while asking for the annual one.
    if not role:
        for cand in click.get("candidates") or []:
            if isinstance(cand, dict) and str(cand.get("kind")) == "role_name":
                head = str(cand.get("value") or "").split("|", 1)[0].strip().lower()
                if head in ("radio", "checkbox"):
                    role = head
                    break
    # set_checked needs a SELECTOR, and the chosen locator may be the role_name
    # that identified the control rather than a css one. Fall back to the best
    # css candidate: addressing the input directly is the whole point.
    target = locator if (locator and locator.get("is_css")) else None
    if role in ("radio", "checkbox") and target is None:
        for cand in click.get("candidates") or []:
            if not isinstance(cand, dict) or not _is_css_kind(str(cand.get("kind") or "")):
                continue
            # UNIQUE only. ICICI's Monthly and Annual radios share an id and a
            # name, so that candidate matches two nodes -- set_checked would
            # take the first and select MONTHLY, which is the exact bug this is
            # meant to fix, made silent again. An ambiguous selector is worse
            # here than no selector: falling through leaves the step as it was.
            if cand.get("value") and cand.get("match_count") in (1, -1):
                target = {"value": str(cand["value"]), "kind": cand.get("kind"),
                          "match_count": cand.get("match_count"),
                          "confidence": UNKNOWN, "is_css": True}
                break
    # No unique selector? Address it the way the recorder identified it: role
    # plus accessible name, which the worker now resolves for set_checked. That
    # is the ONLY unique handle on a control whose id and name it shares with
    # its sibling -- ICICI's Monthly / Annual pair.
    if role in ("radio", "checkbox") and target is None:
        for cand in click.get("candidates") or []:
            if not isinstance(cand, dict) or str(cand.get("kind")) != "role_name":
                continue
            value = str(cand.get("value") or "")
            if "|" not in value or cand.get("match_count") not in (1, -1):
                continue
            role_part, name_part = value.split("|", 1)
            if role_part.strip().lower() not in ("radio", "checkbox"):
                continue
            step = {
                "command": "set_checked",
                "params": {"role": role_part.strip(), "name": name_part.strip(),
                           "checked": True},
            }
            expect = _expect_for_click(click)
            if expect:
                step["expect"] = expect
            return step

    if role in ("radio", "checkbox") and target:
        step = {
            "command": "set_checked",
            "params": {"selector": target["value"], "checked": True},
        }
        if element.get("in_iframe"):
            for key in ("frame_url", "frame_name"):
                value = str(element.get(key) or "").strip()
                if value:
                    step["params"][key] = value
        expect = _expect_for_click(click)
        if expect:
            step["expect"] = expect
        return step

    # A locator that resolves to the document addresses no control. It appears
    # when the click landed on padding and the walk found nothing better, and at
    # replay it clicks the page: harmless at best, dismissing something at worst.
    _loc = click.get("locator") or {}
    if str(_loc.get("value") or "").strip().lower() in _DOCUMENT_SELECTORS:
        return None

    if click.get("is_opener") and (click.get("candidates") or []):
        # This click OPENED the control the next one used -- a dropdown showing
        # its current value, clicked to reveal the options. Its visible label is
        # therefore the widget's VALUE ("FY2024-25"), not a control name, and it
        # will read differently at replay: next year, or on another account. So
        # address it by a stable selector even though a text candidate exists.
        #
        # Only for an opener. An ordinary control whose label happens to be a
        # div -- ICICI's "Credit Cards" nav -- keeps its text, which is the whole
        # point of recovering it.
        for cand in click["candidates"]:
            if isinstance(cand, dict) and _is_css_kind(str(cand.get("kind") or "")):
                if cand.get("value") and cand.get("match_count") in (1, -1, None):
                    return {"command": "click_element", "params": {"selector": str(cand["value"])}}

    if (click.get("event_type") or "") == "hover":
        # Hover is addressed like any other control, but it opens rather than
        # activates. Only a CSS-expressible locator can be hovered: there is no
        # hover-by-text, and guessing one would hover the wrong thing.
        if not (locator and locator.get("is_css")):
            return None
        hover: dict = {"command": "hover", "params": {"selector": locator["value"]}}
        element = click.get("element") or {}
        if isinstance(element, dict) and element.get("in_iframe"):
            for key in ("frame_url", "frame_name"):
                value = str(element.get(key) or "").strip()
                if value:
                    hover["params"][key] = value
        return hover

    # Marked, not dropped: on a cold replay this login IS needed. Optional means
    # "skip when its control is absent", which is the only reading that is true
    # both times.
    optional = _is_login_control(click)

    step: dict | None = None
    if locator and locator.get("is_css"):
        step = {"command": "click_element", "params": {"selector": locator["value"]}}
    elif locator:
        # role_name candidates carry "role|name"; the runtime matches on the name.
        value = locator["value"]
        if locator["kind"] == "role_name" and "|" in value:
            value = value.split("|", 1)[1]
        step = {"command": "click_by_text", "params": {"text": value, "exact": True}}
    elif text:
        step = {"command": "click_by_text", "params": {"text": text}}

    if step is None:
        return None

    # Which frame the human clicked in. Bank portals embed whole applications in
    # iframes (ICICI serves statements from Finacle that way), and a step that
    # names only the control drives the top-level page instead — the control is
    # not there, the run improvises, and a navigation the human completed
    # becomes one the skill cannot reproduce. The runtime matches on the name
    # first, then the url ignoring its query string, where session tokens churn.
    element = click.get("element") or {}
    if isinstance(element, dict) and element.get("in_iframe"):
        for key in ("frame_url", "frame_name"):
            value = str(element.get(key) or "").strip()
            if value:
                step["params"][key] = value

    # The OTHER ways the recorder saw this control, for the runtime to try when
    # the chosen one matches nothing.
    #
    # The recorder ranks several candidates -- id, aria-label, role+name, visible
    # text, css path -- and choose_locator committed to one and discarded the
    # rest. On a portal whose nav is icon divs the winner is a positional css
    # path, and when the page shifts by one node the step is simply dead, while
    # the text candidate that would have worked was recorded and thrown away.
    # An ICICI replay failed every nav step this way, and a hand-written skill
    # using click_by_text("Cards") on the same portal worked.
    #
    # Ordered as the recorder ranked them, unique matches only: a fallback that
    # matches several nodes would trade a dead step for a wrong click.
    alts = []
    for cand in click.get("candidates") or []:
        if not isinstance(cand, dict) or not cand.get("value"):
            continue
        if locator and cand.get("value") == locator.get("value"):
            continue
        if cand.get("match_count") not in (1, -1, None):
            continue
        value = str(cand["value"])
        kind = str(cand.get("kind") or "")
        if kind == "role_name" and "|" in value:
            value = value.split("|", 1)[1]
        alts.append({"selector": value} if _is_css_kind(kind) else {"text": value})
    if alts:
        step["params"]["fallbacks"] = alts[:4]

    if locator:
        # Carried for the human reading the recipe and for a future repair pass:
        # an AMBIGUOUS step is the one to re-point first when a skill misbehaves.
        step["locator"] = {
            "kind": locator["kind"],
            "confidence": locator["confidence"],
            "match_count": locator["match_count"],
        }
        if text and not locator.get("is_css"):
            step["locator"]["recorded_text"] = text
    if step is not None and optional:
        step["optional"] = True
        step["optional_reason"] = (
            "this signs in to the app this operation runs on, which a replay "
            "may already have done"
        )
    return step


def _expect_for_click(click: dict) -> dict | None:
    """The postcondition a step should assert, from what the recorder observed.

    A linear script cannot tell that it has gone wrong; it just keeps clicking.
    An expectation turns that into a stop: "after this click the URL becomes X"
    is checkable in one step, instead of the failure surfacing five clicks later
    on the wrong page with confidently wrong data.
    """
    outcome = click.get("outcome")
    if not isinstance(outcome, dict):
        return None
    expect: dict = {}
    if outcome.get("navigated") and outcome.get("to_url"):
        expect["url"] = outcome["to_url"]
    if outcome.get("download"):
        expect["download"] = True
    settled = outcome.get("settled_ms")
    if isinstance(settled, int) and settled > 0:
        # Observed, not guessed. Rounded up to a whole second and given headroom,
        # because a recorded settle is one sample from one network.
        expect["settle_ms"] = min(15000, max(1000, settled * 2))
    return expect or None


def _is_shared_class_selector(selector: str) -> bool:
    """Is this a selector that could name many controls at once?

    An id addresses one node by definition, and a recorded css_path is a walk
    down the tree. A bare tag.class is neither: `a.sub-menu-list-item-link` names
    a STYLE, and a nav gives every submenu item in it the same one.
    """
    value = (selector or "").strip()
    if not value or "#" in value or ">" in value or " " in value:
        return False
    return "." in value


def _scope_under_the_hover(step: dict, hover_selector: str) -> None:
    """Address the revealed control INSIDE the thing that revealed it.

    ICICI's nav compiled to `a.sub-menu-list-item-link` -- a class every submenu
    item in the nav carries, and one the recorder never checked for uniqueness:
    the `match_count: 1` on that step belongs to a DIFFERENT candidate, a css_path
    to an ancestor. So the click resolved whichever match came first in the
    document, and when that one sat in a menu that was not open the step failed
    "on the page but not visible". About half of all runs died there, on step 0,
    and passed on an immediate retry.

    A control revealed by a hover lives inside what revealed it, so the hover's
    own selector is the scope that disambiguates -- no site knowledge, no
    guessing which of the matches was meant.

    The unscoped selector stays as the first fallback, since a menu rendered in
    an overlay rather than inside its trigger would not match the scoped form.
    Only for a selector that could name many controls: an id or a recorded path
    is left exactly as it was.
    """
    params = step.get("params") or {}
    selector = str(params.get("selector") or "")
    if not _is_shared_class_selector(selector):
        return
    params["selector"] = f"{hover_selector} {selector}"
    fallbacks = list(params.get("fallbacks") or [])
    params["fallbacks"] = [{"selector": selector}, *fallbacks]
    step["params"] = params


def _fold_hover_into_click(steps: list[dict]) -> list[dict]:
    """A hover and the click it enables are ONE gesture, so emit one step.

    As two steps they are two round trips to the worker, and a menu that only
    exists while the pointer rests on its trigger cannot survive the gap.
    Measured on ICICI: hover returned ok and the submenu was not visible on any
    later call -- 1s, 2s, 3s, 4s. The click was racing the menu closing, which
    looked like slowness and got "fixed" twice by waiting longer.

    Only a css-addressable hover immediately followed by a click_element: there
    is no hover-by-text, and a hover followed by anything else is not this
    pattern.
    """
    out: list[dict] = []
    i = 0
    while i < len(steps):
        step = steps[i]
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        if (
            step.get("command") == "hover"
            and nxt is not None
            and nxt.get("command") == "click_element"
            and (step.get("params") or {}).get("selector")
        ):
            merged = {**nxt, "params": {**(nxt.get("params") or {}),
                                        "hover_first": step["params"]["selector"]}}
            _scope_under_the_hover(merged, step["params"]["selector"])
            # The hover's own expectation described opening the menu; the click's
            # describes where the gesture lands. Keep the click's -- but NOT its
            # timing.
            #
            # How long the menu takes to appear was measured on the HOVER, and
            # folding the pair threw that away with the rest of its expectation:
            # `_inherit_hover_settle` runs after this and has no hover step left
            # to read, so it is a no-op for exactly the pairs it was written for.
            # The merged step then carried no settle at all and fell back to the
            # runtime's 3s floor, while ICICI's nav measured 2161ms to settle and
            # renders its submenu right around there -- which is why this step was
            # a coin flip that passed on an immediate retry.
            #
            # Only when the click has no measurement of its own: what the click
            # observed is about where it landed, and is the better answer when
            # it exists.
            hover_settle = (step.get("expect") or {}).get("settle_ms")
            if hover_settle and not (merged.get("expect") or {}).get("settle_ms"):
                merged["expect"] = {**(merged.get("expect") or {}),
                                    "settle_ms": hover_settle}
            out.append(merged)
            i += 2
            continue
        out.append(step)
        i += 1
    return out


def _inherit_hover_settle(steps: list[dict]) -> None:
    """A click after a hover waits for the menu the hover opened.

    The recorder measures the settle on the HOVER -- 4322ms on ICICI's nav --
    and the click that follows it carries none, so it fell back to the runtime's
    3s floor. The reveal sits right around 3s, which made the step a coin flip:
    it passed in one live run and failed "on the page but not visible" in the
    next.

    The hover's measurement IS how long its menu takes to appear, which is
    exactly what the next click is waiting on. Inherited only when the click has
    no settle of its own -- its own measurement, where it has one, describes its
    own page and is the better number.
    """
    for before, after in zip(steps, steps[1:]):
        if before.get("command") != "hover":
            continue
        settle = (before.get("expect") or {}).get("settle_ms")
        if not isinstance(settle, int) or settle <= 0:
            continue
        expect = after.get("expect") or {}
        if expect.get("settle_ms"):
            continue
        after["expect"] = {**expect, "settle_ms": settle}


#: How much longer than the human we allow. Their gap is an upper bound already
#: -- they could not click before the control existed -- but it was measured on
#: one network on one day, so a little room costs nothing.
_ARRIVAL_BUFFER = 1.5

#: A human clicking straight through says nothing about how fast the page was --
#: they may have known exactly where to aim, or the control may have been there
#: already. Measured 4ms between two clicks on ICICI, which as a budget meant the
#: next arrival waited 6ms and the operation after it failed its starts_from.
#: Below this the measurement carries no information, so the floor stands in.
_ARRIVAL_FLOOR_MS = 5_000

#: Nobody's page takes this long. Beyond it the recorded gap is someone reading
#: their email, not a page loading, and a replay must not inherit that.
_ARRIVAL_CAP_MS = 45_000


def arrival_budget_ms(click_events: list[dict], arrive_seq: int | None) -> int | None:
    """How long the human waited after the click that caused this arrival.

    Measured, not guessed: the gap between the click at ``arrive_seq`` and their
    next interaction. They could not have clicked the next control before it
    existed, so this is an upper bound on the page becoming usable -- and on
    ICICI's statement portal it was 17.2s, where a 3s constant gave up long
    before the form appeared.

    An upper bound is exactly what a deadline wants: the replay proceeds the
    moment the control appears and only uses this to decide when to stop
    waiting. Buffered a little because one recording is one sample, and capped
    because some of that gap is a human reading.
    """
    if arrive_seq is None:
        return None
    ordered = sorted(
        (c for c in click_events or [] if event_seq(c) is not None),
        key=lambda c: event_seq(c),
    )
    cause = next((c for c in ordered if event_seq(c) == arrive_seq), None)
    if cause is None:
        return None
    start = _parse_ts(cause.get("timestamp") or "")
    if start is None:
        return None
    for c in ordered:
        if event_seq(c) <= arrive_seq:
            continue
        at = _parse_ts(c.get("timestamp") or "")
        if at is None:
            continue
        gap_ms = int((at - start).total_seconds() * 1000 * _ARRIVAL_BUFFER)
        return min(max(gap_ms, _ARRIVAL_FLOOR_MS), _ARRIVAL_CAP_MS)
    return None


def _steps_for_page(p: dict) -> list[dict]:
    """Recipe to read a page WITHOUT a reload.

    - landing page (nav is None): just get_page_summary — the session already
      lands here after login.
    - in-app page (nav is a click chain): one click step per click in the gesture
      (e.g. expand "Cards" → click "Credit Card") — each a client-side route
      change, no reload — then get_page_summary.

    A full-page navigate/goto is deliberately never emitted: it reloads the page,
    and refresh-sensitive portals expire the session on it (the ICICI failure —
    the skill's own read landed on /session-expire).
    """
    steps: list[dict] = []
    carried: dict = {}
    for click in p.get("nav") or []:
        step = _stamp_source(_step_for_click(click), click)
        if step is None:
            continue
        expect = _expect_for_click(click) or {}
        if step.get("command") == "hover":
            # A hover cannot navigate or download -- it opens the thing that
            # does. The recorder stamps a hover and the click it reveals at the
            # same millisecond, so the arrival outcome was attributed to the
            # hover, and the compiled step asserted "you are now on /credit-card"
            # immediately after opening a menu. The replay failed there every
            # time, on an assertion no hover could ever satisfy.
            #
            # Carry those parts to the step that does cause them rather than
            # discarding: the assertion is the only thing that stops a linear
            # script walking on after a hop silently failed.
            for key in ("url", "download"):
                if key in expect:
                    carried[key] = expect.pop(key)
        elif carried:
            expect = {**carried, **expect}
            carried = {}
            # The click cannot share the hover's target.
            #
            # Both events resolved to the same single candidate --
            # #scroll-container > div > div:nth-of-type(5), the CARDS nav block --
            # because the recorder's candidate builder climbed to the same
            # actionable ancestor for the submenu link as for the icon box that
            # opens it. The compiled click then clicked the thing that had just
            # been hovered and nothing navigated.
            #
            # They cannot both be right: a hover opens, and the click lands on
            # what it revealed. The click's own recorded selector describes the
            # element that was actually hit, so prefer it when the candidates
            # collide. Only then -- an ordinary click keeps its chosen locator,
            # which is ranked for a reason.
            prev = steps[-1] if steps else None
            if prev is not None and prev.get("command") == "hover":
                same = (prev.get("params") or {}).get("selector")
                own = str(click.get("selector") or "").strip()
                if same and own and (step.get("params") or {}).get("selector") == same:
                    step["params"]["selector"] = own
        if expect:
            step["expect"] = expect
        steps.append(step)

    # A page reached by a PARTIAL chain must prove it arrived.
    #
    # This is what makes keeping the page safe. Without it, a chain that stops
    # short leaves the run on whatever page it managed to reach and
    # get_page_summary returns THAT -- the operation claims to read the
    # statements page and hands back the landing DOM, which is the failure the
    # old drop was protecting against. Asserting the url turns a silent wrong
    # answer into a blocked step naming the page it did not reach.
    if p.get("nav_partial") and p.get("url"):
        steps.append(
            {
                "command": "get_page_info",
                "expect": {"url": p["url"]},
                "note": "the recorded path to this page was incomplete — verify arrival",
            }
        )

    steps.append({"command": "get_page_summary"})
    out = _fold_hover_into_click(_collapse_repeats(steps))
    _inherit_hover_settle(out)
    return out


def ambiguous_steps(pages: list[dict]) -> list[dict]:
    """Steps whose locator is known to match more than one node.

    Surfaced rather than hidden: these are exactly the steps that compile
    cleanly and then misclick, and they are the first thing to re-point when a
    skill misbehaves.
    """
    out: list[dict] = []
    for p in pages:
        for click in p.get("nav") or []:
            loc = click.get("locator")
            if loc and loc.get("confidence") == AMBIGUOUS:
                out.append({"page": p["name"], "text": click.get("text") or "", "locator": loc})
    return out


def entry_urls_by_origin(url_events: list[dict]) -> dict:
    """The first page the journey reached on EACH host it visited.

    A reset must not change hosts. The recording went forward only --
    /overview -> /credit-card -> the statement portal -- and never walked back,
    so no route home across origins was ever observed and any we invent is a
    guess. Staying on the host you are already on keeps a reset to something the
    recording actually saw: the first page it reached there.

    Sign-in pages are excluded; they are not where a journey resumes.
    """
    out: dict = {}
    for ev in url_events or []:
        # from_url BEFORE to_url. The compiler is handed the workflow slice,
        # whose first transition is already /overview -> /credit-card, so
        # reading destinations alone made the net-banking entry /credit-card --
        # the page the first step is trying to REACH. The same trap entry_url
        # fell into.
        for url in (str((ev or {}).get("from_url") or ""), str((ev or {}).get("to_url") or "")):
            if not url or url == "about:blank" or _is_login_flow_url(url):
                continue
            m = re.match(r"^[a-z]+://[^/]+", url, re.I)
            if m:
                out.setdefault(m.group(0).lower(), url)
    return out


def entry_url_for(url_events: list[dict], pages: list[dict]) -> str:
    """Where the journey starts: the page the FIRST step acts on.

    Not pages[0]["url"] -- that is a DESTINATION. A page's recipe begins with
    the click chain that reached it, and that chain is performed on an earlier
    page. On a recording whose split kept /overview as a readable page the two
    happened to coincide; on one that did not, entry_url came out as
    /credit-card while every operation's step 0 was `click_by_text "Credit
    Cards"` -- a click you make FROM /overview. Resetting there and then
    clicking would land nowhere, which is the drift bug from the other side.

    So: the first page reached from the login flow -- the post-login landing
    page, which is where the session itself lands and where the chains start.
    A page whose own nav is None is that page by definition, and is preferred
    when one survived compilation.
    """
    # url_events FIRST. `nav is None` looked like a clean signal for "the
    # landing page" and is not: _resolve_nav_chains re-roots chains onto the
    # first surviving page, so when /overview was dropped, /credit-card came
    # back with nav None and the compiler stamped the destination again. The
    # recorded transitions cannot be re-rooted by anything downstream.
    # The page in effect before the FIRST transition of the slice being
    # compiled -- literally "the page the first step acts on". The compiler is
    # handed the WORKFLOW slice, whose first event is already /overview ->
    # /credit-card, so the login->landing transition is not in it and a rule
    # that looked for one fell through to the destination twice.
    for ev in url_events or []:
        frm = str((ev or {}).get("from_url") or "")
        if not frm or frm == "about:blank" or _is_login_flow_url(frm):
            continue  # the browser opening, or the sign-in nobody replays
        return frm
    for p in pages or []:
        if p.get("nav") is None and p.get("url"):
            return str(p["url"])
    return str((pages or [{}])[0].get("url") or "")


def _truncate_reads_at_terminals(operations: list[dict]) -> None:
    """A read must stop where the next goal begins.

    Page operations collect every click the human made on that page, and the
    human did TWO things on ICICI's statement page: downloaded the monthly
    statement, then switched the form to Annual and downloaded that. So
    `read_corp_finacle` spanned both -- and its steps included `set_checked
    Annual` and the GO that submits it.

    Replayed, that read performed the annual selection whatever timeframe was
    asked for, navigating to /corp/Finacle before the MONTHLY download could
    run. Monthly then either failed its guard or landed on the annual page and
    fetched the wrong document, while every step reported ok.

    The terminal is the boundary: anything a read does after another operation's
    goal completed belongs to what comes next, not to the read. The annual
    branch already carries those steps itself, so nothing is lost -- and annual,
    which works, is untouched.
    """
    terminals = sorted(
        t for t in (op.get("_terminal_seq") for op in operations) if isinstance(t, int)
    )
    if not terminals:
        return
    for op in operations:
        if op.get("_terminal_seq") is not None:
            continue
        segment = op.get("segment_steps")
        if not segment:
            continue
        seqs = [s["_seq"] for s in segment if isinstance(s.get("_seq"), int)]
        if not seqs:
            continue
        inside = [t for t in terminals if min(seqs) < t < max(seqs)]
        if not inside:
            continue
        cut = inside[0]
        kept = [
            s for s in segment
            if not (isinstance(s.get("_seq"), int) and s["_seq"] > cut)
        ]
        dropped = len(segment) - len(kept)
        if not dropped:
            continue
        op["segment_steps"] = kept
        # steps is the journey followed by the segment, so the same tail goes.
        if op.get("steps"):
            op["steps"] = op["steps"][: len(op["steps"]) - dropped]


def _recorded_position(op: dict) -> float:
    """When, in the recording, this operation happens.

    The earliest interaction any of its steps came from, or the moment its
    terminal completed -- whichever is earlier, since a terminal operation's
    lead-in steps carry no position of their own while its terminal does.

    Infinity for an operation the recording cannot place, so a stable sort keeps
    it where it was rather than moving it somewhere arbitrary.
    """
    # Its OWN work, not the journey that reaches it. `steps` carries the whole
    # chain from the landing page, which every operation shares -- so the
    # earliest step there is the same first click for all of them and orders
    # nothing.
    own = op.get("segment_steps")
    if own is None:
        own = op.get("steps") or []
    seqs = [
        step["_seq"]
        for step in own
        if isinstance(step, dict) and isinstance(step.get("_seq"), int)
    ]
    terminal = op.get("_terminal_seq")
    if isinstance(terminal, int):
        seqs.append(terminal)
    return float(min(seqs)) if seqs else float("inf")


def render_browser_operations_json(
    pages: list[dict],
    click_events: list[dict] | None = None,
    *,
    profile_slug: str,
    terminal_ops: list[dict] | None = None,
    entry_url: str = "",
    entry_by_origin: dict | None = None,
) -> str:
    """operations.json for a browser skill — a click+read recipe per page.

    Shape mirrors the harness call_web_api operations.json (schema_version +
    operations[]) so the installer and manifest routing treat it identically.
    """
    operations = []
    for p in pages:
        op = {
            "name": p["name"],
            "description": f"Read the rendered contents of {p['url']}",
            "tool": "call_web_browser",
            "profile_slug": profile_slug,
            "steps": _steps_for_page(p),
        }
        # The segment: the page this operation starts on, and only the hop from
        # it. `steps` above repeats the whole journey into every operation,
        # which is why replaying operation 2 demands being back at a landing
        # page the recording left once and never returned to -- and why the
        # replay grew a reset that has to invent a way there.
        #
        # Emitted ALONGSIDE steps, not instead of them: switching the replay
        # over is a separate change, and until it happens nothing here moves.
        # Note `starts_from: null` means the landing page, where a fresh session
        # already is.
        if p.get("starts_from") is not None or p.get("segment") is not None:
            op["starts_from"] = p.get("starts_from")
            op["segment_steps"] = _steps_for_page({**p, "nav": p.get("segment")})
        # How long the page AFTER this operation took to become usable, measured
        # from the human's own gap. Stamped on the operation that CAUSES the
        # arrival, because that is the click the gap was measured from.
        last = (p.get("nav") or [None])[-1]
        budget = arrival_budget_ms(click_events or [], (last or {}).get("seq"))
        if budget:
            op["causes_arrival_budget_ms"] = budget
        operations.append(op)
    # Goals that finish without landing on a new page — a download, a form
    # submission. The page-centric model above cannot express them at all.
    for op in terminal_ops or []:
        operations.append(
            {
                "name": op["name"],
                "description": _terminal_description(op),
                "tool": "call_web_browser",
                "profile_slug": profile_slug,
                "kind": op["kind"],
                "parameters": op.get("parameters") or [],
                "steps": _steps_for_terminal(op),
                # Alongside steps, exactly as for page operations: the work this
                # operation does on its own page, and the page it starts on.
                # Nothing reads these yet -- wiring the replay to run segments in
                # recorded order is a separate change.
                "starts_from": _terminal_starts_from(op),
                "segment_steps": _terminal_segment(op),
                # Carried onto the emitted operation because folding happens in
                # a later run, reading operations.json back from disk -- the
                # recording is long gone by then.
                "_terminal_seq": op.get("_terminal_seq"),
            }
        )
    # In the order the human did them.
    #
    # These operations are ONE journey cut into pieces, and each continues from
    # the page its predecessor left behind -- so the list order IS the replay
    # order. Emitting every page operation and then every terminal one puts them
    # out of sequence: ICICI's portal sign-in (recorded at 18) landed AFTER the
    # statement-page read (20-24), which had already navigated past the page the
    # sign-in runs on, so it could only ever fail its own starts_from guard.
    #
    # Position comes from the recording: the earliest interaction an operation
    # touches, or the moment it completed. Operations with neither keep their
    # place, since a stable sort leaves equal keys alone.
    _truncate_reads_at_terminals(operations)
    operations.sort(key=_recorded_position)
    doc = {"schema_version": "1", "style": "browser", "operations": operations}
    if entry_by_origin:
        # One per host. A reset must not change hosts -- see entry_urls_by_origin.
        doc["entry_urls"] = entry_by_origin
    if entry_url:
        # Where the journey starts. These operations are one recorded journey cut
        # into pieces -- op N+1 begins on the page op N left behind -- so replaying
        # them means reproducing that journey from the page its first step acts
        # on, which is the post-login landing page and the only one reachable
        # without the steps that precede it. See entry_url_for: this is NOT the
        # first readable page, which is a destination.
        doc["entry_url"] = entry_url
    return json.dumps(doc, indent=2, ensure_ascii=False)


def render_browser_skill_md(
    *,
    skill_id: str,
    app_name: str,
    workflow_name: str,
    pages: list[dict],
    profile_slug: str,
    description_override: str = "",
    terminal_ops: list[dict] | None = None,
) -> str:
    """SKILL.md for a browser-driven skill (harness frontmatter + body)."""
    description = description_override or (
        f"Use this skill to read {workflow_name.lower()} data from {app_name} by "
        f"driving the signed-in browser session (Tabby profile `{profile_slug}`). "
        f"This app renders its data in a single-page app whose requests cannot be "
        f"replayed, so the skill reads what the page displays via the "
        f"`call_web_browser` tool rather than calling APIs directly."
    )

    def _describe(c: dict) -> str:
        text = (c.get("text") or "").strip()
        if text:
            return f'click "{text}"'
        loc = c.get("locator") or {}
        # An icon/SVG control has no label to quote; name it by how the step
        # addresses it, so the line stays readable instead of `click ""`.
        return f"click the control matching `{loc.get('kind', 'selector')}`"

    def _page_line(p: dict) -> str:
        nav = p.get("nav")
        if nav:
            clicks = " then ".join(_describe(c) for c in nav)
            how = clicks or "the page you land on after login"
        else:
            how = "the page you land on after login"
        return f"- **{p['name']}** — `{p['url']}` (reach it via {how})"

    page_lines = (
        "\n".join(_page_line(p) for p in pages)
        or "- (no data pages were captured; re-record reaching the target screen)"
    )

    # Name the ambiguous steps outright. A compile that quietly ships a locator
    # known to match several elements is the exact failure this redesign exists
    # to remove; the human re-recording is the one who can fix it.
    # Goals that produce something rather than land somewhere — a downloaded
    # file, a submitted form. Named explicitly so the agent asks for them by name
    # instead of trying to reconstruct the click path from the read pages.
    if terminal_ops:

        def _op_line(op: dict) -> str:
            head = f"- **{op['name']}** — {_terminal_description(op)}"
            params = op.get("parameters") or []
            if not params:
                return head
            bits = []
            for prm in params:
                shown = f'`{prm["name"]}` ({prm["type"]}, recorded as "{prm["default"]}")'
                if not prm.get("settable"):
                    # Honest about the gap: no browser command can set a native
                    # <select>, so the agent has to open it and pick, and being
                    # told that beats a step that silently does nothing.
                    shown += " — a dropdown; open it and choose, no step is emitted"
                bits.append(shown)
            return head + "\n  - accepts: " + "; ".join(bits)

        lines = "\n".join(_op_line(op) for op in terminal_ops)
        terminal_section = (
            "## Operations that produce a result\n\n"
            "These finish with an artifact or a submission rather than a page to "
            "read. Run the steps exactly as `operations.json` gives them; for a "
            "download the file itself is the result, reported by the closing "
            "`list_downloads` step.\n\n"
            "Where an operation lists parameters, its steps carry "
            "`{{placeholders}}` — substitute the value the user asked for before "
            "running the step. Pass nothing and the recorded value is used, "
            "which reproduces the run the skill was built from rather than what "
            "was asked for, so always check whether the request names a period, "
            "an account or a search term.\n\n"
            f"{lines}\n\n"
        )
    else:
        terminal_section = ""

    flagged = ambiguous_steps(pages)
    if flagged:
        lines = "\n".join(
            f"- `{f['page']}` — {f['locator']['kind']} "
            f"matched {f['locator']['match_count']} elements"
            + (f' (text: "{f["text"]}")' if f["text"] else "")
            for f in flagged
        )
        ambiguity_note = (
            "\n\n### Known ambiguous steps in this skill\n\n"
            f"{lines}\n\n"
            "These were recorded against a page where the control could not be "
            "pinned down uniquely. Re-record those steps if this skill misbehaves."
        )
    else:
        ambiguity_note = ""

    frontmatter = (
        "---\n"
        f"name: {skill_id}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "auth: browser\n"
        f"profile: {profile_slug}\n"
        "---\n"
    )

    body = f"""
# {app_name}

Browser-driven skill for **{app_name}**, targeting the **Adopt Agent Harness**.

This app cannot be automated by replaying HTTP requests — it encrypts request
bodies (or mints per-session headers) in the page's JavaScript, so a recorded
request is not reusable. Instead, this skill drives the live page with the
`call_web_browser` tool and reads what it renders.

## Prerequisites

An authenticated Tabby session for the **`{profile_slug}`** profile. If the
member has no live session, `call_web_browser` returns `login_required` with a
sign-in link — the harness shows the card automatically; ask the user to sign
in, then retry the SAME call with `wait_for_login: true`.

Many portals idle out within a few minutes, so read the data promptly after the
user signs in rather than exploring first.

## Reading data

Navigate the app the way a human does — **click its own menu items**, never a
full-page navigate. This app expires the session on a page reload, so a
`navigate`/goto to a data page lands on its session-expired screen; an in-app
click is a client-side route change that preserves the session.

For each readable page below:
1. If it lists a click, run the step exactly as `operations.json` gives it —
   `click_element` with the recorded selector where there is one, otherwise
   `click_by_text`. Do not substitute your own selector or text: the recorded
   one was verified to match a single control at record time.
2. `call_web_browser` with `command: "get_page_summary"` — returns the page's
   headings, links, buttons and inputs (the rendered account/card/transaction
   values live in `headings`).

## Checking each step

Steps in `operations.json` may carry an `expect` block describing what was
OBSERVED when this flow was recorded:

- `url` — the page you should be on after the click. If you are somewhere else,
  **stop and report it**. Do not keep clicking: the remaining steps were recorded
  for a different page, and continuing produces confidently wrong data.
- `settle_ms` — how long the recorded page took to finish loading, with headroom.
  Give it that long before reading rather than reading immediately.
- `download` — this step produced a file. That is the operation's success
  condition; if no download starts, the step did not work.

A step may also carry a `locator` block. When its `confidence` is `ambiguous`,
the recorded way of addressing that control matched several elements on the page,
so it may well click the wrong one — treat a surprising result there as the
likely cause, and report it rather than working around it.{ambiguity_note}

The landing page needs no click — just read it. If a value you need is not in the
summary, `command: "click_by_text"` on the relevant control (e.g. a "view all"
button) then read again, or `command: "screenshot"` to inspect visually. Do NOT
use `command: "navigate"` on this app.

## Readable pages

{page_lines}

{terminal_section}The same recipes are machine-readable in `operations.json`.

<!-- custom:start:notes -->
<!-- Add skill-specific notes here; this region survives re-compilation. -->
<!-- custom:end:notes -->
"""
    return frontmatter + body


def generate_browser_skill(
    *,
    app_slug: str,
    app_name: str,
    workflow_name: str,
    profile_slug: str,
    url_events: list[dict],
    login_url: str,
    output_dir: str,
    click_events: list[dict] | None = None,
    session_id: str = "",
    start_url: str = "",
    description_override: str = "",
    bundle: dict | None = None,
    bundle_file: str = "",
) -> dict:
    """Compile a recording into an installable browser-driven skill directory.

    Writes SKILL.md, operations.json and manifest.json in the same shape as a
    harness call_web_api skill, so install_skill and the manifest→operations
    routing treat it identically; only runtime.operation_style ("browser") and
    the tool the operations name ("call_web_browser") differ.

    Raises ValueError when the recording yields no readable data page — a
    browser skill with nothing to read is never worth installing, and emitting
    it silently is exactly the "40 operations that all 403" failure in a new
    disguise.

    Returns the manifest dict.
    """
    if not profile_slug:
        raise ValueError(
            "generate_browser_skill requires a profile_slug: a browser skill "
            "drives an authenticated Tabby session, which cannot exist without a "
            "bound profile."
        )

    pages = derive_browser_pages(url_events, click_events or [], login_url=login_url)
    terminal_ops = derive_terminal_operations(pages, click_events or [], login_url=login_url)
    if not pages:
        raise ValueError(
            "No readable data page was captured for this browser skill — the "
            "recording never left the login/auth flow (every page was a login, "
            "OTP, or one-shot-token URL). Re-record reaching the screen whose "
            "data you want to read, then compile again."
        )

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    skill_md = render_browser_skill_md(
        skill_id=app_slug,
        app_name=app_name,
        workflow_name=workflow_name,
        pages=pages,
        profile_slug=profile_slug,
        description_override=description_override,
        terminal_ops=terminal_ops,
    )
    (out_path / "SKILL.md").write_text(skill_md, encoding="utf-8")

    operations_json = render_browser_operations_json(
        pages,
        profile_slug=profile_slug,
        terminal_ops=terminal_ops,
        click_events=click_events,
        entry_url=entry_url_for(url_events, pages),
        entry_by_origin=entry_urls_by_origin(url_events),
    )
    (out_path / "operations.json").write_text(operations_json, encoding="utf-8")

    op_entries = [
        {
            "name": p["name"],
            "description": f"Read the rendered contents of {p['url']}",
            "recipe": "operations.json",
            "tool": "call_web_browser",
            "url": p["url"],
        }
        for p in pages
    ] + [
        {
            "name": op["name"],
            "description": _terminal_description(op),
            "recipe": "operations.json",
            "tool": "call_web_browser",
            "url": op["url"],
        }
        for op in terminal_ops
    ]

    manifest: dict = {
        "schema_version": "1",
        "skill_id": app_slug,
        "app": {"name": app_name, "slug": app_slug},
        "workflow": {
            "id": app_slug,
            "name": workflow_name,
            "workflow_session_id": session_id,
            "start_url": start_url,
        },
        "auth": {
            "requires_auth": True,
            "profile_slug": profile_slug,
            "strategy": "tabby_browser",
            "execution_strategy": "harness_call_web_browser",
        },
        "runtime": {
            "type": "agent-harness-skill",
            "entrypoint": "SKILL.md",
            "operation_style": "browser",
        },
        "operations": op_entries,
        "artifacts": {
            "skill_file": "SKILL.md",
            "files": ["SKILL.md", "operations.json", "manifest.json"],
        },
        "generation": {
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": "noui",
            "generator_version": "v1-browser-skill",
        },
    }
    # Bind the skill to the recording it came from. The installer verifies this
    # against the bundle itself, which is the only artifact here that a skill
    # written from imagination cannot produce. See compile/provenance.py.
    if bundle is not None:
        from noui_core.compile import provenance as _provenance

        # Write the exact bundle that was compiled, beside the skill. Pointing at
        # the saved capture instead would not survive a combined import, where the
        # bundle is split and only a HALF reaches this compiler — the digest would
        # never match the file on disk and every real skill would be rejected.
        (out_path / _provenance.BUNDLE_FILE).write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        manifest["provenance"] = _provenance.build(
            bundle,
            bundle_file=bundle_file or _provenance.BUNDLE_FILE,
            operations=json.loads(operations_json).get("operations") or [],
        )
    (out_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


# --- Terminal operations ------------------------------------------------------
#
# An operation used to mean "a page the human visited": derive_browser_pages
# walks url_events and compiles each distinct page as click-chain →
# get_page_summary. That model can only express READING, so anything whose
# result is not a new URL has no way to become an operation at all —
# a statement download (an in-app click producing a blob:, no navigation), a
# form submission, a filtered export. On ICICI the compiler produced two read
# operations and the download had to be hand-written afterwards, which is not
# reproducible and not verifiable.
#
# Tabby's recorder now reports what each interaction CAUSED, so the compiler can
# work from what the human accomplished rather than from where they went.
# Downloads are one kind of terminal outcome here, not a special case.

#: Interactions that end a goal, and what to call the operation that reaches them.
_TERMINAL_KINDS = ("download", "submit")


def _terminal_kind(ev: dict) -> str | None:
    """What goal, if any, this interaction completed.

    Deliberately narrow. "Fired some XHRs" describes half the clicks on a bank
    portal — filters, toggles, accordions — and emitting an operation for each
    would bury the two or three a user would actually ask for. Only a finished
    artifact and a submitted form count.
    """
    outcome = ev.get("outcome")
    if isinstance(outcome, dict) and outcome.get("download"):
        return "download"
    if (ev.get("event_type") or "") == "submit":
        return "submit"
    return None


def _op_name(kind: str, ev: dict, page_slug: str) -> str:
    """A readable, stable operation name.

    Prefers the control's own label ("Download statement" -> download_statement)
    over the page slug, because the label is what the user will ask for.
    """
    label = (ev.get("text_content") or "").strip()
    if not label:
        loc = choose_locator(ev.get("candidates"))
        if loc and not loc.get("is_css"):
            value = loc["value"]
            label = value.split("|", 1)[1] if loc["kind"] == "role_name" and "|" in value else value
    stem = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") if label else ""
    if not stem:
        stem = page_slug or "action"
    if not stem.startswith(kind):
        stem = f"{kind}_{stem}"
    return stem[:60].strip("_")


def derive_terminal_operations(
    pages: list[dict],
    click_events: list[dict] | None,
    *,
    login_url: str,
) -> list[dict]:
    """Operations for goals that finish WITHOUT landing on a new page.

    Each is the click chain that reaches the page the interaction happened on
    (reusing the resolved page navigation, so the session is never reloaded),
    then the interaction itself, then its recorded success condition.

    Returns [] for any recording whose interactions carry no ``outcome`` — every
    bundle captured before Tabby schema_version 5 — so older captures compile
    exactly as they did.
    """
    click_events = order_events(click_events)
    term_opener_seqs = _opener_seqs(click_events)
    # Same multi-origin rule as the pages themselves: an interaction on the
    # app's second host (ICICI's statement portal) is still this app's.
    app_origins = {_url_origin(p["url"]) for p in pages}
    if login_url:
        app_origins.add(_url_origin(login_url))
    app_origins.discard("")
    by_key = {_page_key(p["url"]): p for p in pages}
    out: list[dict] = []
    seen: set[str] = set()

    for ev in click_events or []:
        kind = _terminal_kind(ev)
        if kind is None:
            continue
        url = ev.get("url") or ""
        if not url or (app_origins and _url_origin(url) not in app_origins):
            continue
        if _is_login_flow_url(url):
            continue

        # Where did this happen, and how does the skill get there? An interaction
        # on a page the compiler never resolved is unreachable, and a step that
        # cannot be reached is worse than a missing one.
        page = by_key.get(_page_key(url))
        if page is None:
            continue

        step = _step_for_click(
            {
                "text": (ev.get("text_content") or "").strip(),
                "locator": choose_locator(ev.get("candidates")),
                "candidates": ev.get("candidates"),
                "outcome": ev.get("outcome"),
            }
        )
        if step is None:
            continue
        expect = _expect_for_click({"outcome": ev.get("outcome")})
        if expect:
            step["expect"] = expect

        # The values the human entered on this page BEFORE acting are what the
        # operation should accept as arguments — a statement period, an account,
        # a search term. Bounded to the same page and to interactions preceding
        # the terminal one, so a later screen's fields never leak in.
        term_seq = event_seq(ev)
        # The page the WORK happened on, which is not always the operation's
        # url. A download click navigates: download_corp_finacle was keyed to
        # /corp/Finacle, where the navigation landed, while its terminal click
        # and every step that decides WHAT is downloaded -- the Annual radio,
        # the GO button, the PDF icon -- happened on the statement page before
        # it. Filtering by the landing page dropped all of them into `nav`,
        # where the segment builder strips them.
        #
        # The consequence was not a failing step. Without the Annual radio the
        # form stays on Monthly, so `timeframe=annual` picked a different month
        # and returned a MONTHLY statement, with every check passing.
        #
        # Naming still uses the operation's url, so no name changes here.
        work_url = str(ev.get("url") or url)
        page_inputs = [
            c
            for c in click_events or []
            if (c.get("event_type") or "") in ("input", "change")
            and _page_key(c.get("url") or "") == _page_key(work_url)
            and (term_seq is None or (event_seq(c) or 0) < term_seq)
        ]
        parameters = derive_parameters(page_inputs)

        # The clicks the human made ON THIS PAGE before the terminal one.
        #
        # Only the final click was kept, so a statement download compiled to
        # "click Download" alone -- losing the "Past Statements" tab and the
        # "Annual" period that decide WHAT is downloaded. At replay the click
        # landed on whatever the page happened to show, which is how a run ended
        # up hunting a control that was one tab away.
        #
        # Bounded to the same page and to before the terminal interaction, so a
        # later screen's clicks never leak in. Inputs are excluded: those become
        # parameters above, and replaying them as clicks would fight the values
        # the caller passes.
        lead_steps: list[dict] = []
        for c in click_events or []:
            # A hover that opened a menu is part of the gesture, not noise.
            if (c.get("event_type") or "click") not in ("click", "hover"):
                continue
            if _page_key(c.get("url") or "") != _page_key(work_url):
                continue
            c_seq = event_seq(c)
            if term_seq is None or c_seq is None or c_seq >= term_seq:
                continue
            lead = _step_for_click(
                {
                    "text": (c.get("text_content") or "").strip(),
                    "locator": choose_locator(c.get("candidates")),
                    "candidates": c.get("candidates"),
                    "is_opener": event_seq(c) in term_opener_seqs,
                    "outcome": c.get("outcome"),
                }
            )
            if lead is None:
                continue
            lead_expect = _expect_for_click({"outcome": c.get("outcome")})
            if lead_expect:
                lead["expect"] = lead_expect
            lead_steps.append(lead)

        name = _op_name(kind, ev, _slug_from_path(url))
        if name in seen:
            continue
        seen.add(name)

        out.append(
            {
                "name": name,
                "kind": kind,
                "url": url,
                # WHEN it finished. Two variants recorded one after the other
                # share a journey, and the boundary between them is the moment
                # the first one completed -- which for ICICI is a `submit` event
                # that never becomes a step, so no comparison of the steps
                # themselves can find it. Recorded order can.
                "_terminal_seq": term_seq,
                # Where its work happens -- what starts_from must name.
                "work_url": work_url,
                # Reach the page exactly the way the read operation for it does.
                "nav": list(page.get("nav") or []),
                "parameters": parameters,
                "lead": lead_steps,
                "terminal": step,
            }
        )
    return out


def _terminal_segment(op: dict) -> list[dict]:
    """A terminal operation's own work, without the journey that reaches it.

    `_steps_for_terminal` prefixes the whole nav chain from the landing page, so
    a download repeats four clicks the previous operation already made. The
    segment is what actually happens ON the page: the lead-in that sets it up
    (tab, period, filter), the parameter fills, and the terminal action itself.

    Built by the real recipe with the journey removed, not by re-listing its
    parts: a hand-mirrored version already dropped the trailing list_downloads
    that confirms a download actually arrived, which is the operation's whole
    success condition.
    """
    return _steps_for_terminal({**op, "nav": []})


def _terminal_starts_from(op: dict) -> str | None:
    """The page a terminal operation begins on: where its nav chain arrived.

    None means the landing page -- a terminal reached without any nav, which a
    fresh session is already positioned for.
    """
    # The operation's OWN page, which it already carries. Walking the nav chain
    # for a recorded outcome was wrong: a cross-origin hop completes after the
    # click returns, so it is never stamped on that click, and the walk fell
    # back to the last hop that WAS stamped. download_statement came out
    # starting from /credit-card while it runs on the statement portal, and its
    # starts_from guard refused it every time.
    url = str(op.get("work_url") or op.get("url") or "").strip()
    return url or None


def _steps_for_terminal(op: dict) -> list[dict]:
    """Recipe for a terminal operation: reach the page, then do the thing."""
    steps: list[dict] = []
    for click in op.get("nav") or []:
        step = _stamp_source(_step_for_click(click), click)
        if step is None:
            continue
        expect = _expect_for_click(click)
        if expect:
            step["expect"] = expect
        steps.append(step)
    # Values the caller can override. Templated on the parameter name, so the
    # operation does exactly what was recorded when nothing is passed.
    # Set the page up the way the human did — tab, period, filter — before the
    # values the caller can override, so a passed parameter lands on the screen
    # those clicks produced rather than on whatever loaded first.
    steps.extend(op.get("lead") or [])
    steps.extend(fill_steps(op.get("parameters") or []))
    # The terminal asserts WHICH PAGE it fires on.
    #
    # ICICI's annual and monthly statement pages are identically designed:
    # #PDF_Download and #DOWNLOAD_ESTATEMENT_PDF exist on both and behave the
    # same. So the monthly steps run happily on either and return whatever that
    # page is showing -- timeframe=annual returned a MONTHLY statement with
    # every check green, because every check we had looked at selectors.
    #
    # The pages differ in one thing the recording captured: their URL. The
    # annual flow is reached by a radio click that navigates to /corp/Finacle;
    # monthly downloads happen on /corp/AuthenticationController. Asserting the
    # page the terminal was RECORDED on turns a silent wrong document into a
    # failed postcondition, which expectation_unmet already enforces.
    terminal = dict(op["terminal"])
    page_url = str(op.get("work_url") or op.get("url") or "").strip()
    if page_url:
        expect = dict(terminal.get("expect") or {})
        expect.setdefault("url", page_url)
        terminal["expect"] = expect
    steps.append(terminal)
    steps = _collapse_repeats(steps)
    if op.get("kind") == "download":
        # The artifact IS the result, so the operation ends by naming it rather
        # than by reading the page it left behind.
        steps.append({"command": "list_downloads"})
    else:
        steps.append({"command": "get_page_summary"})
    steps = _fold_hover_into_click(steps)
    _inherit_hover_settle(steps)
    return steps


def _terminal_description(op: dict) -> str:
    if op.get("kind") == "download":
        return f"Download the file produced from {op['url']}"
    return f"Submit the form on {op['url']} and read the result"


def _behaviour(step: dict) -> dict:
    """A step stripped of provenance -- what it DOES, not where it came from.

    Underscore-prefixed keys record which interaction a step was compiled from.
    Two operations that perform the same action perform the same action whether
    or not it was recorded at the same moment: monthly and annual both end by
    clicking Download, from different clicks, and comparing the raw dicts would
    stop that being recognised as shared and duplicate it into both branches.
    """
    return {k: v for k, v in step.items() if not k.startswith("_")}


def _common_prefix_len(a: list[dict], b: list[dict]) -> int:
    """How many leading steps two operations perform identically."""
    n = 0
    for x, y in zip(a, b):
        if _behaviour(x) != _behaviour(y):
            break
        n += 1
    return n


def _common_suffix_len(a: list[dict], b: list[dict], skip: int) -> int:
    """How many trailing steps match, without eating into the shared prefix."""
    n = 0
    limit = min(len(a), len(b)) - skip
    while n < limit and _behaviour(a[len(a) - 1 - n]) == _behaviour(b[len(b) - 1 - n]):
        n += 1
    return n


#: A fold has to rest on a real shared journey, not a coincidence. Two steps in
#: common is the sort of overlap any two operations on one site have -- both open
#: with the same nav click -- and folding on that would merge workflows that have
#: nothing to do with each other.
_MIN_FOLD_PREFIX = 3


def fold_candidates(operations: list[dict]) -> list[dict]:
    """Operations that are one workflow with a choice in the middle.

    ICICI's monthly and annual statements share four steps to reach the download
    page -- hover the nav, Credit Cards, Past, download previous statement -- then
    diverge: annual clicks a radio and picks a financial year, monthly does not.
    They also END the same way, on the download button and list_downloads. The
    compiler emits them as two operations, each replaying the whole journey, so
    every one of them needs the browser returned to the landing page first.

    Reported, not applied. What the divergent branches should be CALLED is a
    judgement this function cannot make: "corp_finacle" is the recorded page
    name and means nothing to a caller choosing between monthly and annual. A
    fold that emits a meaningless parameter is worse than two honest operations.

    Returns one entry per foldable pair::

        {"operations": [nameA, nameB], "prefix": int, "suffix": int,
         "branches": [[steps...], [steps...]]}
    """
    out: list[dict] = []
    # `operations` is a count in some result shapes, not a list of steps -- and
    # a report that raises would fail an import that otherwise succeeded.
    ops = [
        o for o in operations or [] if isinstance(o, dict) and isinstance(o.get("steps"), list)
    ]
    for i, a in enumerate(ops):
        for b in ops[i + 1 :]:
            if (a.get("kind") or "read") != (b.get("kind") or "read"):
                continue
            sa, sb = a.get("steps") or [], b.get("steps") or []
            pre = _common_prefix_len(sa, sb)
            if pre < _MIN_FOLD_PREFIX:
                continue
            suf = _common_suffix_len(sa, sb, pre)
            if not suf:
                # No shared ending means they do different things, however
                # similarly they start.
                continue
            mid_a, mid_b = sa[pre : len(sa) - suf], sb[pre : len(sb) - suf]
            if not mid_a and not mid_b:
                continue  # identical operations; a fold is not what they need
            out.append(
                {
                    "operations": [a.get("name"), b.get("name")],
                    "prefix": pre,
                    "suffix": suf,
                    "branches": [mid_a, mid_b],
                }
            )
    return out


def _journey_part(op: dict) -> list[dict]:
    """The steps that reach this operation, before its own work begins."""
    steps = op.get("steps") or []
    segment = op.get("segment_steps")
    if segment is None:
        return []
    return steps[: max(0, len(steps) - len(segment))]


def _after_last_terminal(steps: list[dict], boundary_seq: int | None = None) -> list[dict]:
    """Drop everything up to and including a completed terminal action.

    A finished action is a boundary. The human downloaded the MONTHLY statement,
    then switched the period to Annual and downloaded again -- so the clicks
    between the fork and the annual terminal include the monthly download that
    ended the previous variant. Handed to the annual branch, it fired a monthly
    download first, left the form busy ("the system is considering your first
    request"), and the annual steps had nothing to act on.

    What precedes a completed terminal belongs to the variant it completed, not
    to the one that follows.

    Two markers, because a terminal is not always visible in the steps. A step
    that carries the download itself says so. ICICI's did not: the download was
    reported against the `submit` the click triggered, and submits are not
    replayable steps, so nothing in this list can show where the monthly
    statement finished. `boundary_seq` is that moment in recorded order, and any
    step at or before it was that variant's work.
    """
    last = -1
    for i, step in enumerate(steps):
        seq = step.get("_seq")
        carries_download = bool((step.get("expect") or {}).get("download"))
        confirms_download = step.get("command") == "list_downloads"
        within_earlier_variant = (
            boundary_seq is not None and seq is not None and seq <= boundary_seq
        )
        if carries_download or confirms_download or within_earlier_variant:
            last = i
    return steps[last + 1 :] if last >= 0 else steps


def _preceding_terminal(mine: int | None, theirs: int | None) -> int | None:
    """The other variant's terminal, if it completed BEFORE this one's.

    Only a terminal already in the past can bound this branch. Applied the other
    way round it would read the whole journey as belonging to a variant that had
    not happened yet and drop every step the earlier branch needs -- which is
    the same "everything passed, nothing ran" failure this rule exists to stop.
    """
    if mine is None or theirs is None or theirs >= mine:
        return None
    return theirs


def _branch_work(a: dict, b: dict) -> tuple[list[dict], list[dict]]:
    """Each operation's own work, including the journey only IT takes.

    The shared head of the two journeys is the way to the fork and stays out of
    both branches -- the operation before them already walks it. The tail that
    differs is the branch selecting itself.
    """
    a_journey, b_journey = _journey_part(a), _journey_part(b)
    shared = _common_prefix_len(a_journey, b_journey)
    a_term, b_term = a.get("_terminal_seq"), b.get("_terminal_seq")
    return (
        _after_last_terminal(a_journey[shared:], _preceding_terminal(a_term, b_term))
        + (a.get("segment_steps") or []),
        _after_last_terminal(b_journey[shared:], _preceding_terminal(b_term, a_term))
        + (b.get("segment_steps") or []),
    )


def _merge_branches(a_steps: list[dict], b_steps: list[dict], param: str,
                    values: list[str]) -> list[dict]:
    """One list: shared prefix, each divergent middle tagged, shared ending.

    Used for BOTH `steps` and `segment_steps`. Folding only the former left the
    segment holding operation A's copy, so a segmented replay ran the monthly
    branch whatever timeframe was asked for -- the skill offered a choice it
    could not honour, which is worse than not folding at all.
    """
    pre = _common_prefix_len(a_steps, b_steps)
    suf = _common_suffix_len(a_steps, b_steps, pre)
    merged = list(a_steps[:pre])
    for branch, value in zip(
        (a_steps[pre : len(a_steps) - suf], b_steps[pre : len(b_steps) - suf]), values
    ):
        merged.extend({**step, "when": {param: value}} for step in branch)
    if suf:
        merged.extend(a_steps[len(a_steps) - suf :])
    return merged


def apply_fold(
    operations: list[dict], fold: dict, *, name: str, param: str, values: list[str]
) -> list[dict]:
    """Replace a foldable pair with one operation the member has named.

    The names come from a person, not from the recording: "corp_finacle" is the
    page the annual statement happened to be served from, and a caller choosing
    between a monthly and an annual statement can make nothing of it. The
    compiler finds the fold; the member says what it is.

    The shared prefix and ending run for every variant; each divergent middle is
    tagged with ``when`` so only its own steps run. Nothing is invented -- every
    step is the one that was recorded, in the order it was recorded.
    """
    a_name, b_name = fold["operations"]
    by_name = {o.get("name"): o for o in operations}
    a, b = by_name.get(a_name), by_name.get(b_name)
    if a is None or b is None or len(values) != 2:
        return operations

    # The shared way to the fork, then each branch's own work -- the same
    # decomposition the segment uses below, so the two cannot disagree. Merging
    # the two full step lists directly instead left `steps` holding the monthly
    # download at the head of the annual branch while `segment_steps` had
    # correctly dropped it: one artifact describing the journey two ways, and
    # the wrong one is the fallback for anything not running segments.
    a_journey, b_journey = _journey_part(a), _journey_part(b)
    shared = a_journey[: _common_prefix_len(a_journey, b_journey)]
    work = _branch_work(a, b)
    # Only a segmented operation can be split this way -- the journey is
    # recovered by subtracting the segment, so without one there is nothing to
    # subtract and the decomposition yields an empty skill. Those fold exactly
    # as they always did.
    segmented = a.get("segment_steps") is not None and b.get("segment_steps") is not None
    starts = [a.get("starts_from"), b.get("starts_from")]
    merged_steps = (
        shared + _merge_branches(*work, param, values)
        if segmented
        else _merge_branches(a.get("steps") or [], b.get("steps") or [], param, values)
    )

    folded = {
        **a,
        "name": name,
        "steps": merged_steps,
        # The segment gets the same treatment, PLUS the part of each journey
        # that only its own branch takes.
        #
        # A step can both navigate and belong to one branch. ICICI's annual
        # statement is selected by clicking a radio and pressing GO -- and that
        # submit navigates to /corp/Finacle, so both steps were classified as
        # journey and stripped from the segment. Nothing else performed them:
        # the monthly branch diverges before, and no operation sits between. So
        # timeframe=annual ran the monthly steps on the monthly page and
        # returned a MONTHLY statement.
        #
        # Each operation's steps are its journey followed by its segment, so the
        # journey part is recoverable by length. Whatever the two journeys share
        # is the way to the fork; whatever they do not is the branch choosing
        # itself, and belongs with the branch.
        "segment_steps": _merge_branches(*work, param, values),
        # Each variant begins where ITS OWN work happens.
        #
        # The folded operation inherits its scalar fields from A, and
        # `starts_from` is not shared: ICICI's monthly statement is produced on
        # /corp/AuthenticationController, and choosing Annual navigates to
        # /corp/Finacle -- so the annual variant carried the monthly page as its
        # precondition and its guard refused it every time, on the page it was
        # supposed to run on.
        #
        # Only when the two actually differ, so nothing changes for a fold whose
        # variants share a page.
        **(
            {"starts_from_by": {"param": param, "by": dict(zip(values, starts))}}
            if len(set(starts)) > 1
            else {}
        ),
        "parameters": [
            *(a.get("parameters") or []),
            {
                "name": param,
                "default": values[0],
                "allowed": list(values),
                "description": f"Which variant to run: {', '.join(values)}.",
            },
        ],
    }
    out: list[dict] = []
    for op in operations:
        if op.get("name") == a_name:
            out.append(folded)
        elif op.get("name") == b_name:
            continue
        else:
            out.append(op)
    return out
