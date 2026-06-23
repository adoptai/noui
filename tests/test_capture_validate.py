"""Tests for noui_core.capture.validate (ported HAR validator + in-memory variant)."""

from noui_core.capture.validate import validate_har_dict


def _entry(url, method="GET", content_type="application/json", post=None):
    e = {
        "request": {"url": url, "method": method},
        "response": {"headers": [{"name": "Content-Type", "value": content_type}]},
    }
    if post is not None:
        e["request"]["postData"] = {"text": post}
    return e


def _har(entries):
    return {"log": {"entries": entries}}


def test_empty_har_fails():
    r = validate_har_dict(_har([]))
    assert not r.passed
    assert any("no entries" in w for w in r.warnings)


def test_only_static_assets_fails():
    r = validate_har_dict(_har([_entry("https://x.com/app.js", content_type="text/javascript")]))
    assert not r.passed
    assert r.api_call_count == 0


def test_json_get_counts_as_api_and_collects_domains():
    r = validate_har_dict(
        _har(
            [
                _entry("https://api.x.com/v1/search"),
                _entry("https://api.x.com/v1/book", method="POST", post='{"id":1}'),
            ]
        )
    )
    assert r.passed
    assert r.api_call_count == 2
    assert "api.x.com" in r.domains


def test_credential_leak_warns():
    r = validate_har_dict(
        _har([_entry("https://api.x.com/login", method="POST", post='{"password":"hunter2"}')])
    )
    assert r.passed  # leak is a warning, not a hard fail
    assert any("credential" in w.lower() for w in r.warnings)


def test_no_mutation_warns():
    r = validate_har_dict(
        _har([_entry("https://api.x.com/v1/a"), _entry("https://api.x.com/v1/b")])
    )
    assert r.passed
    assert any("mutation" in w.lower() for w in r.warnings)


def test_orchestration_modules_import():
    # Smoke: the new orchestration wrappers import and expose their entry points.
    from noui_core.capture import recording
    from noui_core.compile.login import compile_login_bundle
    from noui_core.compile.workflow import app_slug, compile_workflow_bundle

    assert callable(recording.start)
    assert callable(recording.fetch_bundle)
    assert callable(compile_workflow_bundle)
    assert callable(compile_login_bundle)
    assert app_slug("My App!") == "my-app"
