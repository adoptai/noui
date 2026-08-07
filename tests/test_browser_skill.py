"""Tests for browser-driven skill compilation (noui_core.compile.browser_skill).

A browser skill reads DATA PAGES via call_web_browser, for apps whose requests
cannot be replayed (per-request in-page encryption / per-session headers). These
tests pin the page-selection filtering and the emitted operations/SKILL.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.compile.browser_skill import (  # noqa: E402
    derive_browser_pages,
    render_browser_operations_json,
    render_browser_skill_md,
)

_H = "https://retailnetbanking.icici.bank.in"
# The click the ICICI_EVENTS comment describes. It has to actually exist: a
# non-landing page whose driving click can't be recovered is now DROPPED rather
# than silently compiled into a recipe that reads the landing page instead.
ICICI_CLICKS = [
    {
        "event_type": "click",
        "url": f"{_H}/overview",
        "text_content": "Credit Cards",
        "timestamp": "2026-08-04T22:17:29.000Z",
    },
]

ICICI_EVENTS = [
    {"from_url": "about:blank", "to_url": f"{_H}/login-page", "timestamp": "2026-08-04T22:16:24Z"},
    # login auto-redirect to the landing page (no click) → landing, no nav
    {
        "from_url": f"{_H}/login-page",
        "to_url": f"{_H}/overview",
        "timestamp": "2026-08-04T22:17:24Z",
    },
    # in-app click drove this route change → nav = click "Credit Cards"
    {
        "from_url": f"{_H}/overview",
        "to_url": f"{_H}/credit-card",
        "timestamp": "2026-08-04T22:17:29.485Z",
    },
    # same page, different query — one readable page
    {
        "from_url": f"{_H}/credit-card",
        "to_url": f"{_H}/credit-card?tab=statements",
        "timestamp": "2026-08-04T22:17:40Z",
    },
    # third-party widget/telemetry origin — must NOT be treated as a data page
    {
        "from_url": f"{_H}/credit-card",
        "to_url": "https://www.icici.bank.in/analytics",
        "timestamp": "2026-08-04T22:17:41Z",
    },
    # one-shot token in the query — navigating here later lands on an error
    {
        "from_url": f"{_H}/credit-card",
        "to_url": f"{_H}/pay?token=ONESHOTTOKEN123456",
        "timestamp": "2026-08-04T22:17:45Z",
    },
]
ICICI_CLICKS = [
    {
        "event_type": "click",
        "text_content": "Credit Cards",
        "selector": "div.submenu-text",
        "url": f"{_H}/overview",
        "timestamp": "2026-08-04T22:17:29.461Z",
    },
]
LOGIN = f"{_H}/login-page"


def test_derive_pages_keeps_only_readable_app_pages():
    pages = derive_browser_pages(ICICI_EVENTS, ICICI_CLICKS, login_url=LOGIN)
    assert [p["name"] for p in pages] == ["read_overview", "read_credit_card"]
    assert pages[1]["url"] == "https://retailnetbanking.icici.bank.in/credit-card"


def test_derive_pages_attaches_in_app_click_not_reload():
    # The landing page (post-login redirect) needs no nav; a page reached by an
    # in-app click carries that click — so the skill routes there WITHOUT a reload
    # (the ICICI failure: the skill's own navigate/goto expired the session).
    pages = derive_browser_pages(ICICI_EVENTS, ICICI_CLICKS, login_url=LOGIN)
    overview, credit = pages[0], pages[1]
    assert overview["nav"] is None
    assert [c["text"] for c in credit["nav"]] == ["Credit Cards"]


def test_derive_pages_captures_full_click_chain_for_submenu():
    # ICICI's nav is an accordion: expand "Cards" (no URL change), then click
    # "Credit Card" (routes). Both clicks must be emitted in order so the skill can
    # open the menu — keying off the route-change alone captures only the child and
    # the compiled skill can never reach the statement (the observed run: the model
    # clicked "Cards" then flailed).
    events = [
        {
            "from_url": "about:blank",
            "to_url": f"{_H}/login-page",
            "timestamp": "2026-08-04T22:16:24Z",
        },
        {
            "from_url": f"{_H}/login-page",
            "to_url": f"{_H}/overview",
            "timestamp": "2026-08-04T22:17:00Z",
        },
        {
            "from_url": f"{_H}/overview",
            "to_url": f"{_H}/credit-card",
            "timestamp": "2026-08-04T22:17:30Z",
        },
    ]
    clicks = [
        {
            "event_type": "click",
            "text_content": "Cards",
            "selector": "a.mb-0",
            "url": f"{_H}/overview",
            "timestamp": "2026-08-04T22:17:25Z",
        },  # expand parent
        {
            "event_type": "click",
            "text_content": "Credit Card",
            "selector": "a.submenu",
            "url": f"{_H}/overview",
            "timestamp": "2026-08-04T22:17:29Z",
        },  # navigate child
    ]
    pages = derive_browser_pages(events, clicks, login_url=LOGIN)
    credit = next(p for p in pages if p["name"] == "read_credit_card")
    assert [c["text"] for c in credit["nav"]] == ["Cards", "Credit Card"]
    # operations emit a click per gesture step, in order, then read
    doc = json.loads(render_browser_operations_json(pages, profile_slug="icici-credit-card"))
    ops = {o["name"]: o for o in doc["operations"]}
    assert [s["command"] for s in ops["read_credit_card"]["steps"]] == [
        "click_by_text",
        "click_by_text",
        "get_page_summary",
    ]
    assert [
        s["params"]["text"]
        for s in ops["read_credit_card"]["steps"]
        if s["command"] == "click_by_text"
    ] == ["Cards", "Credit Card"]


def test_nav_gesture_window_excludes_stale_clicks():
    # A click made long before the navigation (outside the gesture window) is not
    # part of the menu path and must be dropped, not emitted as a spurious step.
    events = [
        {
            "from_url": f"{_H}/login-page",
            "to_url": f"{_H}/overview",
            "timestamp": "2026-08-04T22:00:00Z",
        },
        {
            "from_url": f"{_H}/overview",
            "to_url": f"{_H}/credit-card",
            "timestamp": "2026-08-04T22:17:30Z",
        },
    ]
    clicks = [
        {
            "event_type": "click",
            "text_content": "Some banner",
            "url": f"{_H}/overview",
            "timestamp": "2026-08-04T22:05:00Z",
        },  # 12 min before the nav → stale, not the gesture
        {
            "event_type": "click",
            "text_content": "Cards",
            "url": f"{_H}/overview",
            "timestamp": "2026-08-04T22:17:25Z",
        },
        {
            "event_type": "click",
            "text_content": "Credit Card",
            "url": f"{_H}/overview",
            "timestamp": "2026-08-04T22:17:29Z",
        },
    ]
    pages = derive_browser_pages(events, clicks, login_url=LOGIN)
    credit = next(p for p in pages if p["name"] == "read_credit_card")
    assert [c["text"] for c in credit["nav"]] == ["Cards", "Credit Card"]  # banner dropped


def test_derive_pages_drops_login_flow_even_with_suffix():
    # /login-page and /signin_v2 both carry a login token as a prefix; a bare
    # membership test misses them, and ICICI's real pages sit beside them.
    events = [
        {"to_url": "https://b.test/login-page"},
        {"to_url": "https://b.test/signin_v2"},
        {"to_url": "https://b.test/otp-verify"},
        {"to_url": "https://b.test/dashboard"},
    ]
    pages = derive_browser_pages(events, login_url="https://b.test/login-page")
    assert [p["name"] for p in pages] == ["read_dashboard"]


def test_derive_pages_empty_when_recording_never_left_login():
    events = [
        {"to_url": "https://b.test/login-page"},
        {"to_url": "https://b.test/login-page/otp"},
    ]
    assert derive_browser_pages(events, login_url="https://b.test/login-page") == []


def test_derive_pages_respects_max():
    events = [{"to_url": f"https://b.test/p{i}"} for i in range(30)]
    pages = derive_browser_pages(events, login_url="https://b.test/home", max_pages=5)
    assert len(pages) == 5


def test_operations_json_uses_clicks_never_navigate():
    pages = derive_browser_pages(ICICI_EVENTS, ICICI_CLICKS, login_url=LOGIN)
    doc = json.loads(render_browser_operations_json(pages, profile_slug="icici-credit-card"))
    assert doc["style"] == "browser"
    ops = {o["name"]: o for o in doc["operations"]}
    assert all(o["tool"] == "call_web_browser" for o in doc["operations"])
    # landing page: read only, no navigation
    assert [s["command"] for s in ops["read_overview"]["steps"]] == ["get_page_summary"]
    # in-app page: click_by_text then read — a client-side route change, no reload
    assert [s["command"] for s in ops["read_credit_card"]["steps"]] == [
        "click_by_text",
        "get_page_summary",
    ]
    assert ops["read_credit_card"]["steps"][0]["params"]["text"] == "Credit Cards"
    # a full-page navigate/goto must NEVER be emitted (it reloads → session expiry)
    all_cmds = [st["command"] for o in doc["operations"] for st in o["steps"]]
    assert "navigate" not in all_cmds


def test_skill_md_declares_browser_auth_and_tool():
    pages = derive_browser_pages(ICICI_EVENTS, ICICI_CLICKS, login_url=LOGIN)
    md = render_browser_skill_md(
        skill_id="icici-credit-card",
        app_name="Icici Credit Card",
        workflow_name="credit card statement",
        pages=pages,
        profile_slug="icici-credit-card",
    )
    assert "auth: browser" in md
    assert "profile: icici-credit-card" in md
    assert "call_web_browser" in md
    assert "get_page_summary" in md
    # the readable pages are listed
    assert "read_credit_card" in md
    # it does NOT claim to replay APIs
    assert "call_web_api" not in md


def test_generate_browser_skill_writes_installable_dir(tmp_path):
    from noui_core.compile.browser_skill import generate_browser_skill

    manifest = generate_browser_skill(
        app_slug="icici-credit-card",
        app_name="Icici Credit Card",
        workflow_name="credit card statement",
        profile_slug="icici-credit-card",
        url_events=ICICI_EVENTS,
        click_events=ICICI_CLICKS,
        login_url=LOGIN,
        output_dir=str(tmp_path),
        session_id="sess-1",
        start_url=LOGIN,
    )
    # three files written
    for f in ("SKILL.md", "operations.json", "manifest.json"):
        assert (tmp_path / f).exists(), f
    # manifest routes it as a harness browser skill
    assert manifest["runtime"]["operation_style"] == "browser"
    assert manifest["runtime"]["type"] == "agent-harness-skill"
    assert manifest["auth"]["execution_strategy"] == "harness_call_web_browser"
    assert [o["name"] for o in manifest["operations"]] == ["read_overview", "read_credit_card"]
    assert all(o["tool"] == "call_web_browser" for o in manifest["operations"])


def test_generate_refuses_when_no_readable_page(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="never left the login"):
        from noui_core.compile.browser_skill import generate_browser_skill

        generate_browser_skill(
            app_slug="x",
            app_name="X",
            workflow_name="w",
            profile_slug="x-prof",
            url_events=[{"to_url": "https://b.test/login-page"}],
            login_url="https://b.test/login-page",
            output_dir=str(tmp_path),
        )


def test_generate_refuses_without_profile(tmp_path):
    import pytest
    from noui_core.compile.browser_skill import generate_browser_skill

    with pytest.raises(ValueError, match="requires a profile_slug"):
        generate_browser_skill(
            app_slug="x",
            app_name="X",
            workflow_name="w",
            profile_slug="",
            url_events=ICICI_EVENTS,
            login_url=LOGIN,
            output_dir=str(tmp_path),
        )


def test_compile_workflow_bundle_browser_driven(tmp_path):
    """The --browser-driven path through the top-level compiler emits a browser
    skill without touching the HAR-replay path."""
    from noui_core.compile.workflow import compile_workflow_bundle

    bundle = {
        "har": {"log": {"entries": []}},
        "click_events": ICICI_CLICKS,
        "url_events": ICICI_EVENTS,
    }
    res = compile_workflow_bundle(
        session_id="deadbeef1234",
        bundle=bundle,
        name="icici credit card",
        target="skill",
        profile_slug="icici-credit-card",
        start_url=LOGIN,
        output_root=str(tmp_path),
        browser_driven=True,
        allow_unbound_profile=True,  # offline test can't reach Tabby to verify
    )
    m = res["skill"]
    assert m["runtime"]["operation_style"] == "browser"
    assert [o["name"] for o in m["operations"]] == ["read_overview", "read_credit_card"]
    assert all(o["tool"] == "call_web_browser" for o in m["operations"])


def test_compile_workflow_bundle_default_stays_call_web_api(tmp_path):
    """Regression guard: without browser_driven the skill path is unchanged."""
    from noui_core.compile.workflow import compile_workflow_bundle

    bundle = {
        "har": {
            "log": {
                "entries": [
                    {
                        "startedDateTime": "2026-08-05T00:00:00.000Z",
                        "request": {
                            "method": "GET",
                            "url": "https://retailnetbanking.icici.bank.in/dashboardAPI/creditCardSummary",
                            "headers": [{"name": "accept", "value": "application/json"}],
                        },
                        "response": {
                            "status": 200,
                            "content": {"mimeType": "application/json", "text": "{}"},
                        },
                    }
                ]
            }
        },
        "click_events": ICICI_CLICKS,
        "url_events": ICICI_EVENTS,
    }
    res = compile_workflow_bundle(
        session_id="deadbeef1234",
        bundle=bundle,
        name="icici credit card",
        target="skill",
        profile_slug="icici-credit-card",
        start_url=LOGIN,
        output_root=str(tmp_path),
        allow_unbound_profile=True,
    )
    # default skill is NOT a browser skill
    assert res["skill"]["runtime"]["operation_style"] != "browser"


# ---------------------------------------------------------------------------
# Navigation must be reachable from the landing page, and hash routes are pages
# ---------------------------------------------------------------------------

_B = "https://bank.test"


def _url_ev(frm, to, ts):
    return {"from_url": frm, "to_url": to, "timestamp": ts}


def _click(url, text, ts):
    return {"event_type": "click", "url": url, "text_content": text, "timestamp": ts}


def test_multi_hop_page_gets_the_whole_chain_from_the_landing_page():
    """_nav_clicks_for only recovers the clicks made on the immediately preceding
    page, so landing -> accounts -> statements compiled to just ["Statements"] —
    a control that does not exist on the page the session actually lands on."""
    pages = derive_browser_pages(
        [
            _url_ev(f"{_B}/login", f"{_B}/home", "2026-01-01T00:00:01+00:00"),
            _url_ev(f"{_B}/home", f"{_B}/accounts", "2026-01-01T00:00:05+00:00"),
            _url_ev(f"{_B}/accounts", f"{_B}/statements", "2026-01-01T00:00:10+00:00"),
        ],
        [
            _click(f"{_B}/home", "Accounts", "2026-01-01T00:00:04+00:00"),
            _click(f"{_B}/accounts", "Statements", "2026-01-01T00:00:09+00:00"),
        ],
        login_url=f"{_B}/login",
    )
    by_name = {p["name"]: p for p in pages}
    assert by_name["read_home"]["nav"] is None  # the landing page needs no click
    assert [c["text"] for c in by_name["read_accounts"]["nav"]] == ["Accounts"]
    assert [c["text"] for c in by_name["read_statements"]["nav"]] == ["Accounts", "Statements"]


def test_page_whose_driving_click_is_untextual_is_dropped_not_read_as_landing():
    """`nav = [] or None` made an unreachable page look like the landing page: its
    recipe became a bare get_page_summary, so the operation claimed to read
    /accounts and actually returned the landing DOM. Icon/SVG nav buttons (no
    text) are common in bank portals, so this was silently wrong data."""
    pages = derive_browser_pages(
        [
            _url_ev(f"{_B}/login", f"{_B}/home", "2026-01-01T00:00:01+00:00"),
            _url_ev(f"{_B}/home", f"{_B}/accounts", "2026-01-01T00:00:05+00:00"),
        ],
        [_click(f"{_B}/home", "", "2026-01-01T00:00:04+00:00")],  # icon button
        login_url=f"{_B}/login",
    )
    assert [p["name"] for p in pages] == ["read_home"]


def test_hash_router_screens_are_separate_pages():
    """HSBCnet serves every screen from one path (…/Landing#/accounts), so a
    path-only page key collapsed the whole portal into a single page — and it
    compiled clean, shipping a skill that could read only the landing screen."""
    pages = derive_browser_pages(
        [
            _url_ev(f"{_B}/login", f"{_B}/app#/home", "2026-01-01T00:00:01+00:00"),
            _url_ev(f"{_B}/app#/home", f"{_B}/app#/accounts", "2026-01-01T00:00:05+00:00"),
            _url_ev(f"{_B}/app#/accounts", f"{_B}/app#/statements", "2026-01-01T00:00:10+00:00"),
        ],
        [
            _click(f"{_B}/app#/home", "Accounts", "2026-01-01T00:00:04+00:00"),
            _click(f"{_B}/app#/accounts", "Statements", "2026-01-01T00:00:09+00:00"),
        ],
        login_url=f"{_B}/login",
    )
    assert [p["name"] for p in pages] == [
        "read_app_home",
        "read_app_accounts",
        "read_app_statements",
    ]


def test_bare_anchor_is_not_a_route():
    """`#section` scrolls within one page; treating it as a route would split a
    single page into many. Only `#/…` counts."""
    pages = derive_browser_pages(
        [
            _url_ev(f"{_B}/login", f"{_B}/report", "2026-01-01T00:00:01+00:00"),
            _url_ev(f"{_B}/report", f"{_B}/report#summary", "2026-01-01T00:00:05+00:00"),
        ],
        [_click(f"{_B}/report", "Summary", "2026-01-01T00:00:04+00:00")],
        login_url=f"{_B}/login",
    )
    assert [p["name"] for p in pages] == ["read_report"]


# ---------------------------------------------------------------------------
# Nav gestures are correlated by `seq`, not by wall clock
# ---------------------------------------------------------------------------


def _seq_url_ev(frm, to, seq, ts=None):
    ev = {"from_url": frm, "to_url": to, "seq": seq}
    if ts:
        ev["timestamp"] = ts
    return ev


def _seq_click(url, text, seq, ts=None):
    ev = {"event_type": "click", "url": url, "text_content": text, "seq": seq}
    if ts:
        ev["timestamp"] = ts
    return ev


def test_nav_gesture_causality_uses_seq_when_the_bundle_carries_it():
    """A click made AFTER the route change cannot have caused it. `seq` is the
    reliable ordering — it is assigned at interaction time, unlike the recorder's
    input timestamps, and it survives events sharing a millisecond."""
    pages = derive_browser_pages(
        [
            _seq_url_ev(f"{_B}/login", f"{_B}/home", 1),
            _seq_url_ev(f"{_B}/home", f"{_B}/accounts", 3),
        ],
        [
            _seq_click(f"{_B}/home", "Accounts", 2),
            # A click back on /home AFTER the nav (browser back, then a re-click
            # elsewhere) must not be folded into the gesture that reached /accounts.
            _seq_click(f"{_B}/home", "Log out", 4),
        ],
        login_url=f"{_B}/login",
    )
    by_name = {p["name"]: p for p in pages}
    assert [c["text"] for c in by_name["read_accounts"]["nav"]] == ["Accounts"]


def test_seq_recovers_the_gesture_when_clicks_share_a_timestamp():
    """Two clicks in the same millisecond are unorderable by timestamp — the
    expand→navigate pair then compiles in whichever order they were recorded."""
    same = "2026-01-01T00:00:04+00:00"
    pages = derive_browser_pages(
        [
            _seq_url_ev(f"{_B}/login", f"{_B}/home", 1, "2026-01-01T00:00:01+00:00"),
            _seq_url_ev(f"{_B}/home", f"{_B}/accounts", 4, "2026-01-01T00:00:05+00:00"),
        ],
        [
            # Recorded in the wrong order; seq says Banking was clicked first.
            _seq_click(f"{_B}/home", "Accounts", 3, same),
            _seq_click(f"{_B}/home", "Banking", 2, same),
        ],
        login_url=f"{_B}/login",
    )
    by_name = {p["name"]: p for p in pages}
    assert [c["text"] for c in by_name["read_accounts"]["nav"]] == ["Banking", "Accounts"]


def test_undated_but_numbered_capture_recovers_the_whole_chain():
    """An agent-driven capture numbers its events but records no wall clock. The
    old code could not bound the gesture without timestamps and kept only the
    last click; `seq` makes the order trustworthy, so the whole expand→navigate
    chain survives (bounded by the trailing-click cap)."""
    pages = derive_browser_pages(
        [
            _seq_url_ev(f"{_B}/login", f"{_B}/home", 1),
            _seq_url_ev(f"{_B}/home", f"{_B}/accounts", 4),
        ],
        [
            _seq_click(f"{_B}/home", "Banking", 2),
            _seq_click(f"{_B}/home", "Accounts", 3),
        ],
        login_url=f"{_B}/login",
    )
    by_name = {p["name"]: p for p in pages}
    assert [c["text"] for c in by_name["read_accounts"]["nav"]] == ["Banking", "Accounts"]


def test_unnumbered_bundle_still_uses_timestamps():
    """Regression guard: bundles recorded before `seq` keep the old behaviour."""
    pages = derive_browser_pages(
        [
            _url_ev(f"{_B}/login", f"{_B}/home", "2026-01-01T00:00:01+00:00"),
            _url_ev(f"{_B}/home", f"{_B}/accounts", "2026-01-01T00:00:05+00:00"),
        ],
        [
            _click(f"{_B}/home", "Banking", "2026-01-01T00:00:03+00:00"),
            _click(f"{_B}/home", "Accounts", "2026-01-01T00:00:04+00:00"),
            _click(f"{_B}/home", "Log out", "2026-01-01T00:00:09+00:00"),  # after the nav
        ],
        login_url=f"{_B}/login",
    )
    by_name = {p["name"]: p for p in pages}
    assert [c["text"] for c in by_name["read_accounts"]["nav"]] == ["Banking", "Accounts"]


# --- schema_version >= 4/5 recordings: candidates + outcomes -------------------
#
# The recorder no longer guesses a single selector. It emits several candidates
# with match counts, and what happened after each click. These pin that the
# compiler actually USES them — previously `selector` was captured on every nav
# click and then silently discarded, so every step compiled to click_by_text on
# visible text alone.

_RICH_EVENTS = [
    {
        "from_url": f"{_H}/login-page",
        "to_url": f"{_H}/overview",
        "timestamp": "2026-08-07T10:00:00Z",
    },
    {
        "from_url": f"{_H}/overview",
        "to_url": f"{_H}/credit-card",
        "timestamp": "2026-08-07T10:00:05Z",
    },
]


def _rich_click(**over):
    base = {
        "event_type": "click",
        "text_content": "Credit Cards",
        "selector": "div.submenu-text",
        "url": f"{_H}/overview",
        "timestamp": "2026-08-07T10:00:04.900Z",
        "event_time": "2026-08-07T10:00:04.900Z",
        "candidates": [
            {"kind": "testid", "value": '[data-testid="nav-cards"]', "match_count": 1},
            {"kind": "text", "value": "Credit Cards", "match_count": 1},
        ],
        "outcome": {
            "navigated": True,
            "to_url": f"{_H}/credit-card",
            "request_count": 3,
            "settled_ms": 420,
            "download": False,
        },
    }
    base.update(over)
    return base


def _ops(pages):
    return json.loads(render_browser_operations_json(pages, profile_slug="icici-bank"))


def test_step_uses_the_unique_candidate_instead_of_visible_text():
    pages = derive_browser_pages(_RICH_EVENTS, [_rich_click()], login_url=LOGIN)
    steps = _ops(pages)["operations"][1]["steps"]

    assert steps[0]["command"] == "click_element"
    assert steps[0]["params"]["selector"] == '[data-testid="nav-cards"]'
    assert steps[0]["locator"]["confidence"] == "unique"


def test_text_steps_are_exact_so_they_cannot_widen_back_into_ambiguity():
    # The recorder established the text matched ONE control. A substring match
    # would reintroduce exactly the ambiguity that made HSBCnet misclick.
    click = _rich_click(
        candidates=[{"kind": "text", "value": "Statements", "match_count": 1}],
    )
    pages = derive_browser_pages(_RICH_EVENTS, [click], login_url=LOGIN)
    steps = _ops(pages)["operations"][1]["steps"]

    assert steps[0]["command"] == "click_by_text"
    assert steps[0]["params"] == {"text": "Statements", "exact": True}


def test_step_carries_the_observed_postcondition():
    # A linear script cannot tell it has gone wrong; it keeps clicking. The URL
    # the recorder OBSERVED after this click is checkable in one step.
    pages = derive_browser_pages(_RICH_EVENTS, [_rich_click()], login_url=LOGIN)
    step = _ops(pages)["operations"][1]["steps"][0]

    assert step["expect"]["url"] == f"{_H}/credit-card"
    # A recorded settle is one sample from one network, so it is doubled for
    # headroom — but never below a second, which is not a wait worth having on a
    # bank SPA. 420ms doubles to 840 and lands on the floor.
    assert step["expect"]["settle_ms"] == 1000


def test_a_slow_observed_settle_is_doubled_rather_than_floored():
    click = _rich_click(
        outcome={
            "navigated": True,
            "to_url": f"{_H}/credit-card",
            "request_count": 2,
            "settled_ms": 2600,
            "download": False,
        }
    )
    pages = derive_browser_pages(_RICH_EVENTS, [click], login_url=LOGIN)
    step = _ops(pages)["operations"][1]["steps"][0]

    assert step["expect"]["settle_ms"] == 5200


def test_a_click_that_downloaded_records_it_as_the_success_condition():
    click = _rich_click(
        outcome={
            "navigated": False,
            "to_url": None,
            "request_count": 1,
            "settled_ms": None,
            "download": True,
        }
    )
    pages = derive_browser_pages(_RICH_EVENTS, [click], login_url=LOGIN)
    step = _ops(pages)["operations"][1]["steps"][0]

    assert step["expect"] == {"download": True}


def test_an_icon_button_is_compiled_instead_of_dropped():
    # A click with no visible text used to be discarded outright, which threw away
    # every icon control — the hamburger and the chevrons that open a bank nav's
    # accordions, so the page they lead to became unreachable and was dropped.
    icon = _rich_click(
        text_content="",
        candidates=[
            {"kind": "aria_label", "value": 'button[aria-label="Open menu"]', "match_count": 1}
        ],
    )
    pages = derive_browser_pages(_RICH_EVENTS, [icon], login_url=LOGIN)

    assert [p["name"] for p in pages] == ["read_overview", "read_credit_card"]
    step = _ops(pages)["operations"][1]["steps"][0]
    assert step["params"]["selector"] == 'button[aria-label="Open menu"]'


def test_ambiguous_locators_are_surfaced_rather_than_hidden():
    from noui_core.compile.browser_skill import ambiguous_steps  # noqa: PLC0415

    click = _rich_click(
        candidates=[{"kind": "text", "value": "Credit Cards", "match_count": 4}],
    )
    pages = derive_browser_pages(_RICH_EVENTS, [click], login_url=LOGIN)
    flagged = ambiguous_steps(pages)

    assert len(flagged) == 1
    assert flagged[0]["page"] == "read_credit_card"
    assert flagged[0]["locator"]["match_count"] == 4


def test_recordings_without_candidates_still_compile_the_old_way():
    # Every recording made before schema_version 4 has no candidates. Those must
    # keep compiling exactly as they did, not start failing.
    legacy = {
        "event_type": "click",
        "text_content": "Credit Cards",
        "selector": "div.submenu-text",
        "url": f"{_H}/overview",
        "timestamp": "2026-08-07T10:00:04.900Z",
    }
    pages = derive_browser_pages(_RICH_EVENTS, [legacy], login_url=LOGIN)
    steps = _ops(pages)["operations"][1]["steps"]

    assert steps[0] == {"command": "click_by_text", "params": {"text": "Credit Cards"}}
    assert steps[-1] == {"command": "get_page_summary"}


def test_skill_md_tells_the_agent_to_stop_when_an_expectation_fails():
    # With an LLM runtime the prose IS the enforcement for `expect` — nothing
    # else reads it.
    pages = derive_browser_pages(_RICH_EVENTS, [_rich_click()], login_url=LOGIN)
    md = render_browser_skill_md(
        skill_id="icici-bank",
        app_name="ICICI",
        workflow_name="Credit card",
        pages=pages,
        profile_slug="icici-bank",
    )
    assert "stop and report it" in md
    assert "settle_ms" in md
    assert "Known ambiguous steps" not in md  # nothing ambiguous in this recording


def test_skill_md_names_the_ambiguous_steps():
    click = _rich_click(candidates=[{"kind": "text", "value": "Credit Cards", "match_count": 4}])
    pages = derive_browser_pages(_RICH_EVENTS, [click], login_url=LOGIN)
    md = render_browser_skill_md(
        skill_id="icici-bank",
        app_name="ICICI",
        workflow_name="Credit card",
        pages=pages,
        profile_slug="icici-bank",
    )
    assert "Known ambiguous steps" in md
    assert "matched 4 elements" in md


def test_skill_md_describes_an_icon_click_without_an_empty_quote():
    icon = _rich_click(
        text_content="",
        candidates=[{"kind": "aria_label", "value": 'button[aria-label="Menu"]', "match_count": 1}],
    )
    pages = derive_browser_pages(_RICH_EVENTS, [icon], login_url=LOGIN)
    md = render_browser_skill_md(
        skill_id="icici-bank",
        app_name="ICICI",
        workflow_name="Credit card",
        pages=pages,
        profile_slug="icici-bank",
    )
    assert 'click ""' not in md
    assert "aria_label" in md
