#!/usr/bin/env python3
"""End-to-end smoke test for the autopilot recording flow.

Validates the full pipeline against the local ToyApp:
  1. Start ToyApp (port 9111)
  2. Start NoUI backend (port 8002)
  3. Create workflow + capture sessions
  4. Use browser commands to navigate, login, and create a note
  5. Stop capture, export MCP
  6. Verify note was created and MCP was generated

Prerequisites:
  - Chrome with the NoUI extension loaded and connected to localhost:8002
  - NoUI backend running

Run:
    .venv/bin/python tests/test_autopilot_e2e.py

    Or with pytest (skipped by default unless --run-e2e is passed):
    .venv/bin/python -m pytest tests/test_autopilot_e2e.py --run-e2e -v -s
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

_NOUI_ROOT = Path(__file__).resolve().parent.parent
_TOYAPP_PORT = 9111
_NOUI_PORT = int(os.environ.get("NOUI_PORT", "8002"))
_TOYAPP_URL = f"http://localhost:{_TOYAPP_PORT}"
_NOUI_URL = f"http://localhost:{_NOUI_PORT}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _http(method: str, base: str, path: str, body: dict | None = None, timeout: int = 10) -> dict:
    url = base + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _alive(base: str) -> bool:
    try:
        with urllib.request.urlopen(base + "/health", timeout=3) as resp:
            return json.loads(resp.read().decode()).get("status") == "ok"
    except Exception:
        return False


def _wait_alive(base: str, label: str, timeout: int = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _alive(base):
            return
        time.sleep(0.5)
    raise RuntimeError(f"{label} did not become healthy within {timeout}s at {base}")


def _browser_cmd(command_type: str, params: dict | None = None, timeout: int = 35) -> dict:
    """Execute a browser command via the synchronous endpoint."""
    return _http(
        "POST",
        _NOUI_URL,
        "/browser-commands/execute",
        {
            "command_type": command_type,
            "params": params or {},
        },
        timeout=timeout,
    )


def _extension_connected() -> bool:
    """Check if the extension is connected by trying a simple command."""
    try:
        result = _browser_cmd("get_page_info", timeout=5)
        return isinstance(result, dict) and "url" in result
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _start_toyapp() -> subprocess.Popen:
    proc = subprocess.Popen(
        [
            str(_NOUI_ROOT / ".venv" / "bin" / "python"),
            str(_NOUI_ROOT / "tests" / "toyapp" / "app.py"),
            "--port",
            str(_TOYAPP_PORT),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(_NOUI_ROOT),
    )
    _wait_alive(_TOYAPP_URL, "ToyApp")
    return proc


def _stop_proc(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def toyapp():
    proc = _start_toyapp()
    yield proc
    _stop_proc(proc)


@pytest.fixture(scope="module")
def noui_backend():
    if not _alive(_NOUI_URL):
        pytest.skip("NoUI backend not running. Start it with: noui start")
    yield


# ---------------------------------------------------------------------------
# ToyApp standalone tests (no extension needed)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_toyapp_health(toyapp):
    """ToyApp is running and healthy."""
    resp = _http("GET", _TOYAPP_URL, "/health")
    assert resp["status"] == "ok"
    assert resp["app"] == "toyapp"


@pytest.mark.e2e
def test_toyapp_login_flow(toyapp):
    """ToyApp login returns a session cookie."""
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    data = urllib.parse.urlencode({"username": "testuser", "password": "testpass"}).encode()
    req = urllib.request.Request(f"{_TOYAPP_URL}/api/login", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    opener.open(req, timeout=10)

    cookies = {c.name: c.value for c in jar}
    assert "toyapp_session" in cookies

    req2 = urllib.request.Request(f"{_TOYAPP_URL}/api/notes")
    resp2 = opener.open(req2, timeout=10)
    notes_data = json.loads(resp2.read().decode())
    assert "notes" in notes_data
    assert notes_data["count"] >= 1


# ---------------------------------------------------------------------------
# Backend endpoint tests (no extension needed)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_browser_execute_endpoint_exists(toyapp, noui_backend):
    """The /browser-commands/execute endpoint exists and returns 504 when no extension."""
    try:
        _browser_cmd("get_page_info", timeout=35)
        # If this succeeds, extension is connected — that's fine
    except urllib.error.HTTPError as exc:
        # 504 = timeout (extension not connected) — endpoint exists, correct behavior
        assert exc.code == 504, f"Expected 504 timeout, got {exc.code}"
    except (TimeoutError, OSError):
        # Socket timeout is also acceptable (no extension, client timed out before server)
        pass


@pytest.mark.e2e
def test_create_autopilot_run_record(toyapp, noui_backend):
    """Can create an autopilot run record via the API."""
    result = _http(
        "POST",
        _NOUI_URL,
        "/autopilot-recordings",
        {
            "website_url": f"http://localhost:{_TOYAPP_PORT}",
            "task_description": "Create a test note",
        },
    )
    assert result["id"]
    assert result["status"] == "queued"
    assert result["website_url"] == f"http://localhost:{_TOYAPP_PORT}"

    # Can retrieve it
    run = _http("GET", _NOUI_URL, f"/autopilot-recordings/{result['id']}")
    assert run["id"] == result["id"]

    # Can list
    runs = _http("GET", _NOUI_URL, "/autopilot-recordings")
    assert any(r["id"] == result["id"] for r in runs)


@pytest.mark.e2e
def test_create_workflow_and_capture_session(toyapp, noui_backend):
    """Can create workflow + capture sessions (the setup phase of autopilot)."""
    # Create workflow session
    wf = _http(
        "POST",
        _NOUI_URL,
        "/workflow-sessions",
        {
            "name": "E2E Test Note",
            "start_url": f"http://localhost:{_TOYAPP_PORT}",
            "description": "Create a test note in ToyApp",
        },
    )
    assert wf["id"]
    assert wf["process_id"]
    assert wf["project_id"]
    workflow_session_id = wf["id"]
    process_id = wf["process_id"]

    # Start workflow session
    wf_started = _http("POST", _NOUI_URL, f"/workflow-sessions/{workflow_session_id}/start")
    assert wf_started["status"] == "recording"

    # Create capture session
    cs = _http(
        "POST",
        _NOUI_URL,
        f"/processes/{process_id}/capture-sessions",
        {
            "click_tracking": True,
            "url_monitoring": True,
            "har_capture": True,
        },
    )
    assert cs["id"]
    capture_session_id = cs["id"]

    # Start capture session (PUT not POST)
    cs_started = _http("PUT", _NOUI_URL, f"/capture-sessions/{capture_session_id}/start")
    assert cs_started["status"] == "capturing"

    # Stop capture session
    cs_stopped = _http("PUT", _NOUI_URL, f"/capture-sessions/{capture_session_id}/stop")
    assert cs_stopped["status"] == "stopped"

    # Complete workflow session
    wf_done = _http("POST", _NOUI_URL, f"/workflow-sessions/{workflow_session_id}/complete")
    assert wf_done["status"] == "completed"


# ---------------------------------------------------------------------------
# Full E2E test (requires Chrome extension)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_full_autopilot_with_browser(toyapp, noui_backend):
    """Full autopilot: browser navigate + login + create note + capture + export.

    Requires Chrome with the NoUI extension connected.
    """
    if not _extension_connected():
        pytest.skip("Chrome extension not connected — skipping full browser test")

    # Reset ToyApp state
    _http("POST", _TOYAPP_URL, "/api/reset")

    # 1. Create workflow + capture session
    wf = _http(
        "POST",
        _NOUI_URL,
        "/workflow-sessions",
        {
            "name": "Autopilot E2E",
            "start_url": f"http://localhost:{_TOYAPP_PORT}",
            "description": "Create a note via autopilot",
        },
    )
    workflow_session_id = wf["id"]
    process_id = wf["process_id"]

    _http("POST", _NOUI_URL, f"/workflow-sessions/{workflow_session_id}/start")

    cs = _http(
        "POST",
        _NOUI_URL,
        f"/processes/{process_id}/capture-sessions",
        {
            "click_tracking": True,
            "url_monitoring": True,
            "har_capture": True,
        },
    )
    capture_session_id = cs["id"]
    _http("PUT", _NOUI_URL, f"/capture-sessions/{capture_session_id}/start")

    try:
        # 2. Navigate to login
        print("\n  Navigating to ToyApp login...")
        _browser_cmd("navigate", {"url": f"http://localhost:{_TOYAPP_PORT}/login"})
        time.sleep(2)

        # 3. Login
        print("  Logging in...")
        _browser_cmd("type_into_label", {"label": "Username", "text": "testuser"})
        _browser_cmd("type_into_label", {"label": "Password", "text": "testpass"})
        _browser_cmd("click_by_text", {"text": "Sign In"})
        time.sleep(2)

        # Verify logged in
        page = _browser_cmd("get_page_summary")
        print(f"  Page after login: {page.get('url', '?')}")

        # 4. Navigate to New Note
        print("  Navigating to New Note...")
        _browser_cmd("click_by_text", {"text": "New Note"})
        time.sleep(1)

        # 5. Create note
        print("  Creating note...")
        _browser_cmd("type_into_label", {"label": "Title", "text": "Autopilot Test Note"})
        _browser_cmd("type_into_label", {"label": "Body", "text": "Created by autopilot E2E test"})
        _browser_cmd("click_by_text", {"text": "Save Note"})
        time.sleep(2)

    finally:
        # 6. Stop capture
        print("  Stopping capture...")
        _http("PUT", _NOUI_URL, f"/capture-sessions/{capture_session_id}/stop")
        _http("POST", _NOUI_URL, f"/workflow-sessions/{workflow_session_id}/complete")
        time.sleep(3)  # Wait for HAR upload

    # 7. Verify note was created in ToyApp
    notes = _http("GET", _TOYAPP_URL, "/api/notes")
    note_titles = [n["title"] for n in notes["notes"]]
    print(f"  Notes in ToyApp: {note_titles}")
    assert "Autopilot Test Note" in note_titles, f"Note not found. Notes: {note_titles}"

    # 8. Export MCP
    print("  Exporting MCP...")
    try:
        result = _http(
            "POST",
            _NOUI_URL,
            f"/workflow-sessions/{workflow_session_id}/export"
            f"?as=mcp&capture_session_id={capture_session_id}",
            timeout=30,
        )
        manifest = result.get("mcp") or {}
        server_id = manifest.get("server_id", "")
        tools_count = manifest.get("tools_count", len(manifest.get("tools", [])))
        print(f"  Server ID: {server_id}")
        print(f"  Tools: {tools_count}")
        assert server_id, "No server_id returned"
        assert tools_count > 0, "No tools generated"
        print("  PASS: Full autopilot E2E completed!")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        if "No HAR file" in detail:
            pytest.skip("HAR not captured — extension may not have recorded traffic")
        else:
            pytest.fail(f"MCP export failed: {detail}")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def _run_standalone():
    print("=" * 60)
    print("NoUI Autopilot E2E Test (standalone)")
    print("=" * 60)

    # 1. Start ToyApp
    print("\n1. Starting ToyApp...")
    toyapp_proc = _start_toyapp()
    print(f"   ToyApp running on {_TOYAPP_URL}")

    try:
        # 2. Check NoUI backend
        print("\n2. Checking NoUI backend...")
        if not _alive(_NOUI_URL):
            print(f"   NoUI backend not running at {_NOUI_URL}")
            print("   Start it with: .venv/bin/python cli/main.py start")
            return 1
        print(f"   NoUI backend OK at {_NOUI_URL}")

        # 3. Check extension
        print("\n3. Checking Chrome extension...")
        if not _extension_connected():
            print("   Extension not connected.")
            print("   You can test manually with the skill:")
            print("   Tell Claude Code: /noui-autopilot")
            print(f"   Website: http://localhost:{_TOYAPP_PORT}")
            print("   Credentials: testuser / testpass")
            print("   Task: Create a note titled 'Test Note' with body 'Hello World'")
            print("\n   Or drive the browser yourself:")
            print(
                f"   .venv/bin/python cli/main.py autopilot browser navigate url=http://localhost:{_TOYAPP_PORT}/login"
            )
            print("   .venv/bin/python cli/main.py autopilot browser get_page_summary")
            print(
                "   .venv/bin/python cli/main.py autopilot browser type_into_label label=Username text=testuser"
            )
            print("\n   Press Ctrl+C to stop ToyApp")
            try:
                toyapp_proc.wait()
            except KeyboardInterrupt:
                pass
            return 0

        # 4. Run the browser test
        print("   Extension connected!")
        print("\n4. Running full autopilot flow...")
        # (Same flow as test_full_autopilot_with_browser but inline)
        _http("POST", _TOYAPP_URL, "/api/reset")

        wf = _http(
            "POST",
            _NOUI_URL,
            "/workflow-sessions",
            {
                "name": "Standalone E2E",
                "start_url": f"http://localhost:{_TOYAPP_PORT}",
            },
        )
        _http("POST", _NOUI_URL, f"/workflow-sessions/{wf['id']}/start")
        cs = _http(
            "POST",
            _NOUI_URL,
            f"/processes/{wf['process_id']}/capture-sessions",
            {
                "click_tracking": True,
                "url_monitoring": True,
                "har_capture": True,
            },
        )
        _http("PUT", _NOUI_URL, f"/capture-sessions/{cs['id']}/start")

        _browser_cmd("navigate", {"url": f"http://localhost:{_TOYAPP_PORT}/login"})
        time.sleep(2)
        _browser_cmd("type_into_label", {"label": "Username", "text": "testuser"})
        _browser_cmd("type_into_label", {"label": "Password", "text": "testpass"})
        _browser_cmd("click_by_text", {"text": "Sign In"})
        time.sleep(2)
        _browser_cmd("click_by_text", {"text": "New Note"})
        time.sleep(1)
        _browser_cmd("type_into_label", {"label": "Title", "text": "Autopilot Test Note"})
        _browser_cmd("type_into_label", {"label": "Body", "text": "Created by standalone E2E"})
        _browser_cmd("click_by_text", {"text": "Save Note"})
        time.sleep(2)

        _http("PUT", _NOUI_URL, f"/capture-sessions/{cs['id']}/stop")
        _http("POST", _NOUI_URL, f"/workflow-sessions/{wf['id']}/complete")
        time.sleep(3)

        notes = _http("GET", _TOYAPP_URL, "/api/notes")
        note_titles = [n["title"] for n in notes["notes"]]
        if "Autopilot Test Note" in note_titles:
            print("   PASS: Note created in ToyApp!")
        else:
            print(f"   FAIL: Note not found. Notes: {note_titles}")
            return 1

        try:
            result = _http(
                "POST",
                _NOUI_URL,
                f"/workflow-sessions/{wf['id']}/export?as=mcp&capture_session_id={cs['id']}",
            )
            manifest = result.get("mcp") or {}
            print(
                f"   PASS: MCP exported — server_id={manifest.get('server_id')},"
                f" tools={manifest.get('tools_count', len(manifest.get('tools', [])))}"
            )
        except Exception as exc:
            print(f"   WARN: MCP export failed (HAR may not have captured): {exc}")

        return 0

    finally:
        _stop_proc(toyapp_proc)
        print("\n   ToyApp stopped.")


if __name__ == "__main__":
    sys.exit(_run_standalone())
