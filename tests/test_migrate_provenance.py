"""Migrating an old browser skill attaches its recording — and only if it fits.

A skill compiled before provenance existed is refused on its next install. It
WAS compiled from a real recording and capture always saved the bundle, so the
way back is to re-attach that recording rather than re-record.

The risk this carries is obvious: a "just stamp it" tool would let anyone bless a
fabricated skill by pointing it at any recording. So migration runs the same
check the installer runs, and these tests pin that it does.
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("skills/noui/scripts/migrate_provenance.py").resolve()

RECORDING = {
    "session_id": "sess-old",
    "schema_version": 5,
    "click_events": [{"seq": 1, "locator": {"value": "#dl", "kind": "css", "is_css": True}}],
    "url_events": [{"seq": 0, "to_url": "https://bank.test/statements"}],
}


def _skill(tmp_path, steps=None, session_id="sess-old"):
    d = tmp_path / "skills" / "bank"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "runtime": {"operation_style": "browser"},
                "workflow": {"workflow_session_id": session_id},
            }
        )
    )
    (d / "operations.json").write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "name": "download",
                        "tool": "call_web_browser",
                        "steps": steps
                        or [{"command": "click_element", "params": {"selector": "#dl"}}],
                    }
                ]
            }
        )
    )
    return d


def _bundles(tmp_path, bundle=RECORDING):
    b = tmp_path / "bundles"
    b.mkdir(parents=True, exist_ok=True)
    (b / "bank-sess-old.json").write_text(json.dumps(bundle))
    return b


def _run(skill_dir, tmp_path, *extra):
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path("skills/noui").resolve()),
        "NOUI_WORKBENCH_DIR": str(tmp_path),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(skill_dir), *extra],
        capture_output=True,
        text=True,
        env=env,
    )


def test_it_finds_the_recording_by_session_id_and_attaches_it(tmp_path):
    d = _skill(tmp_path)
    _bundles(tmp_path)
    r = _run(d, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "session id sess-old matches" in r.stdout

    manifest = json.loads((d / "manifest.json").read_text())
    from noui_core.compile.provenance import bundle_digest

    assert manifest["provenance"]["bundle_sha256"] == bundle_digest(RECORDING)
    assert json.loads((d / "recording_bundle.json").read_text()) == RECORDING


def test_it_refuses_a_recording_that_does_not_contain_the_steps(tmp_path):
    """The check that stops this being a laundering tool.

    A hand-written skill's selectors were never in any bundle, so pointing this
    at a real recording must not make it pass.
    """
    d = _skill(tmp_path, steps=[{"command": "click_element", "params": {"selector": "#invented"}}])
    _bundles(tmp_path)
    r = _run(d, tmp_path)
    assert r.returncode == 1
    assert "do not appear in it" in r.stderr and "#invented" in r.stderr
    assert not (d / "recording_bundle.json").exists()
    assert "provenance" not in json.loads((d / "manifest.json").read_text())


def test_it_refuses_when_no_saved_bundle_matches(tmp_path):
    d = _skill(tmp_path, session_id="sess-gone")
    _bundles(tmp_path)
    r = _run(d, tmp_path)
    assert r.returncode == 1
    assert "no saved bundle has session id sess-gone" in r.stderr


def test_it_leaves_non_browser_skills_alone(tmp_path):
    d = _skill(tmp_path)
    (d / "manifest.json").write_text(json.dumps({"runtime": {"operation_style": "api"}}))
    r = _run(d, tmp_path)
    assert r.returncode == 1
    assert "not a browser skill" in r.stderr


def test_dry_run_writes_nothing(tmp_path):
    d = _skill(tmp_path)
    _bundles(tmp_path)
    r = _run(d, tmp_path, "--dry-run")
    assert r.returncode == 0
    assert "Would attach" in r.stdout
    assert not (d / "recording_bundle.json").exists()


def test_it_is_idempotent(tmp_path):
    d = _skill(tmp_path)
    _bundles(tmp_path)
    assert _run(d, tmp_path).returncode == 0
    again = _run(d, tmp_path)
    assert again.returncode == 0 and "nothing to do" in again.stdout
