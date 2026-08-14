"""A step can say which variant of an operation it belongs to.

Two operations recorded from one page often differ by WHICH STEPS RUN, not by a
value. ICICI's monthly and annual statements share four steps to reach the
download page — hover the nav, Credit Cards, Past, download previous statement —
and then diverge: annual clicks a radio and picks a financial year, monthly does
not. Substitution fills placeholders; it cannot add or remove a step. So the
compiler emitted both as separate operations, each replaying the whole journey
from the landing page, which is what made every operation need the browser
returned to the entry page first.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.verify.replay import step_applies, substitute_parameters  # noqa: E402

# The real divergence, reduced to the steps that differ.
DOWNLOAD_OP = {
    "name": "download_statement",
    "parameters": [{"name": "timeframe", "default": "monthly"}],
    "steps": [
        {"command": "click_by_text", "params": {"text": "download previous statement"}},
        {
            "command": "click_element",
            "params": {"selector": "#HDisplay23"},
            "when": {"timeframe": "monthly"},
        },
        {"command": "click_by_text", "params": {"text": "Annual"}, "when": {"timeframe": "annual"}},
        {
            "command": "click_by_text",
            "params": {"text": "{{financial_year}}"},
            "when": {"timeframe": "annual"},
        },
        {"command": "click_element", "params": {"selector": "#DOWNLOAD_ESTATEMENT_PDF"}},
    ],
}


def _commands(op, values=None):
    return [s["command"] for s in substitute_parameters(op, values)]


def test_a_step_with_no_condition_always_runs():
    # Everything compiled before `when` existed must behave exactly as it did.
    assert step_applies({"command": "click_element"}, {}) is True
    assert step_applies({"command": "click_element", "when": {}}, {}) is True
    assert step_applies({"command": "click_element", "when": None}, {}) is True


def test_the_default_variant_runs_its_own_steps_only():
    steps = substitute_parameters(DOWNLOAD_OP)
    targets = [
        (s.get("params") or {}).get("selector") or (s.get("params") or {}).get("text")
        for s in steps
    ]
    assert targets == ["download previous statement", "#HDisplay23", "#DOWNLOAD_ESTATEMENT_PDF"]


def test_choosing_the_other_variant_swaps_the_divergent_steps():
    steps = substitute_parameters(
        DOWNLOAD_OP, {"timeframe": "annual", "financial_year": "FY2025-26"}
    )
    targets = [
        (s.get("params") or {}).get("selector") or (s.get("params") or {}).get("text")
        for s in steps
    ]
    assert targets == [
        "download previous statement",
        "Annual",
        "FY2025-26",  # substitution still applies to the steps that survive
        "#DOWNLOAD_ESTATEMENT_PDF",
    ]
    assert "#HDisplay23" not in targets


def test_the_shared_prefix_and_suffix_run_for_every_variant():
    for tf in ("monthly", "annual"):
        cmds = _commands(DOWNLOAD_OP, {"timeframe": tf})
        assert cmds[0] == "click_by_text"  # reaching the page
        assert cmds[-1] == "click_element"  # the download itself


def test_every_named_key_has_to_match():
    # A step can belong to a COMBINATION of choices, not just one.
    step = {"command": "x", "when": {"timeframe": "annual", "format": "pdf"}}
    assert step_applies(step, {"timeframe": "annual", "format": "pdf"}) is True
    assert step_applies(step, {"timeframe": "annual", "format": "csv"}) is False


def test_an_unknown_parameter_never_matches_by_accident():
    # A `when` naming something the operation does not declare is a compiler bug
    # or a hand edit. Skipping is the safe reading: running a step whose
    # condition nobody can evaluate is how a download fires on the wrong page.
    step = {"command": "x", "when": {"nonexistent": "annual"}}
    assert step_applies(step, {"timeframe": "annual"}) is False


def test_values_are_compared_as_text():
    # Parameters arrive from a CLI and from JSON, so 2 and "2" are the same
    # choice; a mismatch here would drop a step for a reason nobody could see.
    assert step_applies({"when": {"page": 2}}, {"page": "2"}) is True
    assert step_applies({"when": {"page": "2"}}, {"page": 2}) is True
