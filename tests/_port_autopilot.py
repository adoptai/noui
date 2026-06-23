"""Tests for autopilot recording components.

Covers:
  1. Request normalization (derive missing fields)
  2. Credential store and redaction
  3. Capture validation against sample HARs:
     - Valid JSON API capture
     - No API calls (static-only)
     - Login-only capture (credential leak)
     - Unrelated domain capture
     - Empty HAR
  4. App slug and workflow name derivation
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from backend.autopilot.capture_validator import validate_har
from backend.autopilot.controller import (
    derive_app_slug,
    derive_workflow_name,
    normalize_request_fields,
)
from backend.autopilot.credentials import (
    clear_credentials,
    get_credentials,
    redact_dict,
    redact_text,
    store_credentials,
)

# ---------------------------------------------------------------------------
# HAR builders
# ---------------------------------------------------------------------------


def _har(entries: list[dict]) -> dict:
    return {"log": {"version": "1.2", "entries": entries}}


def _entry(
    method: str = "GET",
    url: str = "https://api.example.com/data",
    status: int = 200,
    resp_content_type: str = "application/json",
    post_body: str | None = None,
    post_content_type: str = "application/json",
) -> dict:
    entry = {
        "request": {
            "method": method,
            "url": url,
            "headers": [],
            "queryString": [],
        },
        "response": {
            "status": status,
            "headers": [{"name": "content-type", "value": resp_content_type}],
            "content": {"size": 100, "mimeType": resp_content_type},
        },
    }
    if post_body:
        entry["request"]["postData"] = {
            "mimeType": post_content_type,
            "text": post_body,
        }
    return entry


def _write_har(path: Path, har: dict) -> None:
    path.write_text(json.dumps(har))


# ---------------------------------------------------------------------------
# Tests: Request normalization
# ---------------------------------------------------------------------------


def test_normalize_derives_login_url():
    updates = normalize_request_fields(
        website_url="https://app.example.com/dashboard",
        task_description="Create a draft invoice",
    )
    assert "login_url" in updates
    assert updates["login_url"] == "https://app.example.com/login"


def test_normalize_preserves_explicit_login_url():
    updates = normalize_request_fields(
        website_url="https://app.example.com/dashboard",
        login_url="https://app.example.com/auth",
        task_description="Create a draft invoice",
    )
    assert "login_url" not in updates


def test_normalize_derives_stop_condition():
    updates = normalize_request_fields(
        website_url="https://app.example.com",
        task_description="Create a draft invoice",
    )
    assert "stop_condition" in updates
    assert "Submit" in updates["stop_condition"]


def test_normalize_preserves_explicit_stop_condition():
    updates = normalize_request_fields(
        website_url="https://app.example.com",
        task_description="Create a draft invoice",
        stop_condition="Stop after draft save",
    )
    assert "stop_condition" not in updates


def test_normalize_derives_success_condition():
    updates = normalize_request_fields(
        website_url="https://app.example.com",
        task_description="Create a draft invoice for Acme Corp",
    )
    assert "success_condition" in updates
    assert "draft invoice" in updates["success_condition"].lower()


# ---------------------------------------------------------------------------
# Tests: App slug and workflow name derivation
# ---------------------------------------------------------------------------


def test_derive_app_slug():
    assert derive_app_slug("https://www.example.com/login") == "example"
    assert derive_app_slug("https://api.stripe.com/v1/charges") == "api"
    assert derive_app_slug("https://my-app.herokuapp.com") == "my-app"


def test_derive_workflow_name():
    assert derive_workflow_name("Create a draft invoice") == "Create a draft invoice"
    name = derive_workflow_name(
        "Create a draft invoice for Acme Corp for one hundred dollars due next Friday"
    )
    assert name.endswith("...")
    assert len(name.split()) <= 7  # 6 words + "..."


# ---------------------------------------------------------------------------
# Tests: Credential store and redaction
# ---------------------------------------------------------------------------


def test_store_and_retrieve_credentials():
    store_credentials("test-run-1", "user@test.com", "s3cret!")
    creds = get_credentials("test-run-1")
    assert creds is not None
    assert creds["username"] == "user@test.com"
    assert creds["password"] == "s3cret!"
    clear_credentials("test-run-1")
    assert get_credentials("test-run-1") is None


def test_redact_text():
    store_credentials("test-run-2", "admin", "SuperSecret123")
    result = redact_text("The password is SuperSecret123 for user admin", "test-run-2")
    assert "SuperSecret123" not in result
    assert "admin" not in result
    assert "[REDACTED]" in result
    clear_credentials("test-run-2")


def test_redact_text_no_credentials():
    result = redact_text("safe text", "nonexistent-run")
    assert result == "safe text"


def test_redact_dict():
    store_credentials("test-run-3", "user", "pass123")
    data = {
        "url": "https://example.com",
        "password": "pass123",
        "authorization": "Bearer token",
        "nested": {"api_key": "key123", "value": "pass123 in value"},
    }
    redacted = redact_dict(data, "test-run-3")
    assert redacted["password"] == "[REDACTED]"
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert "pass123" not in redacted["nested"]["value"]
    clear_credentials("test-run-3")


# ---------------------------------------------------------------------------
# Tests: Capture validation
# ---------------------------------------------------------------------------


def test_validate_valid_api_capture():
    """HAR with JSON API calls should pass."""
    with tempfile.NamedTemporaryFile(suffix=".har", delete=False, mode="w") as f:
        har = _har(
            [
                _entry("GET", "https://api.example.com/invoices", 200),
                _entry(
                    "POST", "https://api.example.com/invoices", 201, post_body='{"amount": 100}'
                ),
                _entry("GET", "https://api.example.com/customers", 200),
            ]
        )
        _write_har(Path(f.name), har)
        result = validate_har(f.name, "https://api.example.com")

    assert result.passed
    assert result.api_call_count >= 2
    assert "api.example.com" in result.domains


def test_validate_no_api_calls():
    """HAR with only static assets should fail."""
    with tempfile.NamedTemporaryFile(suffix=".har", delete=False, mode="w") as f:
        har = _har(
            [
                _entry("GET", "https://example.com/style.css", 200, resp_content_type="text/css"),
                _entry(
                    "GET",
                    "https://example.com/app.js",
                    200,
                    resp_content_type="application/javascript",
                ),
                _entry("GET", "https://example.com/logo.png", 200, resp_content_type="image/png"),
            ]
        )
        _write_har(Path(f.name), har)
        result = validate_har(f.name)

    assert not result.passed
    assert result.api_call_count == 0
    assert any("api calls" in w.lower() for w in result.warnings)


def test_validate_credential_leak():
    """HAR with password-like keys in POST body should warn."""
    with tempfile.NamedTemporaryFile(suffix=".har", delete=False, mode="w") as f:
        har = _har(
            [
                _entry(
                    "POST",
                    "https://api.example.com/auth/login",
                    200,
                    post_body='{"username": "admin", "password": "secret123"}',
                ),
                _entry("GET", "https://api.example.com/dashboard", 200),
            ]
        )
        _write_har(Path(f.name), har)
        result = validate_har(f.name)

    assert result.passed  # passes but with warnings
    assert any("credential" in w.lower() for w in result.warnings)


def test_validate_unrelated_domain():
    """HAR with requests to unrelated domains should warn."""
    with tempfile.NamedTemporaryFile(suffix=".har", delete=False, mode="w") as f:
        har = _har(
            [
                _entry("POST", "https://api.example.com/data", 200, post_body='{"key": "val"}'),
                _entry("GET", "https://analytics.google.com/track", 200),
                _entry(
                    "GET",
                    "https://cdn.jsdelivr.net/npm/react",
                    200,
                    resp_content_type="application/javascript",
                ),
            ]
        )
        _write_har(Path(f.name), har)
        result = validate_har(f.name, "https://api.example.com")

    assert result.passed
    assert any("unrelated" in w.lower() for w in result.warnings)


def test_validate_empty_har():
    """HAR with no entries should fail."""
    with tempfile.NamedTemporaryFile(suffix=".har", delete=False, mode="w") as f:
        har = _har([])
        _write_har(Path(f.name), har)
        result = validate_har(f.name)

    assert not result.passed
    assert any("no entries" in w.lower() for w in result.warnings)


def test_validate_missing_file():
    """Non-existent HAR file should fail."""
    result = validate_har("/nonexistent/path/to/file.har")
    assert not result.passed
    assert any("does not exist" in w.lower() for w in result.warnings)


def test_validate_no_mutation_calls():
    """HAR with only GET API calls should warn about no mutations."""
    with tempfile.NamedTemporaryFile(suffix=".har", delete=False, mode="w") as f:
        har = _har(
            [
                _entry("GET", "https://api.example.com/users", 200),
                _entry("GET", "https://api.example.com/products", 200),
            ]
        )
        _write_har(Path(f.name), har)
        result = validate_har(f.name)

    assert result.passed
    assert any("no mutation" in w.lower() for w in result.warnings)


def test_validate_form_param_credential_leak():
    """HAR with password= in form params should warn."""
    with tempfile.NamedTemporaryFile(suffix=".har", delete=False, mode="w") as f:
        har = _har(
            [
                _entry(
                    "POST",
                    "https://example.com/login",
                    302,
                    post_body="username=admin&password=secret",
                    post_content_type="application/x-www-form-urlencoded",
                ),
                _entry("GET", "https://example.com/api/data", 200),
            ]
        )
        _write_har(Path(f.name), har)
        result = validate_har(f.name)

    assert result.passed
    assert any("credential" in w.lower() for w in result.warnings)
