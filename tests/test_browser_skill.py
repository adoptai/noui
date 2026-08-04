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

ICICI_EVENTS = [
    {"to_url": "https://retailnetbanking.icici.bank.in/login-page"},
    {"to_url": "https://retailnetbanking.icici.bank.in/overview"},
    {"to_url": "https://retailnetbanking.icici.bank.in/credit-card"},
    # same page, different query — one readable page
    {"to_url": "https://retailnetbanking.icici.bank.in/credit-card?tab=statements"},
    # third-party widget/telemetry origin — the DevRev/Dynatrace noise that
    # poisoned the HAR-replay skill; must NOT be treated as a data page
    {"to_url": "https://www.icici.bank.in/analytics"},
    # one-shot token in the query — navigating here later lands on an error
    {"to_url": "https://retailnetbanking.icici.bank.in/pay?token=ONESHOTTOKEN123456"},
]
LOGIN = "https://retailnetbanking.icici.bank.in/login-page"


def test_derive_pages_keeps_only_readable_app_pages():
    pages = derive_browser_pages(ICICI_EVENTS, login_url=LOGIN)
    assert [p["name"] for p in pages] == ["read_overview", "read_credit_card"]
    assert pages[1]["url"] == "https://retailnetbanking.icici.bank.in/credit-card"


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


def test_operations_json_shape():
    pages = derive_browser_pages(ICICI_EVENTS, login_url=LOGIN)
    doc = json.loads(render_browser_operations_json(pages, profile_slug="icici-credit-card"))
    assert doc["style"] == "browser"
    op = doc["operations"][0]
    assert op["tool"] == "call_web_browser"
    assert op["profile_slug"] == "icici-credit-card"
    # navigate then read
    assert [s["command"] for s in op["steps"]] == ["navigate", "get_page_summary"]
    assert op["steps"][0]["params"]["url"].startswith("https://")


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
