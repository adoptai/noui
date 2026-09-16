"""An operation the replay never reached is not one the member has to waive.

A replay goes one way: when the browser is not where the recording started it
stops and ASKS to be put back, because navigating there would reload the app and
sign the member out. That leaves goal_reached false on an operation in which
nothing ran -- and verify_approve read every falsy goal_reached as a failure.

So a replay that reached every goal the member asked for could only be approved
with `--accept-failing read_credit_card`, recording that they waived a failure
that never happened -- while noui's own message tells the agent the opposite
("must not be accepted as an unverified step"). Not checked is its own outcome.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))
sys.path.insert(0, str(_ROOT / "skills" / "noui" / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "_va_nc", _ROOT / "skills" / "noui" / "scripts" / "verify_approve.py"
)
va = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(va)

_ASK = {
    "command": "await_member_at_start",
    "status": "blocked",
    "error": "the replay starts at /overview, and the browser is on /credit-card",
}

REPORT = {
    "fingerprint": "abc123",
    # False, because one operation was never reached -- the exact report shape
    # the run produced.
    "all_goals_reached": False,
    "goals": [{"name": "download_statement", "kind": "download", "reached": True}],
    "operations": [
        {"name": "read_credit_card", "goal_reached": False, "steps": [_ASK]},
        {
            "name": "download_statement",
            "kind": "download",
            "goal_reached": True,
            "steps": [{"command": "list_downloads", "status": "ok"}],
        },
    ],
}


def _skill(tmp_path, report=None):
    d = tmp_path / "icici"
    d.mkdir()
    (d / "replay_report.json").write_text(json.dumps(report or REPORT), encoding="utf-8")
    return d


def _run(monkeypatch, skill_dir, *flags):
    monkeypatch.setattr(sys, "argv", ["verify_approve.py", str(skill_dir), *flags])
    return va.main()


def _approval(skill_dir):
    return json.loads((skill_dir / "replay_approval.json").read_text(encoding="utf-8"))


def test_a_goal_reached_replay_approves_without_waiving_anything(tmp_path, monkeypatch, capsys):
    """The end-to-end case: the card offers a clean install, so this must honour it."""
    d = _skill(tmp_path)
    assert _run(monkeypatch, d) == 0
    assert "--accept-failing" not in capsys.readouterr().err
    record = _approval(d)
    assert record["installable"] is True
    # Not recorded as a waiver -- nothing failed.
    assert "accepted_failing" not in record


def test_the_approval_still_records_what_was_not_covered(tmp_path, monkeypatch):
    d = _skill(tmp_path)
    _run(monkeypatch, d)
    assert _approval(d)["not_checked"] == ["read_credit_card"]


def test_the_member_is_told_which_operation_was_not_covered(tmp_path, monkeypatch, capsys):
    d = _skill(tmp_path)
    _run(monkeypatch, d)
    err = capsys.readouterr().err
    assert "Not checked this time: read_credit_card" in err
    assert "needs no waiver" in err


def test_waiving_a_not_checked_operation_is_refused(tmp_path, monkeypatch, capsys):
    """An agent reaching for the waiver anyway is told nothing ran to waive."""
    d = _skill(tmp_path)
    assert _run(monkeypatch, d, "--accept-failing", "read_credit_card") == 1
    err = capsys.readouterr().err
    assert "did not fail" in err
    assert "nothing to waive" in err
    assert not (d / "replay_approval.json").exists()


def test_a_real_failure_in_the_same_operation_keeps_it_failing(tmp_path, monkeypatch, capsys):
    """Only the ask is excused, and only when it is the sole thing in the way."""
    report = json.loads(json.dumps(REPORT))
    report["operations"][0]["steps"] = [
        _ASK,
        {"command": "click_element", "status": "blocked", "error": "nothing matches"},
    ]
    d = _skill(tmp_path, report)
    assert _run(monkeypatch, d) == 1
    assert "--accept-failing read_credit_card" in capsys.readouterr().err


def test_a_genuinely_failing_operation_is_unaffected(tmp_path, monkeypatch, capsys):
    report = json.loads(json.dumps(REPORT))
    report["operations"][1]["goal_reached"] = False
    report["operations"][1]["steps"] = [
        {"command": "list_downloads", "status": "blocked", "error": "no file arrived"}
    ]
    d = _skill(tmp_path, report)
    assert _run(monkeypatch, d) == 1
    err = capsys.readouterr().err
    assert "--accept-failing download_statement" in err
    # The not-checked operation is NOT dragged into the waiver list.
    assert "--accept-failing read_credit_card" not in err


def test_waiving_the_real_failure_records_only_that_one(tmp_path, monkeypatch):
    report = json.loads(json.dumps(REPORT))
    report["operations"][1]["goal_reached"] = False
    report["operations"][1]["steps"] = [
        {"command": "list_downloads", "status": "blocked", "error": "no file arrived"}
    ]
    d = _skill(tmp_path, report)
    assert _run(monkeypatch, d, "--accept-failing", "download_statement") == 0
    record = _approval(d)
    assert [w["operation"] for w in record["accepted_failing"]] == ["download_statement"]
    assert record["not_checked"] == ["read_credit_card"]
