"""Integration tests for the full compile_workflow() pipeline.

These tests run the entire compilation from a synthetic HAR → generated server
tree, without requiring Tabby or the NoUI backend to be running.

Scenarios covered (matching the plan's acceptance criteria fixtures):
  1. Unauthenticated API (Google Flights style) — no auth_plan.json
  2. Static API-key app (Adopt Bank style) — static_secret_header strategy
  3. Session-cookie app (tabby_credentials strategy)
  4. Bearer token captured only during API calls (not login) — static strategy
  5. Profile UUID never used as runtime identifier — slug always wins
  6. Recorded non-auth headers (Accept) preserved in generated operations
  7. noui_runtime/auth.py uses correct 2-step Tabby flow (not old route)
  8. API.md is generated
  9. manifest.json schema v2 fields present
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.compile.har_to_tools import HarValidationError
from noui_core.compile.server_generator import compile_workflow

# ---------------------------------------------------------------------------
# HAR builders
# ---------------------------------------------------------------------------


def _har(entries: list[dict]) -> dict:
    return {"log": {"version": "1.2", "entries": entries}}


def _entry(
    url: str,
    method: str = "GET",
    status: int = 200,
    request_headers: list[dict] | None = None,
    response_headers: list[dict] | None = None,
    response_content: dict | None = None,
) -> dict:
    return {
        "request": {
            "method": method,
            "url": url,
            "headers": request_headers or [],
            "queryString": [],
            "postData": {},
        },
        "response": {
            "status": status,
            "headers": response_headers or [],
            "content": response_content or {"mimeType": "application/json", "text": "{}"},
        },
        "time": 100,
    }


def _auth_header(value: str = "Bearer tok123") -> dict:
    return {"name": "Authorization", "value": value}


def _accept_header(value: str = "application/json") -> dict:
    return {"name": "Accept", "value": value}


def _set_cookie(name: str = "session", value: str = "abc") -> dict:
    return {"name": "Set-Cookie", "value": f"{name}={value}; Path=/; HttpOnly"}


# ---------------------------------------------------------------------------
# Compile helper
# ---------------------------------------------------------------------------


def _compile(
    har: dict,
    *,
    session_name: str = "Test Workflow",
    app_slug: str = "test-app",
    profile_slug: str = "",
    profile_db_id: str = "",
    tabby_profile_id: str = "",
    execution_mode: str = "tabby",
) -> tuple[dict, dict[str, str]]:
    """Run compile_workflow into a temp dir; return (manifest, {rel_path: content})."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / app_slug / f"{app_slug}-test1234"
        manifest = compile_workflow(
            session_id="test1234-0000-0000-0000-000000000000",
            session_name=session_name,
            app_slug=app_slug,
            tabby_profile_id=tabby_profile_id,
            har=har,
            click_events=[],
            url_events=[],
            output_dir=str(out),
            profile_slug=profile_slug,
            profile_db_id=profile_db_id,
            execution_mode=execution_mode,
        )
        # Copy the whole tree into a stable dict of {relative_path: content}
        files = {
            str(p.relative_to(out)): p.read_text(encoding="utf-8")
            for p in out.rglob("*")
            if p.is_file()
        }
        return manifest, files


# ---------------------------------------------------------------------------
# Scenario 1: Unauthenticated API
# ---------------------------------------------------------------------------


class TestUnauthenticatedApi:
    """No auth headers, no cookies → no auth_plan.json, plain operations."""

    def setup_method(self) -> None:
        har = _har(
            [
                _entry("https://api.flights.example.com/v1/search", method="GET", status=200),
                _entry("https://api.flights.example.com/v1/prices", method="GET", status=200),
            ]
        )
        self.manifest, self.files = _compile(har, app_slug="flights")

    def test_no_auth_plan_json(self) -> None:
        assert "auth_plan.json" not in self.files, (
            "Unauthenticated server must not generate auth_plan.json"
        )

    def test_manifest_requires_auth_false(self) -> None:
        assert self.manifest["auth"]["requires_auth"] is False

    def test_manifest_schema_v2(self) -> None:
        assert self.manifest["schema_version"] == "2"

    def test_no_resolve_auth_in_operations(self) -> None:
        for path, content in self.files.items():
            if path.startswith("operations/"):
                assert "resolve_auth" not in content, (
                    f"{path}: unauthenticated op must not call resolve_auth()"
                )

    def test_noui_runtime_auth_py_present(self) -> None:
        assert "noui_runtime/auth.py" in self.files

    def test_api_md_generated(self) -> None:
        assert "API.md" in self.files, "API.md must be generated"


# ---------------------------------------------------------------------------
# Scenario 2: Static API-key app (Adopt Bank)
# ---------------------------------------------------------------------------


class TestStaticApiKeyApp:
    """Authorization header, no Set-Cookie → static_secret_header strategy."""

    def setup_method(self) -> None:
        har = _har(
            [
                _entry(
                    "https://nearby-nifty.eastus2.cloudapp.azure.com/clients/abc/documents",
                    request_headers=[_auth_header("Bearer abc123"), _accept_header()],
                    status=200,
                )
            ]
        )
        self.manifest, self.files = _compile(
            har,
            app_slug="example-bank",
            profile_slug="example-bank",
            profile_db_id="8fdadf43-01f5-48ab-905b-fc7e4d4b3c70",
        )

    def test_strategy_is_static_secret(self) -> None:
        assert self.manifest["auth"]["strategy"] == "static_secret_header", (
            "App with Authorization header and no Set-Cookie must get static_secret_header strategy"
        )

    def test_auth_plan_json_written(self) -> None:
        assert "auth_plan.json" in self.files

    def test_auth_plan_has_correct_env_var(self) -> None:
        plan = json.loads(self.files["auth_plan.json"])
        assert plan["fallbacks"], "static_secret_header plan must have fallbacks"
        fb = plan["fallbacks"][0]
        assert fb["secret_env_var"] == "EXAMPLE_BANK_API_KEY"
        assert "Bearer" in fb["value_template"], "Bearer prefix must be in value_template"
        # Must NOT embed the actual token value
        assert "abc123" not in json.dumps(plan), "Plan must not store the actual token"

    def test_profile_slug_not_uuid_in_plan(self) -> None:
        plan = json.loads(self.files["auth_plan.json"])
        assert plan["profile_slug"] == "example-bank"
        assert plan["profile_db_id"] == "8fdadf43-01f5-48ab-905b-fc7e4d4b3c70"
        assert plan["tabby_export"]["runtime_identifier"] == "example-bank", (
            "runtime_identifier must be the slug, never the UUID"
        )

    def test_operations_use_execute_fetch(self) -> None:
        """Default execution mode (tabby) wires operations through noui_runtime.execute."""
        for path, content in self.files.items():
            if (
                path.startswith("operations/")
                and path.endswith(".py")
                and path != "operations/__init__.py"
            ):
                assert "from noui_runtime.execute import" in content, (
                    f"{path} must import from noui_runtime.execute under CDP default"
                )
                assert "execute_fetch" in content, f"{path} must call execute_fetch()"
                assert "resolve_auth" not in content, (
                    f"{path}: resolve_auth() must not be used under CDP default"
                )
                assert "PROFILE_SLUG" in content, f"{path}: PROFILE_SLUG constant must be embedded"

    def test_manifest_profile_slug_not_uuid(self) -> None:
        auth = self.manifest["auth"]
        assert auth["profile_slug"] == "example-bank"
        assert auth["profile_db_id"] == "8fdadf43-01f5-48ab-905b-fc7e4d4b3c70"

    def test_accept_header_preserved(self) -> None:
        """Recorded Accept header must survive auth injection."""
        for path, content in self.files.items():
            if path.startswith("operations/") and path != "operations/__init__.py":
                assert "Accept" in content, (
                    f"{path}: recorded Accept header must appear in generated code"
                )


# ---------------------------------------------------------------------------
# Scenario 3: Session-cookie app (tabby_credentials)
# ---------------------------------------------------------------------------


class TestSessionCookieApp:
    """Set-Cookie in responses → tabby_credentials strategy."""

    def setup_method(self) -> None:
        har = _har(
            [
                _entry(
                    "https://app.example.com/api/users",
                    request_headers=[{"name": "Cookie", "value": "session=xyz"}],
                    response_headers=[_set_cookie("session", "xyz")],
                    status=200,
                )
            ]
        )
        self.manifest, self.files = _compile(
            har,
            app_slug="myapp",
            profile_slug="myapp",
        )

    def test_strategy_is_tabby_credentials(self) -> None:
        assert self.manifest["auth"]["strategy"] == "tabby_credentials"

    def test_auth_plan_no_static_fallbacks(self) -> None:
        if "auth_plan.json" in self.files:
            plan = json.loads(self.files["auth_plan.json"])
            assert plan.get("fallbacks", []) == [], (
                "tabby_credentials app must not have static secret fallbacks"
            )


# ---------------------------------------------------------------------------
# Scenario 4: Bearer token only in API calls (no login cookie) — static
# ---------------------------------------------------------------------------


class TestBearerOnlyInApiCalls:
    """API calls use Authorization: Bearer; login sets no cookies → static strategy."""

    def setup_method(self) -> None:
        har = _har(
            [
                # Login page — no auth
                _entry("https://app.example.com/login", status=200),
                # API calls with Bearer, no Set-Cookie
                _entry(
                    "https://app.example.com/api/data",
                    request_headers=[_auth_header("Bearer mytoken")],
                    status=200,
                ),
            ]
        )
        self.manifest, self.files = _compile(
            har,
            app_slug="bearer-app",
            profile_slug="bearer-app",
        )

    def test_static_strategy_when_bearer_no_set_cookie(self) -> None:
        if "auth_plan.json" in self.files:
            plan = json.loads(self.files["auth_plan.json"])
            assert plan["strategy"] == "static_secret_header"


# ---------------------------------------------------------------------------
# Scenario 5: Profile UUID must never appear in runtime credential path
# ---------------------------------------------------------------------------


class TestProfileSlugNotUuid:
    """The UUID is for admin ops; slug is for credentials/request at runtime."""

    def setup_method(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/items",
                    request_headers=[_auth_header("Bearer tok")],
                    status=200,
                )
            ]
        )
        self.manifest, self.files = _compile(
            har,
            app_slug="my-service",
            profile_slug="my-service",
            profile_db_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )

    def test_uuid_not_in_operation_source(self) -> None:
        uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        for path, content in self.files.items():
            if path.startswith("operations/"):
                assert uuid not in content, (
                    f"{path}: DB UUID must not appear in generated operation source; "
                    "use profile slug via auth_plan.json instead"
                )

    def test_uuid_not_in_auth_py(self) -> None:
        uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        auth_py = self.files.get("noui_runtime/auth.py", "")
        assert uuid not in auth_py, "noui_runtime/auth.py must not contain the profile DB UUID"

    def test_auth_plan_separates_slug_and_uuid(self) -> None:
        if "auth_plan.json" in self.files:
            plan = json.loads(self.files["auth_plan.json"])
            assert plan["profile_slug"] == "my-service"
            assert plan["profile_db_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            assert plan["tabby_export"]["runtime_identifier"] == "my-service"


# ---------------------------------------------------------------------------
# Scenario 6: Recorded non-auth headers preserved
# ---------------------------------------------------------------------------


class TestRecordedHeadersPreserved:
    """Accept, Content-Type, X-Request-ID etc. must not be dropped when auth is added."""

    def test_accept_and_content_type_in_auth_op(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/data",
                    request_headers=[
                        _auth_header("Bearer tok"),
                        _accept_header("application/json"),
                        {"name": "X-Api-Version", "value": "2024-01"},
                    ],
                    status=200,
                )
            ]
        )
        _, files = _compile(har, app_slug="app", profile_slug="app")
        for path, content in files.items():
            if path.startswith("operations/") and path != "operations/__init__.py":
                assert "Accept" in content, f"{path}: Accept header must be preserved"
                assert "application/json" in content, f"{path}: Accept value must be preserved"

    def test_no_auth_headers_dropped_for_unauth_op(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/public",
                    request_headers=[_accept_header("application/json")],
                    status=200,
                )
            ]
        )
        _, files = _compile(har, app_slug="public-api")
        for path, content in files.items():
            if path.startswith("operations/") and path != "operations/__init__.py":
                assert "Accept" in content, f"{path}: Accept must be kept even for unauth ops"


# ---------------------------------------------------------------------------
# Scenario 7: noui_runtime/auth.py uses correct Tabby flow
# ---------------------------------------------------------------------------


class TestGeneratedAuthPy:
    """The generated noui_runtime/auth.py must use the 2-step flow, not the old route."""

    def setup_method(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/data",
                    request_headers=[_auth_header()],
                    status=200,
                )
            ]
        )
        _, self.files = _compile(har, app_slug="app", profile_slug="app")
        self.auth_py = self.files.get("noui_runtime/auth.py", "")

    def test_no_old_runtime_credentials_route(self) -> None:
        assert "/runtime/credentials/" not in self.auth_py, (
            "noui_runtime/auth.py must not use the deprecated GET /runtime/credentials/ route"
        )

    def test_uses_agent_token_endpoint(self) -> None:
        assert "/auth/agent-token" in self.auth_py

    def test_uses_credentials_request_endpoint(self) -> None:
        assert "/credentials/request" in self.auth_py

    def test_loads_dotenv(self) -> None:
        assert "load_dotenv" in self.auth_py or "dotenv" in self.auth_py

    def test_reads_tabby_client_id(self) -> None:
        assert "TABBY_CLIENT_ID" in self.auth_py

    def test_resolve_auth_defined(self) -> None:
        assert "async def resolve_auth()" in self.auth_py

    def test_reads_auth_plan_json(self) -> None:
        assert "auth_plan.json" in self.auth_py

    def test_static_secret_handler_present(self) -> None:
        assert "static_secret_header" in self.auth_py


# ---------------------------------------------------------------------------
# Scenario 8 & 9: Output files and manifest schema v2
# ---------------------------------------------------------------------------


class TestOutputFiles:
    def setup_method(self) -> None:
        har = _har(
            [
                _entry("https://api.example.com/items", status=200),
            ]
        )
        self.manifest, self.files = _compile(har, app_slug="my-app")

    def test_server_py_present(self) -> None:
        assert "server.py" in self.files

    def test_tools_json_present(self) -> None:
        assert "tools.json" in self.files

    def test_manifest_json_present(self) -> None:
        assert "manifest.json" in self.files

    def test_api_md_present(self) -> None:
        assert "API.md" in self.files

    def test_manifest_schema_v2(self) -> None:
        assert self.manifest["schema_version"] == "2"

    def test_manifest_generator_version_v2(self) -> None:
        assert self.manifest["generation"]["generator_version"] == "v2"

    def test_manifest_has_v2_auth_fields(self) -> None:
        auth = self.manifest["auth"]
        assert "profile_slug" in auth, "manifest v2 must have profile_slug"
        assert "profile_db_id" in auth, "manifest v2 must have profile_db_id"
        assert "strategy" in auth, "manifest v2 must have strategy"
        assert "auth_plan_file" in auth, "manifest v2 must have auth_plan_file"

    def test_tools_json_is_valid(self) -> None:
        tools = json.loads(self.files["tools.json"])
        assert isinstance(tools, list)

    def test_server_py_imports_fastmcp(self) -> None:
        assert "FastMCP" in self.files["server.py"]

    def test_operations_init_present(self) -> None:
        assert "operations/__init__.py" in self.files

    def test_noui_runtime_init_present(self) -> None:
        assert "noui_runtime/__init__.py" in self.files


# ---------------------------------------------------------------------------
# Scenario 10: CDP execution mode is the default
# ---------------------------------------------------------------------------


class TestCdpIsDefault:
    """Not passing execution_mode must produce execute-adapter operations and execute.py runtime."""

    def setup_method(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/items",
                    request_headers=[_auth_header("Bearer tok"), _accept_header()],
                    status=200,
                )
            ]
        )
        self.manifest, self.files = _compile(har, app_slug="defapp", profile_slug="defapp")

    def test_execute_runtime_module_written(self) -> None:
        assert "noui_runtime/execute.py" in self.files, (
            "CDP default must write noui_runtime/execute.py"
        )

    def test_no_cdp_runtime_written(self) -> None:
        assert "noui_runtime/cdp.py" not in self.files, "CDP mode should no longer write cdp.py"

    def test_operations_import_execute_fetch(self) -> None:
        op_paths = [
            p
            for p in self.files
            if p.startswith("operations/") and p.endswith(".py") and p != "operations/__init__.py"
        ]
        assert op_paths, "expected at least one operation"
        for p in op_paths:
            assert "from noui_runtime.execute import" in self.files[p]
            assert "execute_fetch" in self.files[p]

    def test_no_httpx_in_operations(self) -> None:
        for p, content in self.files.items():
            if p.startswith("operations/") and p.endswith(".py") and p != "operations/__init__.py":
                assert "import httpx" not in content, (
                    f"{p}: httpx must not appear in CDP-default operations"
                )

    def test_no_websockets_in_operations(self) -> None:
        for p, content in self.files.items():
            if p.startswith("operations/") and p.endswith(".py") and p != "operations/__init__.py":
                assert "websockets" not in content, f"{p}: websockets must not appear in operations"

    def test_operations_have_profile_slug(self) -> None:
        for p, content in self.files.items():
            if p.startswith("operations/") and p.endswith(".py") and p != "operations/__init__.py":
                assert "PROFILE_SLUG" in content, f"{p}: operations must reference PROFILE_SLUG"

    def test_manifest_execution_strategy(self) -> None:
        assert self.manifest["auth"]["execution_strategy"] == "tabby_execute_fetch"

    def test_manifest_auth_strategy_unchanged(self) -> None:
        """auth.strategy continues to describe the credential-source strategy."""
        assert self.manifest["auth"]["strategy"] == "static_secret_header"

    def test_execute_runtime_compiles(self) -> None:
        import py_compile
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(self.files["noui_runtime/execute.py"])
            path = f.name
        py_compile.compile(path, doraise=True)


# ---------------------------------------------------------------------------
# Scenario 11: execution_mode="http" preserves the legacy template
# ---------------------------------------------------------------------------


class TestHttpExecutionMode:
    """Legacy opt-in: httpx + resolve_auth, no cdp.py written, execution_strategy mirrors auth.strategy."""

    def setup_method(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/items",
                    request_headers=[_auth_header("Bearer tok"), _accept_header()],
                    status=200,
                )
            ]
        )
        self.manifest, self.files = _compile(
            har, app_slug="httpapp", profile_slug="httpapp", execution_mode="http"
        )

    def test_operations_use_httpx(self) -> None:
        for p, content in self.files.items():
            if p.startswith("operations/") and p.endswith(".py") and p != "operations/__init__.py":
                assert "import httpx" in content
                assert "resolve_auth" in content
                assert "from noui_runtime.cdp" not in content
                assert "from noui_runtime.execute" not in content

    def test_no_execute_runtime_written(self) -> None:
        assert "noui_runtime/execute.py" not in self.files
        assert "noui_runtime/cdp.py" not in self.files

    def test_manifest_execution_strategy_mirrors_auth_strategy(self) -> None:
        auth = self.manifest["auth"]
        assert auth["execution_strategy"] == auth["strategy"]


# ---------------------------------------------------------------------------
# Scenario 12: execution_mode validation
# ---------------------------------------------------------------------------


class TestExecutionModeValidation:
    def test_invalid_mode_raises(self) -> None:
        import pytest

        har = _har([_entry("https://api.example.com/x")])
        with pytest.raises(ValueError, match="execution_mode"):
            _compile(har, app_slug="bad", execution_mode="grpc")


# ---------------------------------------------------------------------------
# Scenario 13: Empty / non-API HARs must not produce a zero-tool server
# ---------------------------------------------------------------------------


class TestEmptyHarRejected:
    """Empty or non-API HARs must raise HarValidationError instead of writing
    a successful-looking but useless MCP server tree."""

    def test_empty_entries_raises(self) -> None:
        with pytest.raises(HarValidationError, match="no entries"):
            _compile(_har([]), app_slug="empty-app")

    def test_only_static_assets_raises(self) -> None:
        har = _har(
            [
                _entry("https://cdn.example.com/bundle.js", status=200),
                _entry("https://cdn.example.com/style.css", status=200),
            ]
        )
        with pytest.raises(HarValidationError, match="none look like API calls"):
            _compile(har, app_slug="static-only")

    def test_missing_log_raises(self) -> None:
        with pytest.raises(HarValidationError, match="log.entries"):
            _compile({}, app_slug="malformed")

    def test_no_server_files_written_on_failure(self) -> None:
        """When validation fails, callers must not see a half-written server tree."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "empty-app" / "empty-app-test1234"
            with pytest.raises(HarValidationError):
                compile_workflow(
                    session_id="test1234-0000-0000-0000-000000000000",
                    session_name="Empty",
                    app_slug="empty-app",
                    tabby_profile_id="",
                    har=_har([]),
                    click_events=[],
                    url_events=[],
                    output_dir=str(out),
                )
            # The compiler may or may not have created the parent directory
            # depending on where it raises, but it must not have written any
            # generated source files (server.py, tools.json, manifest.json).
            if out.exists():
                generated = {p.name for p in out.rglob("*") if p.is_file()}
                forbidden = {"server.py", "tools.json", "manifest.json", "API.md"}
                assert not (generated & forbidden), (
                    f"Compiler wrote artifacts on validation failure: {generated & forbidden}"
                )
