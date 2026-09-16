"""A step substituted at replay is reported as NOT recorded.

The card marks such a step "improvised", because it is the one nobody has ever
been watched performing -- that is where a reviewer's attention belongs. But
nothing ever wrote the key: `_replay_step_inner` built its result from
command/params/expect/locator/status/error/detail and never carried `recorded`,
so every step arrived at the card claiming the recording backed it, an amended
one included.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.verify.replay import _replay_step_inner  # noqa: E402


def _execute(command, params):
    return {"success": True}


def test_a_plan_step_says_nothing_and_reads_as_recorded():
    out = _replay_step_inner(
        _execute,
        {"command": "click_element", "params": {"selector": "#dl"}},
        recorded=set(),
    )
    # Absent, not True: the reader's default is that a step in the plan came from
    # the recording, and a key on every step would be noise.
    assert "recorded" not in out


def test_an_amended_step_is_carried_through_as_not_recorded():
    out = _replay_step_inner(
        _execute,
        {"command": "click_element", "params": {"selector": "#new"}, "recorded": False},
        recorded=set(),
    )
    assert out["recorded"] is False


def test_a_locator_less_step_from_the_plan_is_not_called_improvised():
    """The false positive that motivated the harness-side change.

    Only clicks carry a pre-resolved locator; the compiler attaches none to a
    hover or to a terminal like list_downloads. Deriving "recorded" from the
    locator told the member the agent had made up steps that came straight from
    their own recording.
    """
    out = _replay_step_inner(_execute, {"command": "list_downloads", "params": {}}, recorded=set())
    assert out["locator"] is None
    assert "recorded" not in out
