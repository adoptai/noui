"""Orchestration tests for capture_import._run_combined (network calls stubbed)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_NOUI_ROOT = Path(__file__).resolve().parent.parent
for _p in (_NOUI_ROOT / "skills" / "noui", _NOUI_ROOT / "skills" / "noui" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import capture_import as ci  # noqa: E402

T = {k: f"2026-01-01T00:00:0{k}.000Z" for k in range(8)}


def _merged_bundle(with_login: bool = True) -> dict:
    role = "username" if with_login else None
    return {
        "session_id": "sess-abcd1234",
        "recording_mode": "login",
        "click_events": [
            {"timestamp": T[1], "field_role": role, "tag_name": "input"},
            {
                "timestamp": T[2],
                "field_role": "password" if with_login else None,
                "tag_name": "input",
            },
        ],
        "url_events": [
            {
                "timestamp": T[0],
                "from_url": "about:blank",
                "to_url": "https://app.example.com/login",
            },
            {
                "timestamp": T[4],
                "from_url": "https://app.example.com/login",
                "to_url": "https://app.example.com/dash",
            },
        ],
        "har": {
            "log": {
                "entries": [
                    {
                        "startedDateTime": T[3],
                        "request": {"method": "POST", "url": "https://app.example.com/login"},
                        "response": {},
                    },
                    {
                        "startedDateTime": T[5],
                        "request": {"method": "GET", "url": "https://app.example.com/api/data"},
                        "response": {},
                    },
                ]
            }
        },
        "cookies": [{"name": "session", "domain": "app.example.com"}],
    }


def _args(**over) -> SimpleNamespace:
    base = {
        "session_id": "sess-abcd1234",
        "name": "acme",
        "url": "https://app.example.com/login",
        "target": "skill",
        "execution_mode": "harness",
        "auth_type": "session",
        "api_key_header": "",
        "credential_mode": "takeover",
        "auth_mode": "agent_token",
        "tenant_id": "",
        "post_login_url_pattern": "",
        "profile_slug": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_combined_registers_login_then_compiles_workflow_session_auth(monkeypatch):
    calls: dict = {}

    def fake_login(**kw):
        calls["login"] = kw
        return {"service_profile_draft": {"credential_types": {"headers": ["Authorization"]}}}

    def fake_register(compiled, tenant_id=""):
        calls["register"] = tenant_id
        return {"profile_id": "acme-login"}

    def fake_workflow(**kw):
        calls["workflow"] = kw
        return {"skill": {"skill_id": "acme", "operations": [1, 2]}}

    monkeypatch.setattr(ci, "_default_tenant", lambda t: "tenant-1")
    monkeypatch.setattr(ci, "compile_login_bundle", fake_login)
    monkeypatch.setattr(ci.register, "register_login", fake_register)
    monkeypatch.setattr(ci, "compile_workflow_bundle", fake_workflow)

    rc = ci._run_combined(_args(), _merged_bundle())
    assert rc == 0
    # workflow half is bound to the freshly registered profile, forced session-auth,
    # and fed the login's own declared headers (no Tabby round-trip).
    wf = calls["workflow"]
    assert wf["auth_type"] == "session"
    assert wf["profile_slug"] == "acme-login"
    assert wf["login_credential_headers"] == ["Authorization"]
    # login half received only the pre-boundary slice — the /api/ call is NOT in it.
    login_urls = [e["request"]["url"] for e in calls["login"]["bundle"]["har"]["log"]["entries"]]
    assert "https://app.example.com/login" in login_urls
    assert all("/api/" not in u for u in login_urls)
    # workflow half excludes the login submit.
    wf_urls = [e["request"]["url"] for e in wf["bundle"]["har"]["log"]["entries"]]
    assert wf_urls == ["https://app.example.com/api/data"]


def test_combined_no_login_segment_falls_back_to_workflow_only(monkeypatch):
    calls: dict = {}

    def fake_workflow(**kw):
        calls["workflow"] = kw
        return {"skill": {"skill_id": "acme", "operations": []}}

    monkeypatch.setattr(ci, "compile_workflow_bundle", fake_workflow)

    def _boom(*a, **k):
        raise AssertionError("register_login must NOT be called without a login segment")

    monkeypatch.setattr(ci.register, "register_login", _boom)

    rc = ci._run_combined(
        _args(auth_type="auto", profile_slug="existing-prof"), _merged_bundle(with_login=False)
    )
    assert rc == 0
    # falls through to a plain workflow compile: keeps the passed auth_type/profile,
    # does not force session.
    assert calls["workflow"]["auth_type"] == "auto"
    assert calls["workflow"]["profile_slug"] == "existing-prof"
