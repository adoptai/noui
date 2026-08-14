"""An amendment records what the replay proved about it, not what was hoped.

Run 050970d3: the build confirmed `click_by_text "Credit Card"` worked once on
/overview, then amended step 0 of SIX operations. Two of them ran. The other four
were blocked long before the amended step could execute — and all six went into
amendments.json carrying "confirmed working in live session", ready for the
member to approve in one go.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))
sys.path.insert(0, str(_ROOT / "skills" / "noui" / "scripts"))  # _bootstrap

_spec = importlib.util.spec_from_file_location(
    "_vr", _ROOT / "skills" / "noui" / "scripts" / "verify_replay.py"
)
vr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vr)

# The shape verify_replay's own report has: one entry per operation, one per step.
REPORT = {
    "operations": [
        {"name": "read_credit_card", "steps": [{"status": "ok"}, {"status": "ok"}]},
        {
            "name": "read_corp_authenticationcontroller",
            "steps": [{"status": "ok"}, {"status": "blocked"}, {"status": "blocked"}],
        },
        # Blocked at step 0 — the amended step is index 0, and it did not pass.
        {"name": "download_corp_finacle", "steps": [{"status": "blocked"}]},
    ]
}


def test_a_step_that_ran_cleanly_is_verified():
    assert vr._step_ran_ok(REPORT, "read_credit_card", 0) is True


def test_a_blocked_step_is_not_verified():
    assert vr._step_ran_ok(REPORT, "download_corp_finacle", 0) is False
    assert vr._step_ran_ok(REPORT, "read_corp_authenticationcontroller", 1) is False


def test_a_step_the_replay_never_reached_is_not_verified():
    # The operation died at step 1, so there is no result for step 2 onward.
    # Missing must never read as passing.
    assert vr._step_ran_ok(REPORT, "read_credit_card", 5) is False


def test_an_operation_the_replay_never_ran_is_not_verified():
    assert vr._step_ran_ok(REPORT, "submit_corp_authenticationcontroller", 0) is False


def test_an_empty_report_verifies_nothing():
    # login_required: the session died, so nothing was proved about anything.
    assert vr._step_ran_ok({}, "read_credit_card", 0) is False
    assert vr._step_ran_ok({"operations": []}, "read_credit_card", 0) is False
