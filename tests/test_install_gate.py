"""Tests for the gate that stops an unverified browser skill being installed.

Without a gate, replay is a report — produced, glanced at, and skipped whenever
it is inconvenient. These pin that the installer actually refuses, and that an
approval cannot be stretched to cover a plan it was not given for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.verify.gate import (  # noqa: E402
    NotApprovedError,
    check_installable,
    is_browser_skill,
    read_approval,
    write_approval,
)
from noui_core.verify.replay import approve, build_report  # noqa: E402

BROWSER_OP = {
    "name": "download_statement",
    "kind": "download",
    "tool": "call_web_browser",
    "description": "Download the statement",
    "steps": [{"command": "click_element", "params": {"selector": "#dl"}}],
}


def make_skill(tmp_path: Path, ops=None, style="browser") -> Path:
    d = tmp_path / "skill"
    d.mkdir()
    (d / "operations.json").write_text(
        json.dumps({"schema_version": "1", "operations": ops or [BROWSER_OP]}), encoding="utf-8"
    )
    (d / "manifest.json").write_text(
        json.dumps({"runtime": {"operation_style": style}}), encoding="utf-8"
    )
    return d


def approved_report(ops) -> dict:
    return approve(build_report(ops, [[{"status": "ok", "command": "click_element"}]]))


def test_a_browser_skill_with_no_approval_is_refused(tmp_path):
    # The whole point: never run against the live app, never installed.
    with pytest.raises(NotApprovedError, match="never been run"):
        check_installable(make_skill(tmp_path))


def test_a_browser_skill_with_an_approval_installs(tmp_path):
    d = make_skill(tmp_path)
    write_approval(d, approved_report([BROWSER_OP]))
    check_installable(d)  # does not raise


def test_an_approval_does_not_survive_a_change_to_the_steps(tmp_path):
    # "The human's confirmation takes precedence" only means something if
    # amending the plan invalidates the confirmation.
    d = make_skill(tmp_path)
    write_approval(d, approved_report([BROWSER_OP]))

    changed = {
        **BROWSER_OP,
        "steps": [{"command": "click_element", "params": {"selector": "#other"}}],
    }
    (d / "operations.json").write_text(json.dumps({"operations": [changed]}), encoding="utf-8")

    with pytest.raises(NotApprovedError, match="changed since it was approved"):
        check_installable(d)


def test_renaming_or_rewording_does_not_cost_a_fresh_replay(tmp_path):
    # A gate that fires on a typo fix is a gate people learn to bypass.
    d = make_skill(tmp_path)
    write_approval(d, approved_report([BROWSER_OP]))

    reworded = {**BROWSER_OP, "description": "Fetch the annual statement PDF"}
    (d / "operations.json").write_text(json.dumps({"operations": [reworded]}), encoding="utf-8")

    check_installable(d)  # behaviour is unchanged, so the approval still holds


def test_an_unapproved_report_cannot_be_recorded_as_an_approval(tmp_path):
    # Otherwise the file becomes a rubber stamp written by the code that made the
    # report, and the human is out of the loop entirely.
    d = make_skill(tmp_path)
    with pytest.raises(ValueError, match="never approved"):
        write_approval(d, build_report([BROWSER_OP], [[{"status": "ok"}]]))


def test_a_har_replay_skill_is_not_gated(tmp_path):
    # Those are verified by their existing test loop; replaying one means firing
    # recorded requests, a different risk profile.
    api_op = {"name": "list_txns", "tool": "call_web_api", "steps": []}
    check_installable(make_skill(tmp_path, ops=[api_op], style="api"))


def test_operations_calling_the_browser_are_gated_whatever_the_manifest_says(tmp_path):
    # A manifest can be wrong or hand-edited; what the operations DO cannot.
    with pytest.raises(NotApprovedError):
        check_installable(make_skill(tmp_path, style="api"))


def test_a_corrupt_approval_reads_as_absent(tmp_path):
    d = make_skill(tmp_path)
    (d / "replay_approval.json").write_text("{not json", encoding="utf-8")

    assert read_approval(d) is None
    with pytest.raises(NotApprovedError):
        check_installable(d)


def test_is_browser_skill_reads_the_manifest_then_the_operations(tmp_path):
    assert is_browser_skill(make_skill(tmp_path)) is True


# --- shared fingerprint vector ------------------------------------------------
#
# The harness recomputes this digest to check an approval covers the plan being
# installed, because noui is installed in the sandbox and not in the worker.
# Two implementations, one pinned vector: if either drifts, its own test fails
# rather than the gate silently accepting an approval for different steps.
#
# The twin lives in adoptai-workflows:
# tests/agent_harness/test_web_browser_dispatch.py

_SHARED_FINGERPRINT_OPS = [
    {
        "name": "download_statement",
        "kind": "download",
        "tool": "call_web_browser",
        "description": "ignored — descriptions are excluded from the digest",
        "steps": [{"command": "click_element", "params": {"selector": "#dl"}}],
        "parameters": [{"name": "from_date", "default": "2026-01-01", "type": "date"}],
    }
]
_SHARED_FINGERPRINT = "69656c931cee13bcfee67e2e6fab4db0"


def test_the_fingerprint_matches_the_harness_implementation():
    from noui_core.verify.replay import operations_fingerprint  # noqa: PLC0415

    assert operations_fingerprint(_SHARED_FINGERPRINT_OPS) == _SHARED_FINGERPRINT


def test_the_digest_ignores_descriptions_but_not_steps():
    # Pins the two properties the twin depends on: renaming is free, changing
    # behaviour is not.
    from noui_core.verify.replay import operations_fingerprint  # noqa: PLC0415

    reworded = [{**_SHARED_FINGERPRINT_OPS[0], "description": "completely different"}]
    assert operations_fingerprint(reworded) == _SHARED_FINGERPRINT

    restepped = [
        {
            **_SHARED_FINGERPRINT_OPS[0],
            "steps": [{"command": "click_element", "params": {"selector": "#other"}}],
        }
    ]
    assert operations_fingerprint(restepped) != _SHARED_FINGERPRINT
