"""Tests for tabby_client session-scaling + agent session-status (Autopilot HITL)."""

from __future__ import annotations

from noui_core import tabby_client


def test_scale_sessions(monkeypatch):
    captured = {}

    def fake_http(method, path, body=None, token=None, timeout=15):
        captured.update(method=method, path=path, body=body, token=token)
        return {"desired_sessions": 1, "app_id": "app-1"}

    monkeypatch.setattr(tabby_client, "_tabby_http", fake_http)
    out = tabby_client.scale_sessions("app-1", 1, "admin-tok")
    assert captured["method"] == "POST"
    assert captured["path"] == "/apps/app-1/sessions/scale"
    assert captured["body"] == {"desired_sessions": 1}
    assert out["desired_sessions"] == 1


def test_get_session_status_login_needed(monkeypatch):
    def fake_http(method, path, body=None, token=None, timeout=15):
        assert method == "GET"
        assert path == "/agent/session-status/expedia-e2e2"
        return {
            "session_id": "s-1",
            "state": "LOGIN_NEEDED",
            "hitl_active": True,
            "vnc_stream": {"url": "https://tabby/vnc/s-1#token=x", "expires_at": "..."},
        }

    monkeypatch.setattr(tabby_client, "_tabby_http", fake_http)
    st = tabby_client.get_session_status("expedia-e2e2", "agent-tok")
    assert st["state"] == "LOGIN_NEEDED"
    assert st["hitl_active"] is True
    assert st["vnc_stream"]["url"].startswith("https://")


def test_get_session_status_healthy(monkeypatch):
    monkeypatch.setattr(
        tabby_client,
        "_tabby_http",
        lambda *a, **k: {
            "session_id": "s-1",
            "state": "HEALTHY",
            "hitl_active": False,
            "vnc_stream": None,
        },
    )
    st = tabby_client.get_session_status("expedia-e2e2", "agent-tok")
    assert st["state"] == "HEALTHY"
    assert st["vnc_stream"] is None


def test_create_recording_session_residential_proxy(monkeypatch):
    captured = {}

    def fake_http(method, path, body=None, token=None, timeout=15):
        captured.update(method=method, path=path, body=body)
        return {"session_id": "s1", "vnc_url": "https://t/vnc/s1#token=x"}

    monkeypatch.setattr(tabby_client, "_tabby_http", fake_http)
    tabby_client.create_recording_session(
        "login", "https://www.pnc.com", "agent-tok", residential_proxy=True
    )
    assert captured["path"] == "/recording/sessions"
    assert captured["body"]["residential_proxy"] is True


def test_create_recording_session_omits_residential_proxy_by_default(monkeypatch):
    captured = {}

    def fake_http(method, path, body=None, token=None, timeout=15):
        captured.update(body=body)
        return {"session_id": "s1", "vnc_url": "https://t/vnc/s1#token=x"}

    monkeypatch.setattr(tabby_client, "_tabby_http", fake_http)
    tabby_client.create_recording_session("login", "https://example.com", "agent-tok")
    # Omitted (not False) so the recording-shell app default applies server-side.
    assert "residential_proxy" not in captured["body"]
