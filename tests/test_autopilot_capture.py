"""Tests for noui_core.capture.autopilot — Autopilot driving + bundle synthesis.

Tabby's /execute/browser is mocked: we assert NoUI drives the right commands and
synthesizes a workflow bundle (HAR from har_stop + click/url events from the
commands it issued).
"""

from __future__ import annotations

import noui_core.capture.autopilot as autopilot
from noui_core.capture.autopilot import AutopilotSession, run_steps


class FakeTabby:
    """Records issued commands and returns canned /execute/browser responses."""

    def __init__(self, har):
        self.har = har
        self.calls: list[tuple[str, dict]] = []

    def execute_browser(self, profile_slug, command, params=None, *, token, timeout_ms=None):
        self.calls.append((command, params or {}))
        if command == "navigate":
            return {"success": True, "data": {"url": (params or {}).get("url"), "title": "T"}}
        if command == "har_stop":
            return {"success": True, "data": self.har}
        return {"success": True, "data": {}}


def _patch(monkeypatch, fake):
    monkeypatch.setattr(autopilot.tabby_client, "execute_browser", fake.execute_browser)


def test_session_synthesizes_bundle(monkeypatch):
    har = {
        "log": {
            "entries": [
                {"request": {"url": "https://api.x.com/v1/go", "method": "POST"}, "response": {}}
            ]
        }
    }
    fake = FakeTabby(har)
    _patch(monkeypatch, fake)

    ap = AutopilotSession("my-profile", token="t")
    ap.start_capture()
    ap.navigate("https://x.com/a")
    ap.click("#btn")
    ap.type("#q", "hello")
    ap.navigate("https://x.com/b")
    bundle = ap.finish()

    # Commands issued in order, har bracketed by start/stop.
    issued = [c for c, _ in fake.calls]
    assert issued[0] == "har_start"
    assert issued[-1] == "har_stop"
    assert "navigate" in issued and "click_element" in issued and "type_text" in issued

    # Synthesized bundle is the VNC bundle shape.
    assert bundle["recording_mode"] == "workflow"
    assert bundle["har"] is har
    assert {"event_type": "click", "selector": "#btn", "seq": 2} in bundle["click_events"]
    # URL transitions recorded from the navigates.
    tos = [u["to_url"] for u in bundle["url_events"]]
    assert tos == ["https://x.com/a", "https://x.com/b"]
    # Clicks and navigations are numbered from ONE counter, in driven order, so
    # the compilers can correlate a route change with the click that caused it.
    assert [u["seq"] for u in bundle["url_events"]] == [1, 3]
    assert [c["seq"] for c in bundle["click_events"]] == [2]


def test_run_steps_scripted(monkeypatch):
    har = {"log": {"entries": []}}
    fake = FakeTabby(har)
    _patch(monkeypatch, fake)

    steps = [
        {"action": "navigate", "url": "https://x.com/start"},
        {"action": "click", "selector": ".go"},
        {"action": "type", "selector": "#q", "text": "abc"},
    ]
    bundle = run_steps("p", steps, token="t")
    assert bundle["recording_mode"] == "workflow"
    assert bundle["url_events"][0]["to_url"] == "https://x.com/start"
    assert bundle["click_events"][0]["selector"] == ".go"


def test_run_steps_rejects_unknown_action(monkeypatch):
    fake = FakeTabby({"log": {"entries": []}})
    _patch(monkeypatch, fake)
    import pytest

    with pytest.raises(ValueError, match="unknown action"):
        run_steps("p", [{"action": "teleport"}], token="t")


def test_execute_browser_raises_on_worker_failure(monkeypatch):
    import pytest
    from noui_core import tabby_client

    def boom(method, path, body=None, token=None, timeout=15):
        return {"success": False, "error": "no healthy session"}

    monkeypatch.setattr(tabby_client, "_tabby_http", boom)
    with pytest.raises(RuntimeError, match="no healthy session"):
        tabby_client.execute_browser("p", "navigate", {"url": "https://x"}, token="t")


def test_numbered_bundle_still_compiles_as_workflow_only(monkeypatch):
    """`seq` must not make an autopilot capture look like it has a login.

    The splitter finds a login boundary from credential-field interactions, and
    an autopilot bundle records none — a driven capture runs against an
    already-authenticated profile. Numbering the events must not change that:
    split_bundle returning a pair here would hand the workflow a login slice and
    an empty HAR.
    """
    from noui_core.capture.split import find_login_boundary, split_bundle

    fake = FakeTabby({"log": {"entries": []}})
    _patch(monkeypatch, fake)

    ap = AutopilotSession("p", token="t")
    ap.start_capture()
    ap.navigate("https://x.com/a")
    ap.click("#btn")
    bundle = ap.finish()

    assert find_login_boundary(bundle) is None
    assert split_bundle(bundle) is None


def test_clicks_and_navigations_interleave_in_one_total_order(monkeypatch):
    """The point of numbering: a merged sort of both event lists reproduces the
    order the driver drove them in. These bundles carry no wall clock, so `seq`
    is the ONLY thing that can place a click relative to a route change."""
    from noui_core.event_order import merged_order, order_events

    fake = FakeTabby({"log": {"entries": []}})
    _patch(monkeypatch, fake)

    ap = AutopilotSession("p", token="t")
    ap.start_capture()
    ap.navigate("https://bank.test/home")
    ap.click_text("Banking")
    ap.click_text("Accounts")
    ap.navigate("https://bank.test/accounts")
    bundle = ap.finish()

    assert merged_order(bundle["click_events"], bundle["url_events"])
    merged = order_events(bundle["click_events"] + bundle["url_events"])
    assert [e.get("text") or e.get("to_url") for e in merged] == [
        "https://bank.test/home",
        "Banking",
        "Accounts",
        "https://bank.test/accounts",
    ]
