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
