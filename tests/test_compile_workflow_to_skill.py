"""Integration tests for compile_workflow_to_skill.

Mirror of test_compile_workflow.py but asserts skill-shaped output:
SKILL.md frontmatter, executable CLI operations, skill manifest schema.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.compile.har_to_tools import HarValidationError
from noui_core.compile.skill_generator import compile_workflow_to_skill


def _har(entries: list[dict]) -> dict:
    return {"log": {"version": "1.2", "entries": entries}}


def _entry(
    url: str,
    method: str = "GET",
    status: int = 200,
    request_headers: list[dict] | None = None,
    response_headers: list[dict] | None = None,
    post_data: dict | None = None,
) -> dict:
    return {
        "request": {
            "method": method,
            "url": url,
            "headers": request_headers or [],
            "queryString": [],
            "postData": post_data or {},
        },
        "response": {
            "status": status,
            "headers": response_headers or [],
            "content": {"mimeType": "application/json", "text": "{}"},
        },
        "time": 100,
    }


def _compile(
    har: dict,
    *,
    app_slug: str = "example-app",
    session_name: str = "Example Workflow",
    profile_slug: str = "",
    description_override: str = "",
    execution_mode: str = "tabby",
) -> tuple[Path, dict]:
    tmp = Path(tempfile.mkdtemp())
    manifest = compile_workflow_to_skill(
        session_id="abcdef12-3456-7890-abcd-ef1234567890",
        session_name=session_name,
        app_slug=app_slug,
        tabby_profile_id=profile_slug,
        profile_slug=profile_slug,
        har=har,
        click_events=[],
        url_events=[],
        output_dir=str(tmp),
        description_override=description_override,
        execution_mode=execution_mode,
    )
    return tmp, manifest


# ---------------------------------------------------------------------------
# File tree
# ---------------------------------------------------------------------------


class TestSkillOutputTree:
    def test_unauth_minimum_tree(self) -> None:
        out, manifest = _compile(
            _har([_entry("https://api.example.com/v1/widgets?id=1")]),
        )
        assert (out / "SKILL.md").is_file()
        assert (out / "manifest.json").is_file()
        assert (out / "API.md").is_file()
        assert (out / "noui_runtime" / "auth.py").is_file()
        assert (out / "operations" / "__init__.py").is_file()
        # No auth_plan.json in the unauth case
        assert not (out / "auth_plan.json").exists()
        # At least one operation
        op_files = list((out / "operations").glob("*.py"))
        assert any(p.name != "__init__.py" for p in op_files)

    def test_auth_tree_includes_auth_plan(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/v1/widgets",
                    method="POST",
                    request_headers=[
                        {"name": "Authorization", "value": "Bearer tok"},
                        {"name": "Content-Type", "value": "application/json"},
                    ],
                    post_data={"mimeType": "application/json", "text": '{"name":"x"}'},
                    status=201,
                ),
            ]
        )
        out, manifest = _compile(har, profile_slug="example")
        assert (out / "auth_plan.json").is_file()
        auth_plan = json.loads((out / "auth_plan.json").read_text())
        assert auth_plan.get("profile_slug") == "example"


# ---------------------------------------------------------------------------
# SKILL.md frontmatter + body
# ---------------------------------------------------------------------------


class TestSkillMd:
    def test_frontmatter_has_name_and_description(self) -> None:
        out, _ = _compile(_har([_entry("https://api.example.com/v1/widgets")]))
        body = (out / "SKILL.md").read_text()
        assert body.startswith("---\n")
        # Split frontmatter
        end = body.index("\n---\n", 4)
        frontmatter = body[4:end]
        assert "name: example-app" in frontmatter
        # Description line present and non-empty
        desc_line = next(
            (line for line in frontmatter.splitlines() if line.startswith("description:")),
            "",
        )
        assert desc_line.strip() != "description:"

    def test_description_override_wins(self) -> None:
        out, _ = _compile(
            _har([_entry("https://api.example.com/v1/widgets")]),
            description_override="Custom hand-written description that overrides the heuristic.",
        )
        body = (out / "SKILL.md").read_text()
        assert "Custom hand-written description that overrides the heuristic." in body

    def test_body_lists_every_operation(self) -> None:
        out, manifest = _compile(
            _har(
                [
                    _entry("https://api.example.com/v1/a"),
                    _entry("https://api.example.com/v1/b"),
                ]
            )
        )
        body = (out / "SKILL.md").read_text()
        for op in manifest["operations"]:
            assert f"### `{op['name']}`" in body

    def test_auth_body_mentions_tabby_session(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/widgets",
                    request_headers=[{"name": "Authorization", "value": "Bearer t"}],
                )
            ]
        )
        out, _ = _compile(har, profile_slug="example")
        body = (out / "SKILL.md").read_text()
        assert "tabby session ensure" in body.lower() or "Tabby session" in body


# ---------------------------------------------------------------------------
# Operation scripts are standalone executables
# ---------------------------------------------------------------------------


class TestOperationScripts:
    def test_all_operations_py_compile(self) -> None:
        out, _ = _compile(_har([_entry("https://api.example.com/v1/widgets?id=1")]))
        for py_file in (out / "operations").glob("*.py"):
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", str(py_file)],
                capture_output=True,
            )
            assert r.returncode == 0, f"py_compile failed for {py_file}: {r.stderr.decode()}"
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(out / "noui_runtime" / "auth.py")],
            capture_output=True,
        )
        assert r.returncode == 0, r.stderr.decode()

    def test_operation_cli_help(self) -> None:
        out, manifest = _compile(_har([_entry("https://api.example.com/v1/widgets?id=1")]))
        op_name = manifest["operations"][0]["name"]
        op_file = out / "operations" / f"{op_name}.py"
        r = subprocess.run([sys.executable, str(op_file), "--help"], capture_output=True)
        assert r.returncode == 0, r.stderr.decode()
        stdout = r.stdout.decode()
        assert "usage:" in stdout

    def test_operation_has_execute_coroutine(self) -> None:
        out, manifest = _compile(_har([_entry("https://api.example.com/v1/widgets")]))
        op_name = manifest["operations"][0]["name"]
        src = (out / "operations" / f"{op_name}.py").read_text()
        assert "async def execute(" in src
        assert 'if __name__ == "__main__":' in src


# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------


class TestSkillManifest:
    def test_manifest_runtime_type(self) -> None:
        _, manifest = _compile(_har([_entry("https://api.example.com/v1/widgets")]))
        assert manifest["runtime"]["type"] == "claude-code-skill"
        assert manifest["runtime"]["entrypoint"] == "SKILL.md"
        assert manifest["runtime"]["operation_style"] == "subprocess-cli"

    def test_manifest_skill_id_equals_app_slug(self) -> None:
        _, manifest = _compile(
            _har([_entry("https://api.example.com/v1/widgets")]), app_slug="my-app"
        )
        assert manifest["skill_id"] == "my-app"

    def test_manifest_operations_shape(self) -> None:
        _, manifest = _compile(
            _har(
                [
                    _entry(
                        "https://api.example.com/v1/widgets?id=1",
                        method="GET",
                    )
                ]
            )
        )
        ops = manifest["operations"]
        assert ops, "manifest.operations should not be empty"
        op = ops[0]
        assert "name" in op
        assert "module" in op
        assert op["entry"] == "execute"
        assert "args" in op

    def test_manifest_auth_fields_when_authed(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/v1/widgets",
                    request_headers=[{"name": "Authorization", "value": "Bearer t"}],
                )
            ]
        )
        _, manifest = _compile(har, profile_slug="example")
        assert manifest["auth"]["requires_auth"] is True
        assert manifest["auth"]["profile_slug"] == "example"
        assert manifest["auth"]["auth_plan_file"] == "auth_plan.json"


# ---------------------------------------------------------------------------
# Shared runtime — identical bytes for MCP and Skill outputs
# ---------------------------------------------------------------------------


class TestSharedRuntime:
    def test_auth_py_matches_mcp_output(self) -> None:
        """The noui_runtime/auth.py generated for a Skill must be byte-identical to
        the one generated for the equivalent MCP server — that's the whole point
        of compiler/runtime/."""
        from noui_core.compile.server_generator import compile_workflow

        har = _har([_entry("https://api.example.com/v1/widgets")])
        skill_out = Path(tempfile.mkdtemp())
        mcp_out = Path(tempfile.mkdtemp())
        compile_workflow_to_skill(
            session_id="abcdef12-3456-7890-abcd-ef1234567890",
            session_name="Example",
            app_slug="example",
            tabby_profile_id="",
            har=har,
            click_events=[],
            url_events=[],
            output_dir=str(skill_out),
        )
        compile_workflow(
            session_id="abcdef12-3456-7890-abcd-ef1234567890",
            session_name="Example",
            app_slug="example",
            tabby_profile_id="",
            har=har,
            click_events=[],
            url_events=[],
            output_dir=str(mcp_out),
        )
        skill_auth = (skill_out / "noui_runtime" / "auth.py").read_bytes()
        mcp_auth = (mcp_out / "noui_runtime" / "auth.py").read_bytes()
        assert skill_auth == mcp_auth

    def test_execute_py_matches_mcp_output(self) -> None:
        """noui_runtime/execute.py must be byte-identical for skill and MCP outputs."""
        from noui_core.compile.server_generator import compile_workflow

        har = _har([_entry("https://api.example.com/v1/widgets")])
        skill_out = Path(tempfile.mkdtemp())
        mcp_out = Path(tempfile.mkdtemp())
        compile_workflow_to_skill(
            session_id="abcdef12-3456-7890-abcd-ef1234567890",
            session_name="Example",
            app_slug="example",
            tabby_profile_id="",
            har=har,
            click_events=[],
            url_events=[],
            output_dir=str(skill_out),
        )
        compile_workflow(
            session_id="abcdef12-3456-7890-abcd-ef1234567890",
            session_name="Example",
            app_slug="example",
            tabby_profile_id="",
            har=har,
            click_events=[],
            url_events=[],
            output_dir=str(mcp_out),
        )
        skill_execute = (skill_out / "noui_runtime" / "execute.py").read_bytes()
        mcp_execute = (mcp_out / "noui_runtime" / "execute.py").read_bytes()
        assert skill_execute == mcp_execute


# ---------------------------------------------------------------------------
# Execution mode — tabby default and HTTP opt-in
# ---------------------------------------------------------------------------


class TestSkillCdpDefault:
    """Skill compiler must match the MCP compiler's execute-fetch default."""

    def test_execute_runtime_written(self) -> None:
        out, _ = _compile(_har([_entry("https://api.example.com/v1/widgets")]))
        assert (out / "noui_runtime" / "execute.py").is_file()
        assert not (out / "noui_runtime" / "cdp.py").exists()

    def test_operations_import_execute_fetch(self) -> None:
        out, manifest = _compile(_har([_entry("https://api.example.com/v1/widgets?id=1")]))
        op_name = manifest["operations"][0]["name"]
        src = (out / "operations" / f"{op_name}.py").read_text()
        assert "from noui_runtime.execute import" in src
        assert "execute_fetch" in src
        assert "import httpx" not in src
        assert "resolve_auth" not in src

    def test_manifest_execution_strategy(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/widgets",
                    request_headers=[{"name": "Authorization", "value": "Bearer t"}],
                )
            ]
        )
        _, manifest = _compile(har, profile_slug="example")
        assert manifest["auth"]["execution_strategy"] == "tabby_execute_fetch"


class TestSkillHttpExecutionMode:
    """execution_mode='http' keeps the legacy template."""

    def test_operations_use_httpx(self) -> None:
        out, manifest = _compile(
            _har(
                [
                    _entry(
                        "https://api.example.com/widgets",
                        request_headers=[{"name": "Authorization", "value": "Bearer t"}],
                    )
                ]
            ),
            profile_slug="example",
            execution_mode="http",
        )
        op_name = manifest["operations"][0]["name"]
        src = (out / "operations" / f"{op_name}.py").read_text()
        assert "import httpx" in src
        assert "resolve_auth" in src
        assert "from noui_runtime.cdp" not in src
        assert "from noui_runtime.execute" not in src

    def test_no_execute_runtime_written(self) -> None:
        out, _ = _compile(
            _har([_entry("https://api.example.com/widgets")]),
            execution_mode="http",
        )
        assert not (out / "noui_runtime" / "execute.py").exists()
        assert not (out / "noui_runtime" / "cdp.py").exists()

    def test_manifest_execution_strategy_mirrors_auth_strategy(self) -> None:
        har = _har(
            [
                _entry(
                    "https://api.example.com/widgets",
                    request_headers=[{"name": "Authorization", "value": "Bearer t"}],
                )
            ]
        )
        _, manifest = _compile(har, profile_slug="example", execution_mode="http")
        auth = manifest["auth"]
        assert auth["execution_strategy"] == auth["strategy"]


class TestSkillExecutionModeValidation:
    def test_invalid_mode_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="execution_mode"):
            _compile(
                _har([_entry("https://api.example.com/widgets")]),
                execution_mode="grpc",
            )


# ---------------------------------------------------------------------------
# Execution mode — harness (Agent Harness target, no transport code)
# ---------------------------------------------------------------------------


def _cookie_auth_har() -> dict:
    """HAR whose auth signal is session cookies → tabby_credentials strategy."""
    entry = _entry(
        "https://api.example.com/v1/widgets?id=1",
        request_headers=[{"name": "Cookie", "value": "session=abc"}],
        response_headers=[{"name": "Set-Cookie", "value": "session=abc; Path=/"}],
    )
    entry["request"]["queryString"] = [{"name": "id", "value": "1"}]
    return _har([entry])


class TestSkillHarnessExecutionMode:
    """execution_mode='harness' ships recipes, never transport code."""

    def test_tree_has_no_transport_code(self) -> None:
        out, _ = _compile(_cookie_auth_har(), profile_slug="example", execution_mode="harness")
        assert (out / "SKILL.md").is_file()
        assert (out / "manifest.json").is_file()
        assert (out / "API.md").is_file()
        assert (out / "operations.json").is_file()
        assert (out / "auth_plan.json").is_file()
        # The whole point: nothing executable, no Python environment.
        assert not (out / "noui_runtime").exists()
        assert not (out / "operations").exists()
        assert not (out / "pyproject.toml").exists()
        assert not (out / ".python-version").exists()

    def test_skill_md_renders_call_web_api_cards(self) -> None:
        out, manifest = _compile(
            _cookie_auth_har(), profile_slug="example", execution_mode="harness"
        )
        body = (out / "SKILL.md").read_text()
        assert "call_web_api" in body
        assert '"app": "example"' in body
        for op in manifest["operations"]:
            assert f"### `{op['name']}`" in body
        # No venv/script mechanics may leak into a harness skill.
        assert ".venv" not in body
        assert "uv sync" not in body
        assert "operations/" not in body

    def test_unauth_skill_md_renders_curl_cards(self) -> None:
        out, _ = _compile(
            _har([_entry("https://api.example.com/v1/widgets?id=1")]),
            execution_mode="harness",
        )
        body = (out / "SKILL.md").read_text()
        assert "curl" in body
        assert '"app"' not in body

    def test_operations_json_recipes(self) -> None:
        out, manifest = _compile(
            _cookie_auth_har(), profile_slug="example", execution_mode="harness"
        )
        recipes = json.loads((out / "operations.json").read_text())["operations"]
        assert len(recipes) == len(manifest["operations"])
        recipe = recipes[0]
        assert recipe["tool"] == "call_web_api"
        assert recipe["app"] == "example"
        # Explicit per-op auth binding: the broker resolves the signed-in user's
        # federated Tabby bearer for this profile (no secret in the recipe).
        assert recipe["auth"] == {"type": "tabby_per_user", "profile": "example"}
        assert recipe["url_template"].startswith("https://api.example.com")
        assert recipe["method"] == "GET"
        assert any(p["name"] == "id" for p in recipe["query_params"])

    def test_manifest_harness_fields(self) -> None:
        _, manifest = _compile(_cookie_auth_har(), profile_slug="example", execution_mode="harness")
        assert manifest["auth"]["execution_strategy"] == "harness_call_web_api"
        assert manifest["runtime"]["type"] == "agent-harness-skill"
        assert manifest["runtime"]["operation_style"] == "call_web_api"
        assert "python_executable" not in manifest["runtime"]
        op = manifest["operations"][0]
        assert op["recipe"] == "operations.json"
        assert op["tool"] == "call_web_api"
        assert "module" not in op

    def test_static_secret_header_emits_secret_placeholder(self) -> None:
        """G1 closed: a static-API-key workflow emits a ${SECRET:name} placeholder
        the harness resolves server-side — the real key never appears."""
        har = _har(
            [
                _entry(
                    "https://api.example.com/v1/widgets",
                    request_headers=[{"name": "Authorization", "value": "Bearer SUPERSECRET"}],
                )
            ]
        )
        out, manifest = _compile(har, profile_slug="example", execution_mode="harness")
        recipe = json.loads((out / "operations.json").read_text())["operations"][0]
        auth = recipe["headers"]["Authorization"]
        assert auth.startswith("Bearer ${SECRET:")
        assert auth.endswith("}")
        # The recorded secret value must NOT leak anywhere in the skill.
        assert "SUPERSECRET" not in (out / "operations.json").read_text()
        assert "SUPERSECRET" not in (out / "SKILL.md").read_text()
        # The manifest records which secrets an admin must configure.
        assert manifest["secrets_required"]
        secret = manifest["secrets_required"][0]
        assert f"${{SECRET:{secret}}}" in (out / "operations.json").read_text()
        # SKILL.md guides the agent to pass the placeholder, not a real key.
        body = (out / "SKILL.md").read_text()
        assert "${SECRET:" in body
        assert "never a real key" in body

    def test_browser_managed_headers_dropped_from_recipes(self) -> None:
        """call_web_api runs in a real browser — recorded fingerprint headers are noise."""
        entry = _entry(
            "https://api.example.com/v1/widgets",
            request_headers=[
                {"name": "User-Agent", "value": "Mozilla/5.0"},
                {"name": "sec-ch-ua-platform", "value": '"Linux"'},
                {"name": "Sec-Fetch-Mode", "value": "cors"},
                {"name": "Cookie", "value": "session=abc"},
                {"name": "Accept", "value": "application/json"},
                {"name": "X-Custom-Token", "value": "keep-me"},
            ],
            response_headers=[{"name": "Set-Cookie", "value": "session=abc; Path=/"}],
        )
        out, _ = _compile(_har([entry]), profile_slug="example", execution_mode="harness")
        recipe = json.loads((out / "operations.json").read_text())["operations"][0]
        headers = recipe.get("headers", {})
        assert "Accept" in headers
        assert "X-Custom-Token" in headers
        assert "User-Agent" not in headers
        assert "sec-ch-ua-platform" not in headers
        assert "Sec-Fetch-Mode" not in headers
        assert "Cookie" not in headers

    def test_cookie_auth_needs_no_secrets(self) -> None:
        """Cookie-auth (tabby_credentials) skills carry no ${SECRET} placeholder —
        the browser session supplies the cookies, nothing to configure."""
        out, manifest = _compile(
            _cookie_auth_har(), profile_slug="example", execution_mode="harness"
        )
        assert "secrets_required" not in manifest
        assert "${SECRET:" not in (out / "operations.json").read_text()


# ---------------------------------------------------------------------------
# Empty / non-API HARs must be rejected at the same layer as the MCP compiler
# ---------------------------------------------------------------------------


class TestEmptyHarRejected:
    def test_empty_entries_raises(self) -> None:
        with pytest.raises(HarValidationError, match="no entries"):
            _compile(_har([]), app_slug="empty-skill")

    def test_only_static_assets_raises(self) -> None:
        har = _har(
            [
                _entry("https://cdn.example.com/bundle.js", status=200),
                _entry("https://cdn.example.com/style.css", status=200),
            ]
        )
        with pytest.raises(HarValidationError, match="none look like API calls"):
            _compile(har, app_slug="static-only-skill")

    def test_missing_log_raises(self) -> None:
        with pytest.raises(HarValidationError, match="log.entries"):
            _compile({}, app_slug="malformed-skill")

    def test_no_skill_files_written_on_failure(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        with pytest.raises(HarValidationError):
            compile_workflow_to_skill(
                session_id="abcdef12-3456-7890-abcd-ef1234567890",
                session_name="Empty",
                app_slug="empty-skill",
                tabby_profile_id="",
                har=_har([]),
                click_events=[],
                url_events=[],
                output_dir=str(tmp),
            )
        # The output dir may exist (the compiler mkdir's it) but no generated
        # source files should have been written.
        generated = {p.name for p in tmp.rglob("*") if p.is_file()}
        forbidden = {"SKILL.md", "manifest.json", "API.md"}
        assert not (generated & forbidden), (
            f"Skill compiler wrote artifacts on validation failure: {generated & forbidden}"
        )
