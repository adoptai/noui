"""Tests for turning recorded input values into operation parameters.

A recording captures ONE run — "1 Jan – 31 Jan", "account ...4471". Compiled
literally the skill can only fetch that, which is why the ICICI agent, asked for
"last year", found nothing in the operation to vary and spent twenty steps
hunting the page for a period selector.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.compile.parameters import derive_parameters, fill_steps  # noqa: E402


def ev(**over) -> dict:
    base = {
        "event_type": "input",
        "tag_name": "INPUT",
        "input_type": "text",
        "field_name": "fromDate",
        "value": "2026-01-01",
        "url": "https://bank.test/statements",
        "candidates": [{"kind": "id", "value": "#from-date", "match_count": 1}],
    }
    base.update(over)
    return base


def test_a_select_on_its_placeholder_option_is_not_a_parameter():
    # ICICI's #FieldDropdown decoy records value "Value" -- the inert first
    # option. It must not become a parameter + select_option that drives a
    # meaningless control and asks the caller for a value meaning "nothing chosen".
    for placeholder in ("Value", "-- Select --", "Choose", "Please select", "None"):
        assert (
            derive_parameters(
                [
                    ev(
                        tag_name="SELECT",
                        event_type="change",
                        field_name="FieldDropdown",
                        value=placeholder,
                    )
                ]
            )
            == []
        ), placeholder


def test_a_select_with_a_real_choice_is_still_a_parameter():
    # A real selection (a period, an account) carries a real value and is kept.
    for real in ("FY2025-26", "All accounts", "Savings ...4471"):
        params = derive_parameters(
            [ev(tag_name="SELECT", event_type="change", field_name="period", value=real)]
        )
        assert len(params) == 1 and params[0]["default"] == real, real
        assert params[0]["control"] == "select"


def test_a_recorded_value_becomes_a_parameter_with_that_value_as_default():
    # Default = recorded, so the operation still does exactly what was recorded
    # when nothing is passed.
    (p,) = derive_parameters([ev()])
    assert p["name"] == "from_date"
    assert p["type"] == "date"
    assert p["default"] == "2026-01-01"
    assert p["selector"] == "#from-date"


def test_a_password_is_never_a_parameter():
    # Tabby exists so callers do not hold credentials; a skill that accepted one
    # as an argument would hand that problem straight back.
    assert (
        derive_parameters([ev(field_role="password", value="[REDACTED]", is_redacted=True)]) == []
    )
    assert derive_parameters([ev(field_role="password", value="hunter2")]) == []


def test_an_otp_is_never_a_parameter():
    assert derive_parameters([ev(field_role="otp", value="482913")]) == []


def test_the_last_value_for_a_field_wins():
    # A human who types, corrects and retypes leaves several events for one
    # field; what matters is what it held when they acted.
    params = derive_parameters([ev(value="2026-01-01"), ev(value="2025-04-01")])
    assert len(params) == 1
    assert params[0]["default"] == "2025-04-01"


def test_recognises_a_date_even_when_the_input_is_a_text_field():
    # Bank date fields are routinely type="text" with a picker bolted on.
    (p,) = derive_parameters([ev(input_type="text", value="31/03/2026", field_name="toDate")])
    assert p["type"] == "date"


def test_recognises_a_year_and_an_amount_from_the_field_name():
    (y,) = derive_parameters([ev(field_name="statementYear", value="2025")])
    assert y["type"] == "year"
    (a,) = derive_parameters([ev(field_name="txnAmount", value="1500.50")])
    assert a["type"] == "amount"


def test_checkbox_state_is_not_a_parameter():
    # Part of the recorded path, not a value a caller supplies.
    assert (
        derive_parameters([ev(event_type="change", input_type="checkbox", value="checked")]) == []
    )


def test_a_dropdown_is_chosen_not_typed_into():
    """A native <select> IS settable -- the runtime has always had select_option.

    Marking it read-only meant every dropdown a skill needed was surfaced as
    something to report rather than something to drive, leaving clicks through
    whatever widget the page draws over it as the only way to change one. On
    ICICI that is three brittle steps per dropdown and a replay that stalls
    mid-panel.

    It must not compile to type_text either: you choose from a select, you do
    not type into one.
    """
    (p,) = derive_parameters(
        [ev(event_type="change", tag_name="SELECT", field_name="period", value="Last 6 months")]
    )
    assert p["control"] == "select"
    assert p["settable"] is True
    (step,) = fill_steps([{**p, "selector": "#period"}])
    assert step["command"] == "select_option"
    assert step["params"] == {"selector": "#period", "value": "{{period}}"}


def test_falls_back_to_the_label_when_a_field_has_no_name():
    (p,) = derive_parameters(
        [
            ev(
                field_name="",
                candidates=[{"kind": "label", "value": "Statement period", "match_count": 1}],
                element={"accessible_name": "Statement period"},
            )
        ]
    )
    assert p["name"] == "statement_period"
    assert p["by_label"] == "Statement period"


def test_fill_steps_template_on_the_parameter_name():
    # The placeholder is what lets a caller override; without it the step would
    # hard-code the recorded run.
    steps = fill_steps(derive_parameters([ev()]))
    assert steps == [
        {"command": "type_text", "params": {"selector": "#from-date", "text": "{{from_date}}"}}
    ]


def test_fills_by_label_when_there_is_no_usable_selector():
    steps = fill_steps(
        derive_parameters(
            [
                ev(
                    field_name="",
                    candidates=[{"kind": "text", "value": "From", "match_count": 3}],
                    element={"accessible_name": "From date"},
                )
            ]
        )
    )
    assert steps == [
        {"command": "type_into_label", "params": {"label": "From date", "text": "{{from_date}}"}}
    ]


def test_ignores_empty_and_non_string_values():
    assert derive_parameters([ev(value="   "), ev(value=None), ev(value=42)]) == []


def test_ignores_events_that_are_not_inputs():
    assert derive_parameters([ev(event_type="click"), ev(event_type="submit")]) == []
