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
ICICI_EVENTS = [
    {"from_url": "about:blank", "to_url": f"{_H}/login-page", "timestamp": "2026-08-04T22:16:24Z"},
    # login auto-redirect to the landing page (no click) → landing, no nav
    {"from_url": f"{_H}/login-page", "to_url": f"{_H}/overview", "timestamp": "2026-08-04T22:17:24Z"},
    # in-app click drove this route change → nav = click "Credit Cards"
    {"from_url": f"{_H}/overview", "to_url": f"{_H}/credit-card", "timestamp": "2026-08-04T22:17:29.485Z"},
    # same page, different query — one readable page
    {"from_url": f"{_H}/credit-card", "to_url": f"{_H}/credit-card?tab=statements", "timestamp": "2026-08-04T22:17:40Z"},
    # third-party widget/telemetry origin — must NOT be treated as a data page
    {"from_url": f"{_H}/credit-card", "to_url": "https://www.icici.bank.in/analytics", "timestamp": "2026-08-04T22:17:41Z"},
    # one-shot token in the query — navigating here later lands on an error
    {"from_url": f"{_H}/credit-card", "to_url": f"{_H}/pay?token=ONESHOTTOKEN123456", "timestamp": "2026-08-04T22:17:45Z"},
]
ICICI_CLICKS = [
    {"event_type": "click", "text_content": "Credit Cards", "selector": "div.submenu-text",
     "url": f"{_H}/overview", "timestamp": "2026-08-04T22:17:29.461Z"},
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
    assert credit["nav"]["text"] == "Credit Cards"


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
    assert [s["command"] for s in ops["read_credit_card"]["steps"]] == ["click_by_text", "get_page_summary"]
    assert ops["read_credit_card"]["steps"][0]["params"]["text"] == "Credit Cards"
    # a full-page navigate/goto must NEVER be emitted (it reloads → session expiry)
    all_cmds = [st["command"] for o in doc["operations"] for st in o["steps"]]
    assert "navigate" not in all_cmds


def test_skill_md_declares_browser_auth_and_tool():
    pages = derive_browser_pages(ICICI_EVENTS, login_url=LOGIN)
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
        generate_browser_skill_no_pages = None
        from noui_core.compile.browser_skill import generate_browser_skill
        generate_browser_skill(
            app_slug="x", app_name="X", workflow_name="w", profile_slug="x-prof",
            url_events=[{"to_url": "https://b.test/login-page"}],
            login_url="https://b.test/login-page", output_dir=str(tmp_path),
        )


def test_generate_refuses_without_profile(tmp_path):
    import pytest
    from noui_core.compile.browser_skill import generate_browser_skill
    with pytest.raises(ValueError, match="requires a profile_slug"):
        generate_browser_skill(
            app_slug="x", app_name="X", workflow_name="w", profile_slug="",
            url_events=ICICI_EVENTS, login_url=LOGIN, output_dir=str(tmp_path),
        )


def test_compile_workflow_bundle_browser_driven(tmp_path):
    """The --browser-driven path through the top-level compiler emits a browser
    skill without touching the HAR-replay path."""
    from noui_core.compile.workflow import compile_workflow_bundle

    bundle = {
        "har": {"log": {"entries": []}},
        "click_events": [],
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
        "har": {"log": {"entries": [{
            "startedDateTime": "2026-08-05T00:00:00.000Z",
            "request": {
                "method": "GET",
                "url": "https://retailnetbanking.icici.bank.in/dashboardAPI/creditCardSummary",
                "headers": [{"name": "accept", "value": "application/json"}],
            },
            "response": {"status": 200, "content": {"mimeType": "application/json", "text": "{}"}},
        }]}},
        "click_events": [],
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
