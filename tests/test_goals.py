"""A skill's goal is the user's outcome, not one badge per operation.

The muddle this closes: a statement download navigates, selects a period, submits
a GO, then downloads -- three terminals, ONE goal. Asked "what did you build?",
the answer is "download the annual statement", not "3 of 3 operations".
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.compile.goals import (  # noqa: E402
    goal_coverage_warning,
    infer_primary_goal,
    primary_goal_index,
)


def op(name: str, kind: str | None = None) -> dict:
    return {"name": name, "kind": kind, "description": f"{name} desc"}


def test_primary_goal_index_points_at_the_same_op_infer_selects():
    # The index and the descriptor must agree: the report indexes ops_out by this
    # to read reached-status off the right op even when two share a name.
    ops = [op("submit_period", "submit"), op("download_annual", "download")]
    assert primary_goal_index(ops) == 1
    assert infer_primary_goal(ops)["name"] == ops[primary_goal_index(ops)]["name"]
    # Duplicate names -> the LAST terminal's index, not the first match.
    dup = [op("download_x", "download"), op("download_x", "download")]
    assert primary_goal_index(dup) == 1
    assert primary_goal_index([]) is None
    assert primary_goal_index(None) is None


def test_the_goal_helpers_tolerate_non_dict_operations():
    # capture_import can hand these a placeholder/malformed operations list (e.g. a
    # count-only stand-in). They must not AttributeError on a non-dict entry.
    assert primary_goal_index([1, 2]) is None
    assert infer_primary_goal([1, 2]) is None
    assert goal_coverage_warning([1, 2]) is not None  # no dict terminal -> warns
    # A real dict alongside a non-dict still resolves to the dict.
    mixed = [1, op("download_x", "download")]
    assert primary_goal_index(mixed) == 1
    assert infer_primary_goal(mixed)["name"] == "download_x"


def test_the_endpoint_download_is_the_goal_not_the_intermediate_submits():
    # ICICI's shape: select period (submit), GO (submit), then download. The goal
    # is the download, and the submits are the path to it.
    ops = [
        op("read_credit_card"),
        op("submit_period", "submit"),
        op("submit_go", "submit"),
        op("download_annual_statement", "download"),
    ]
    goal = infer_primary_goal(ops)
    assert goal["name"] == "download_annual_statement"
    assert goal["kind"] == "download"


def test_a_download_wins_even_when_a_submit_came_after_it():
    # A completed file is the strongest signal, so it wins over a later submit.
    ops = [op("download_report", "download"), op("submit_feedback", "submit")]
    assert infer_primary_goal(ops)["name"] == "download_report"


def test_the_last_download_wins_when_there_are_several():
    ops = [op("download_jan", "download"), op("download_annual", "download")]
    assert infer_primary_goal(ops)["name"] == "download_annual"


def test_a_submit_is_the_goal_when_nothing_downloaded():
    ops = [op("read_page"), op("submit_search", "submit")]
    assert infer_primary_goal(ops)["name"] == "submit_search"


def test_a_navigation_only_recording_falls_back_to_its_last_page():
    # No artifact produced -- the destination reached is the goal ("open X").
    ops = [op("read_overview"), op("read_dashboard")]
    goal = infer_primary_goal(ops)
    assert goal["name"] == "read_dashboard"
    assert goal["kind"] == "read"


def test_no_operations_infers_no_goal():
    assert infer_primary_goal([]) is None
    assert infer_primary_goal(None) is None


def test_coverage_warns_when_the_recording_produced_no_result():
    # Read-only: the "meant to fetch a file but captured nothing" trap.
    warn = goal_coverage_warning([op("read_overview"), op("read_dashboard")])
    assert warn and "demonstrated no goal" in warn


def test_coverage_is_silent_when_a_terminal_was_captured():
    assert goal_coverage_warning([op("read_x"), op("download_y", "download")]) is None
    assert goal_coverage_warning([op("submit_z", "submit")]) is None
