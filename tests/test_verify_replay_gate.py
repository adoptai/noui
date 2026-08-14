"""verify_replay's provenance gate tells a build WHICH kind of problem it hit.

The failure that motivated this: a re-import left a recording_bundle.json in the
skill dir that was not the one operations.json was sealed from. unobserved_locators
then read the wrong bundle and reported real locators as "controls the recording
never saw" -- so the build concluded the RECORDING was bad and re-recorded, twice,
burning ~50 tool calls. The bundle-consistency check catches the mismatch first and
names it a compile/import problem, not a recording one.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))
sys.path.insert(0, str(_ROOT / "skills" / "noui" / "scripts"))

from noui_core.compile import provenance  # noqa: E402

_vr = importlib.import_module("verify_replay")

_OPS = {
    "operations": [
        {
            "name": "read_x",
            "tool": "call_web_browser",
            "steps": [{"command": "click_element", "params": {"selector": "#dl"}}],
        }
    ]
}
_SEALED = {"click_events": [{"candidates": [{"kind": "id", "value": "#dl"}]}]}
_SWAPPED = {"click_events": [{"candidates": [{"kind": "id", "value": "#other"}]}]}


def _run(skill_dir: Path) -> tuple[int, str]:
    sys.argv = ["verify_replay.py", str(skill_dir), "--profile-slug", "x"]
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            rc = _vr.main()
        except SystemExit as exc:  # argparse/exit paths
            rc = exc.code
    return rc, err.getvalue()


def _write(p: Path, bundle: dict) -> None:
    manifest = {
        "provenance": {
            "steps_sha256": provenance.steps_digest(_OPS["operations"]),
            "bundle_sha256": provenance.bundle_digest(_SEALED),
        }
    }
    (p / "operations.json").write_text(json.dumps(_OPS))
    (p / "manifest.json").write_text(json.dumps(manifest))
    (p / provenance.BUNDLE_FILE).write_text(json.dumps(bundle))


def test_a_swapped_bundle_is_named_a_compile_problem_not_a_recording_one():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        _write(p, _SWAPPED)  # dir bundle != the one operations were sealed from
        rc, msg = _run(p)
    assert rc == 1
    assert "COMPILE/IMPORT problem" in msg
    assert "DO NOT re-record" in msg
    # It stops at the desync gate, not the misleading unobserved-locators verdict
    # (whose message alone carries this line about the bundle checksum matching).
    assert "The bundle checksum already matched" not in msg


def test_the_matching_bundle_passes_the_consistency_gate():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        _write(p, _SEALED)  # the bundle these operations were sealed from
        _, msg = _run(p)
    # Gate not triggered; the run proceeds past it (and later reports no session).
    assert "SKILL DIRECTORY IS INCONSISTENT" not in msg
