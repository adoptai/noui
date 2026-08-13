"""Turning recorded input values into operation parameters.

A recording captures ONE run: "1 Jan – 31 Jan", "account ...4471". Compiled
literally, the skill can only ever fetch that. On ICICI the agent was asked for
"last year", found nothing in the operation to vary, and spent twenty steps
hunting the page for a period selector it had no way to know existed.

The recorded values are the parameters — they just have to be recognised as
such. Each becomes an argument with the recorded value as its DEFAULT, so the
operation still does exactly what was recorded when nothing is passed, and does
something useful when something is.

Two rules this module will not bend:

  - Credentials are never parameters. Password and OTP values are redacted
    in-pod before they leave the browser, and a skill that accepted them as
    arguments would be asking callers to hold what Tabby exists to avoid holding.
  - A control we cannot drive is reported, not faked. A native <select> cannot
    be set with the browser command set available to a skill, so it is surfaced
    as a parameter the agent must handle rather than compiled into a step that
    would silently do nothing.
"""

from __future__ import annotations

import re
from typing import Any

from noui_core.capture.split import CREDENTIAL_FIELD_ROLES
from noui_core.compile.locators import choose_locator

#: Field roles that must never become parameters, whatever their value. Reuses
#: the SINGLE canonical set (capture/split.py) rather than a local subset: a
#: narrower copy here ({"password", "otp"}) silently let an ``unknown_sensitive``
#: field -- sensitive but not confidently password/otp, e.g. a re-entered
#: transaction PIN -- through unless it happened to be redacted upstream, and
#: compile it to a plaintext parameter default. Aligning fails closed on every
#: role Tabby treats as a credential (also ``username``).
_CREDENTIAL_ROLES = CREDENTIAL_FIELD_ROLES

#: The recorder writes this in place of a credential value.
_REDACTED = "[REDACTED]"

#: A <select>'s recorded value, normalised to letters/digits, that means "nothing
#: was chosen" -- the inert placeholder option a decoy dropdown sits on. Kept
#: deliberately tight: only values that are self-evidently non-selections, so a
#: real option (a year, an account, "All accounts") is never dropped.
_PLACEHOLDER_SELECT_VALUES = frozenset(
    {"value", "select", "choose", "none", "pleaseselect", "selectanoption", "selectone"}
)


def _is_placeholder_select_value(value: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower()) in _PLACEHOLDER_SELECT_VALUES


_DATE_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),  # 2026-01-31
    re.compile(r"^\d{2}[/-]\d{2}[/-]\d{4}$"),  # 31/01/2026, 31-01-2026
    re.compile(r"^\d{2}\s+\w{3,9}\s+\d{4}$"),  # 31 January 2026
)
_YEAR = re.compile(r"^(19|20)\d{2}$")
_AMOUNT = re.compile(r"^\d+(\.\d{1,2})?$")


def _param_type(value: str, field_name: str, input_type: str) -> str:
    """What KIND of value this is, so the caller knows what to pass.

    Read off the value first and the field name second: a field called
    ``txnDate`` holding ``2026-01-31`` is a date whatever its input type says,
    and a bank's date fields are routinely ``type="text"`` with a picker bolted
    on.
    """
    v = (value or "").strip()
    name = (field_name or "").lower()
    if input_type in ("date", "month"):
        return "date"
    if any(p.match(v) for p in _DATE_PATTERNS):
        return "date"
    if _YEAR.match(v) and ("year" in name or "yr" in name):
        return "year"
    if _AMOUNT.match(v) and any(k in name for k in ("amount", "amt", "value", "sum")):
        return "amount"
    return "string"


def _param_name(field_name: str, label: str, index: int) -> str:
    """A readable argument name — the field's own name, else its label."""
    raw = (field_name or label or "").strip()
    slug = re.sub(r"(?<!^)(?=[A-Z])", "_", raw)  # fromDate -> from_Date
    slug = re.sub(r"[^a-z0-9]+", "_", slug.lower()).strip("_")
    return slug or f"value_{index + 1}"


def _label_of(ev: dict) -> str:
    """The human-readable label for the field, when the recording carries one."""
    element = ev.get("element")
    if isinstance(element, dict) and element.get("accessible_name"):
        return str(element["accessible_name"])
    for c in ev.get("candidates") or []:
        if isinstance(c, dict) and c.get("kind") == "label" and c.get("value"):
            return str(c["value"])
    return str(ev.get("placeholder") or ev.get("aria_label") or "")


def derive_parameters(events: list[dict] | None) -> list[dict]:
    """Parameters implied by the values a human typed or chose.

    ``events`` are the input/change interactions belonging to one operation, in
    interaction order. The LAST value for a field wins: a human who types, edits
    and retypes leaves several events for one field, and what matters is what it
    held when they acted on it.

    Each parameter is ``{name, type, default, control, selector|label,
    settable}``. ``settable`` is False for a control no browser command can
    drive; the caller surfaces those rather than compiling a step that would
    quietly do nothing.
    """
    by_field: dict[str, dict] = {}

    for index, ev in enumerate(events or []):
        if (ev.get("event_type") or "") not in ("input", "change"):
            continue
        role = ev.get("field_role") or ""
        value = ev.get("value")
        if role in _CREDENTIAL_ROLES or ev.get("is_redacted") or value == _REDACTED:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        # Checkbox/radio states are not values a caller supplies; they are part
        # of the recorded path and belong in the steps, not the signature.
        if value in ("checked", "unchecked"):
            continue

        tag = (ev.get("tag_name") or "").lower()
        input_type = (ev.get("input_type") or "").lower()
        # A <select> still sitting on its placeholder option was never a choice
        # the human made. ICICI's #FieldDropdown is exactly this: its recorded
        # value is the literal "Value", the inert first option. Turning that into
        # a parameter + select_option drives a meaningless control and asks the
        # caller to supply a value that means "nothing is selected". A real
        # selection (a period, an account) carries a real value and is kept.
        if tag == "select" and _is_placeholder_select_value(value):
            continue
        label = _label_of(ev)
        name = _param_name(str(ev.get("field_name") or ""), label, index)

        locator = choose_locator(ev.get("candidates"))
        param: dict[str, Any] = {
            "name": name,
            "type": _param_type(value, str(ev.get("field_name") or ""), input_type),
            "default": value,
            "control": "select" if tag == "select" else "text",
            # A native <select> IS settable: the runtime has had select_option
            # (by option value or by visible label) all along -- see
            # execute-browser-handler's `case 'select_option'`. Marking these
            # read-only meant every dropdown a skill needed was surfaced as
            # something to report rather than something to drive, and the only
            # way left to change one was to click through whatever widget the
            # page draws on top of it. On ICICI that is three brittle steps per
            # dropdown, keyed on the overlay's current-state classes and on a
            # :text-is holding the whole option list, and it stalled every
            # replay mid-panel.
            "settable": True,
        }
        if label:
            param["label"] = label
        if locator and locator.get("is_css"):
            param["selector"] = locator["value"]
        elif label:
            param["by_label"] = label

        by_field[name] = param  # last write wins

    return list(by_field.values())


def fill_steps(parameters: list[dict]) -> list[dict]:
    """Steps that put each parameter's value into its field.

    Templated on the parameter name, so the operation runs as recorded when
    nothing is passed and takes an override when something is. Parameters whose
    control cannot be driven produce no step — see ``settable``.
    """
    steps: list[dict] = []
    for p in parameters:
        if not p.get("settable"):
            continue
        placeholder = "{{" + p["name"] + "}}"
        if p.get("control") == "select" and p.get("selector"):
            # A dropdown is chosen, not typed into. select_option takes the
            # option's value or its visible label, so the recorded default and a
            # caller's override both work unchanged.
            steps.append(
                {
                    "command": "select_option",
                    "params": {"selector": p["selector"], "value": placeholder},
                }
            )
        elif p.get("selector"):
            steps.append(
                {"command": "type_text", "params": {"selector": p["selector"], "text": placeholder}}
            )
        elif p.get("by_label"):
            steps.append(
                {
                    "command": "type_into_label",
                    "params": {"label": p["by_label"], "text": placeholder},
                }
            )
    return steps
