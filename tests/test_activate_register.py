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
    def __init__(self):
        self.calls = []

    def is_alive(self):
        return True

    def register_application(self, bundle, token, *, tenant_id=""):
        self.calls.append(("register_application", token, tenant_id))
        return {"app_id": "app-123"}

    def register_service_profile(self, bundle, token, app_id, *, tenant_id=""):
        self.calls.append(("register_service_profile", app_id, tenant_id))
        return {"id": "profile-db-456"}

    def promote_profile(self, profile_db_id, token):
        self.calls.append(("promote_profile", profile_db_id))
        return {}

    def register_app_template(self, payload, token, *, tenant_id=""):
        self.calls.append(("register_app_template", payload, tenant_id))
        return {"id": "tmpl-789"}


def _patch(monkeypatch, fake):
    monkeypatch.setattr(register, "tabby_client", fake)
    monkeypatch.setattr(register, "resolve_admin_token", lambda: "admin-tok")


def test_register_basic(monkeypatch):
    fake = FakeClient()
    _patch(monkeypatch, fake)
    out = register.register_login(_compiled())
    assert out == {
        "app_id": "app-123",
        "profile_db_id": "profile-db-456",
        "profile_id": "expedia-login",
        "version_state": "STAGING",
    }
    assert [c[0] for c in fake.calls] == ["register_application", "register_service_profile"]


def test_register_with_promote(monkeypatch):
    fake = FakeClient()
    _patch(monkeypatch, fake)
    out = register.register_login(_compiled(), promote=True)
    # One promote: STAGING → CANARY (runtime-usable). ACTIVE is gated by canary traffic.
    assert out["version_state"] == "CANARY"
    assert [c[0] for c in fake.calls].count("promote_profile") == 1


def test_register_with_template(monkeypatch):
    fake = FakeClient()
    _patch(monkeypatch, fake)
    out = register.register_login(_compiled(), promote=True, as_template=True)
    assert out["template_id"] == "tmpl-789"
    tmpl_call = next(c for c in fake.calls if c[0] == "register_app_template")
    payload = tmpl_call[1]
    # profile_name_pattern must equal the runtime slug; execute_enabled must NOT be present.
    assert payload["profile_name_pattern"] == "expedia-login"
    assert "execute_enabled" not in payload
    # profile credential_types/target_domains folded into export_policy.
    assert payload["export_policy"]["target_domains"] == ["www.expedia.com"]


def test_register_threads_tenant_id(monkeypatch):
    # An explicit tenant_id must reach app, profile, AND template creates so the
    # agent token (whose tenant it is) can resolve + drive them.
    fake = FakeClient()
    _patch(monkeypatch, fake)
    register.register_login(_compiled(), as_template=True, tenant_id="7f420cc0")
    by_name = {c[0]: c for c in fake.calls}
    assert by_name["register_application"][2] == "7f420cc0"
    assert by_name["register_service_profile"][2] == "7f420cc0"
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
