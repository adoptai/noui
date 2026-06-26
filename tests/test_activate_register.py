"""Tests for noui_core.activate.register — login registration + App Template wiring.

tabby_client HTTP calls are mocked; we assert the right endpoints fire and the
template payload is built from the compiled drafts.
"""

from __future__ import annotations

import noui_core.activate.register as register


def _compiled():
    return {
        "validation": {"generator_valid": True},
        "application_draft": {
            "name": "Expedia",
            "target_urls": ["https://www.expedia.com/"],
            "login_config": {"steps": []},
            "export_policy": {},
        },
        "service_profile_draft": {
            "profile_id": "expedia-login",
            "credential_types": {"cookies": [{"name": "sid", "volatility": "STABLE"}]},
            "target_domains": ["www.expedia.com"],
        },
    }


class FakeClient:
    # Template-first only: the client no longer exposes register_application /
    # register_service_profile / promote_profile. If register_login tried to call
    # one, this fake would AttributeError — a loud guard against regressing to
    # direct App creation.
    def __init__(self):
        self.calls = []

    def is_alive(self):
        return True

    def register_app_template(self, payload, token, *, tenant_id=""):
        self.calls.append(("register_app_template", payload, tenant_id))
        return {"id": "tmpl-789"}


def _patch(monkeypatch, fake):
    monkeypatch.setattr(register, "tabby_client", fake)
    monkeypatch.setattr(register, "resolve_admin_token", lambda: "admin-tok")


def test_register_creates_template_only(monkeypatch):
    # Template-first: ONLY the App Template is created — no direct App/Profile.
    fake = FakeClient()
    _patch(monkeypatch, fake)
    out = register.register_login(_compiled())
    assert out == {"template_id": "tmpl-789", "profile_id": "expedia-login"}
    assert [c[0] for c in fake.calls] == ["register_app_template"]


def test_template_payload_is_correct(monkeypatch):
    fake = FakeClient()
    _patch(monkeypatch, fake)
    register.register_login(_compiled())
    payload = next(c for c in fake.calls if c[0] == "register_app_template")[1]
    # profile_name_pattern must equal the runtime slug (the auto-provision match key).
    assert payload["profile_name_pattern"] == "expedia-login"
    # execute_enabled MUST be present and true — cloned onto each per-user app so
    # /execute (call_web_api) works; the DTO now accepts it.
    assert payload["execute_enabled"] is True
    # profile credential_types/target_domains folded into export_policy.
    assert payload["export_policy"]["target_domains"] == ["www.expedia.com"]


def test_register_threads_tenant_id(monkeypatch):
    # An explicit tenant_id must reach the template create so the agent token
    # (whose tenant it is) can resolve + drive the per-user profiles it provisions.
    fake = FakeClient()
    _patch(monkeypatch, fake)
    register.register_login(_compiled(), tenant_id="7f420cc0")
    by_name = {c[0]: c for c in fake.calls}
    assert by_name["register_app_template"][2] == "7f420cc0"


def test_register_rejects_invalid(monkeypatch):
    import pytest

    fake = FakeClient()
    _patch(monkeypatch, fake)
    bad = _compiled()
    bad["validation"] = {"generator_valid": False, "issues": ["nope"]}
    with pytest.raises(RuntimeError, match="invalid"):
        register.register_login(bad)


def test_tenant_id_from_token():
    import base64
    import json

    from noui_core.auth import tenant_id_from_token

    payload = (
        base64.urlsafe_b64encode(json.dumps({"tenant_id": "abc-123"}).encode()).decode().rstrip("=")
    )
    jwt = f"hdr.{payload}.sig"
    assert tenant_id_from_token(jwt) == "abc-123"
    assert tenant_id_from_token("not-a-jwt") == ""
    assert tenant_id_from_token("") == ""
