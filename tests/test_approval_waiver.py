"""A waiver has to name what it waives, and name all of it.

Run 050970d3: the replay reached 3 of 7 goals. The failures were the statement
download — the entire thing the member had asked for. `--force` was one flag
that covered all of it, so the build reached for it, drafted the member's
approval sentence, and asked them to paste it back.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))
sys.path.insert(0, str(_ROOT / "skills" / "noui" / "scripts"))  # _bootstrap

_spec = importlib.util.spec_from_file_location(
    "_va", _ROOT / "skills" / "noui" / "scripts" / "verify_approve.py"
)
va = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(va)

REPORT = {
    "fingerprint": "abc123",
    "all_goals_reached": False,
    "operations": [
        {"name": "read_overview", "goal_reached": True, "steps": [{"status": "ok"}]},
        {
            "name": "download_monthly_statement",
            "goal_reached": False,
            "steps": [
                {"command": "click_by_text", "status": "ok"},
                {"command": "click_by_text", "status": "blocked", "error": "nothing matches"},
            ],
        },
        {
            "name": "download_annual_statement",
            "goal_reached": False,
            "steps": [{"command": "click_element", "status": "blocked", "error": "no match"}],
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


def test_force_is_gone_and_says_what_replaced_it(tmp_path, monkeypatch, capsys):
    d = _skill(tmp_path)
    assert _run(monkeypatch, d, "--force") == 1
    err = capsys.readouterr().err
    assert "--accept-failing" in err
    assert not (d / "replay_approval.json").exists()


def test_an_unnamed_failure_refuses_and_lists_the_flags(tmp_path, monkeypatch, capsys):
    d = _skill(tmp_path)
    assert _run(monkeypatch, d) == 1
    err = capsys.readouterr().err
    # The message must be usable as-is: the build should not have to work out
    # the operation names itself, that is how the convenient ones get named and
    # the rest get quietly dropped.
    assert "--accept-failing download_monthly_statement" in err
    assert "--accept-failing download_annual_statement" in err


def test_naming_only_some_failures_is_refused(tmp_path, monkeypatch, capsys):
    d = _skill(tmp_path)
    code = _run(monkeypatch, d, "--accept-failing", "download_monthly_statement")
    assert code == 1
    assert "download_annual_statement" in capsys.readouterr().err
    assert not (d / "replay_approval.json").exists()


def test_naming_an_operation_that_passed_is_refused(tmp_path, monkeypatch, capsys):
    # A list carried over from an earlier replay: it names something that is now
    # green, which means it was not written about THIS run.
    d = _skill(tmp_path)
    code = _run(
        monkeypatch,
        d,
        "--accept-failing",
        "read_overview",
        "--accept-failing",
        "download_monthly_statement",
        "--accept-failing",
        "download_annual_statement",
    )
    assert code == 1
    assert "read_overview" in capsys.readouterr().err


def test_naming_an_operation_that_does_not_exist_is_refused(tmp_path, monkeypatch, capsys):
    d = _skill(tmp_path)
    code = _run(monkeypatch, d, "--accept-failing", "download_quarterly")
    assert code == 1
    assert "download_quarterly" in capsys.readouterr().err


def test_naming_every_failure_approves_and_records_why(tmp_path, monkeypatch):
    d = _skill(tmp_path)
    code = _run(
        monkeypatch,
        d,
        "--accept-failing",
        "download_monthly_statement",
        "--accept-failing",
        "download_annual_statement",
    )
    assert code == 0
    rec = _approval(d)
    assert rec["installable"] is True
    waived = {a["operation"]: a for a in rec["accepted_failing"]}
    assert set(waived) == {"download_monthly_statement", "download_annual_statement"}
    # Built from the report, not from what the caller said about it — the member
    # sees the actual error, not a summary written by whoever wanted the waiver.
    assert waived["download_monthly_statement"]["blocked"] == [
        {"command": "click_by_text", "error": "nothing matches"}
    ]


def test_an_all_green_replay_needs_no_waiver(tmp_path, monkeypatch):
    report = {
        "fingerprint": "abc123",
        "all_goals_reached": True,
        "operations": [{"name": "read_overview", "goal_reached": True, "steps": []}],
    }
    d = _skill(tmp_path, report)
    assert _run(monkeypatch, d) == 0
    assert "accepted_failing" not in _approval(d)
