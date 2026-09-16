"""A replay that reached its goal is not re-run to "confirm reliability".

An agent finished a clean replay and proposed "repeat the run once more to
confirm reliability before installing". A repeat over unchanged steps is the
same run: it proves nothing the first did not, and it is not free -- it
re-drives the member's live bank or portal, and on an app that expires its
session on reload it costs them another sign-in. The guard refuses exactly that
case and names the two reasons a repeat IS worth running.
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
            "name": "download",
            "tool": "call_web_browser",
            "steps": [{"command": "click_element", "params": {"selector": "#dl"}}],
        }
    ]
}
_BUNDLE = {"click_events": [{"candidates": [{"kind": "id", "value": "#dl"}]}]}


def _skill(p: Path, report: dict | None) -> None:
    (p / "operations.json").write_text(json.dumps(_OPS))
    (p / "manifest.json").write_text(
        json.dumps(
            {
                "provenance": {
                    "steps_sha256": provenance.steps_digest(_OPS["operations"]),
                    "bundle_sha256": provenance.bundle_digest(_BUNDLE),
                }
            }
        )
    )
    (p / provenance.BUNDLE_FILE).write_text(json.dumps(_BUNDLE))
    if report is not None:
        (p / _vr.REPORT_FILE).write_text(json.dumps(report))


def _run(skill_dir: Path, *extra: str) -> tuple[int, str]:
    sys.argv = ["verify_replay.py", str(skill_dir), "--profile-slug", "x", *extra]
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            rc = _vr.main()
        except SystemExit as exc:
            rc = exc.code
    return rc, err.getvalue()


def _clean_report(**over):
    report = {
        "goals": [{"name": "download", "reached": True}],
        "operations": [
            {
                "name": "download",
                "goal_reached": True,
                "steps": [{"command": "click_element", "status": "ok"}],
            }
        ],
        "steps_digest": provenance.steps_digest(_OPS["operations"]),
    }
    report.update(over)
    return report


def test_a_clean_replay_is_not_repeated_to_confirm_it():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        _skill(p, _clean_report())
        rc, msg = _run(p)
    assert rc == 1
    assert "already replayed and it reached every goal" in msg
    assert "confirm reliability" in msg
    assert "--again" in msg


def test_the_member_asking_for_one_is_honoured():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        _skill(p, _clean_report())
        _, msg = _run(p, "--again")
    assert "already replayed and it reached every goal" not in msg


def test_an_amended_plan_replays_without_argument():
    """The digest is what keeps the guard from blocking a real re-run."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        _skill(p, _clean_report(steps_digest="stale-digest-from-the-old-plan"))
        _, msg = _run(p)
    assert "already replayed and it reached every goal" not in msg


def test_a_replay_that_missed_its_goal_is_never_blocked():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        _skill(p, _clean_report(goals=[{"name": "download", "reached": False}]))
        _, msg = _run(p)
    assert "already replayed and it reached every goal" not in msg


def test_a_blocked_step_is_never_blocked_from_replaying():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        report = _clean_report()
        report["operations"][0]["steps"] = [{"command": "click_element", "status": "blocked"}]
        _skill(p, report)
        _, msg = _run(p)
    assert "already replayed and it reached every goal" not in msg


def test_an_operation_the_replay_never_reached_is_named_as_the_one_caveat():
    """The ask to be put back at a start page is not a failure, so it does not
    license a pointless repeat -- but it IS the one thing a repeat could still
    check, so the refusal says which operation and what the member must do."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        report = _clean_report()
        report["operations"].insert(
            0,
            {
                "name": "read_credit_card",
                "goal_reached": False,
                "steps": [{"command": "await_member_at_start", "status": "blocked"}],
            },
        )
        _skill(p, report)
        rc, msg = _run(p)
    assert rc == 1
    assert "read_credit_card was not checked" in msg
    assert "put it back" in msg


def test_a_partial_run_is_not_evidence_and_never_blocks_a_full_one():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        _skill(p, _clean_report(partial=["download"], installable=False))
        _, msg = _run(p)
    assert "already replayed and it reached every goal" not in msg
