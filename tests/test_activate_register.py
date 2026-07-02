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


# ── extend_login_scope_for_workflow ─────────────────────────────────────────
#
# Regression coverage for the QBO gap: a login profile registered before its
# paired workflow capture never has that workflow's hosts in scope, so
# Tabby's dynamic header capture never activates. Verified live end-to-end
# (see plans/noui/noui-dynamic-bearer-header-capture-gap-plan.md) that this
# closes the gap, once target_urls carry the required "/**" glob suffix.


class FakeExtendClient:
    def __init__(self, *, profile=None, app=None, template=None):
        self.profile = profile
        self.app = app
        self.template = template
        self.update_app_template_calls: list[tuple[str, dict]] = []
        self.update_app_calls: list[tuple[str, dict]] = []

    def get_service_profile_by_slug(self, slug, token):
        return self.profile

    def get_app(self, app_id, token):
        return self.app

    def get_app_template(self, template_id, token):
        return self.template

    def update_app_template(self, template_id, payload, token):
        self.update_app_template_calls.append((template_id, payload))
        self.template = {**self.template, **payload}
        return self.template

    def update_app(self, app_id, payload, token):
        self.update_app_calls.append((app_id, payload))
        self.app = {**self.app, **payload}
        return self.app


def _extend_patch(monkeypatch, fake):
    monkeypatch.setattr(register, "tabby_client", fake)
    monkeypatch.setattr(register, "resolve_admin_token", lambda: "admin-tok")


def test_extend_widens_target_urls_with_wildcard_suffix(monkeypatch):
    fake = FakeExtendClient(
        profile={"app_id": "app-1"},
        app={
            "id": "app-1",
            "template_id": "tmpl-1",
            "target_urls": ["https://accounts.example.com/**"],
        },
        template={
            "id": "tmpl-1",
            "export_policy": {
                "encryption": {"algo": "AES-256-GCM", "key_version": "v1"},
                "ttl_seconds": 3600,
                "target_urls": ["https://accounts.example.com/**"],
                "artifact_types": ["cookies", "headers"],
                "credential_types": {"headers": ["authorization"]},
            },
        },
    )
    _extend_patch(monkeypatch, fake)
    result = register.extend_login_scope_for_workflow(
        "myapp",
        target_domains=["api.example.com"],
        required_headers=["authorization"],
    )
    assert result["status"] == "extended"

    # Template's new target_urls carry the required glob suffix, not a bare origin.
    _, template_payload = fake.update_app_template_calls[0]
    assert "https://api.example.com/**" in template_payload["export_policy"]["target_urls"]
    # export_policy.target_domains set — feeds the ServiceProfile's own field
    # when the template update propagates (a key separate from target_urls).
    assert template_payload["export_policy"]["target_domains"] == ["api.example.com"]
    # Preserved fields untouched (the earlier live bug this guards against:
    # dropping these caused Tabby to reject the App's own subsequent update).
    assert template_payload["export_policy"]["encryption"] == {
        "algo": "AES-256-GCM",
        "key_version": "v1",
    }
    assert template_payload["export_policy"]["ttl_seconds"] == 3600

    # The already-provisioned App's own top-level target_urls also extended.
    app_id, app_payload = fake.update_app_calls[0]
    assert app_id == "app-1"
    assert "https://api.example.com/**" in app_payload["target_urls"]
    assert "https://accounts.example.com/**" in app_payload["target_urls"]


def test_extend_is_noop_when_scope_already_covers_workflow(monkeypatch):
    fake = FakeExtendClient(
        profile={"app_id": "app-1"},
        app={
            "id": "app-1",
            "template_id": "tmpl-1",
            "target_urls": ["https://api.example.com/**"],
        },
        template={
            "id": "tmpl-1",
            "export_policy": {
                "target_urls": ["https://api.example.com/**"],
                "target_domains": ["api.example.com"],
                "credential_types": {"headers": ["authorization"]},
                "request_header_allowlist": ["authorization"],
                "artifact_types": ["headers"],
            },
        },
    )
    _extend_patch(monkeypatch, fake)
    result = register.extend_login_scope_for_workflow(
        "myapp",
        target_domains=["api.example.com"],
        required_headers=["authorization"],
    )
    assert result["status"] == "unchanged"
    assert fake.update_app_calls == []


def test_extend_skips_when_profile_not_found(monkeypatch):
    fake = FakeExtendClient(profile=None)
    _extend_patch(monkeypatch, fake)
    result = register.extend_login_scope_for_workflow(
        "missing-profile",
        target_domains=["api.example.com"],
        required_headers=["authorization"],
    )
    assert result["status"] == "skipped"


def test_extend_never_raises_on_unexpected_error(monkeypatch):
    """Best-effort by design — a failure here must never break workflow compilation."""

    class ExplodingClient:
        def get_service_profile_by_slug(self, slug, token):
            raise RuntimeError("Tabby unreachable")

    monkeypatch.setattr(register, "tabby_client", ExplodingClient())
    monkeypatch.setattr(register, "resolve_admin_token", lambda: "admin-tok")
    result = register.extend_login_scope_for_workflow(
        "myapp", target_domains=["api.example.com"], required_headers=["authorization"]
    )
    assert result["status"] == "skipped"


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
