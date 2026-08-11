"""A hover and the click it enables are one gesture, so they are one step.

As two steps they are two round trips to the worker, and a menu that only exists
while the pointer rests on its trigger cannot survive the gap. Measured on ICICI:
hover returned ok, and the submenu was not visible on any later call — checked at
1s, 2s, 3s and 4s. The click was racing the menu closing, which looked like
slowness and got "fixed" twice by waiting longer — the opposite of the cure.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.compile.browser_skill import _fold_hover_into_click  # noqa: E402

NAV = "#scroll-container > div > div:nth-of-type(5)"
ITEM = "a.sub-menu-list-item-link"


def test_the_pair_becomes_one_step():
    out = _fold_hover_into_click([
        {"command": "hover", "params": {"selector": NAV}, "expect": {"settle_ms": 4322}},
        {"command": "click_element", "params": {"selector": ITEM},
         "expect": {"url": "https://x/credit-card"}},
        {"command": "get_page_summary", "params": {}},
    ])
    assert [s["command"] for s in out] == ["click_element", "get_page_summary"]
    # The click is addressed INSIDE what the hover revealed. `a.sub-menu-list-
    # item-link` is a class every submenu item in this nav carries, so unscoped
    # it resolved whichever match came first in the document — and when that one
    # sat in a menu that was not open, the step failed "on the page but not
    # visible" on about half of all runs. The bare selector stays as a fallback
    # for a menu rendered in an overlay rather than inside its trigger.
    assert out[0]["params"] == {
        "selector": f"{NAV} {ITEM}",
        "hover_first": NAV,
        "fallbacks": [{"selector": ITEM}],
    }
    # The click's expectation describes where the gesture lands; the hover's
    # described opening a menu and is not the postcondition.
    # Plus the hover's own measurement of how long its menu takes to appear —
    # the click never measured that, and without it the step fell back to the
    # runtime's 3s floor.
    assert out[0]["expect"] == {"url": "https://x/credit-card", "settle_ms": 4322}


def test_a_hover_before_something_else_is_left_alone():
    steps = [
        {"command": "hover", "params": {"selector": NAV}},
        {"command": "click_by_text", "params": {"text": "Credit Cards"}},
    ]
    assert [s["command"] for s in _fold_hover_into_click(steps)] == ["hover", "click_by_text"]


def test_a_trailing_hover_survives():
    steps = [{"command": "hover", "params": {"selector": NAV}}]
    assert _fold_hover_into_click(steps) == steps


def test_an_ordinary_click_is_untouched():
    steps = [{"command": "click_element", "params": {"selector": "#DL"}}]
    assert _fold_hover_into_click(steps) == steps


def test_consecutive_pairs_both_fold():
    steps = [
        {"command": "hover", "params": {"selector": "#a"}},
        {"command": "click_element", "params": {"selector": "#b"}},
        {"command": "hover", "params": {"selector": "#c"}},
        {"command": "click_element", "params": {"selector": "#d"}},
    ]
    out = _fold_hover_into_click(steps)
    assert len(out) == 2
    assert out[0]["params"]["hover_first"] == "#a"
    assert out[1]["params"]["hover_first"] == "#c"


def test_a_selector_that_already_names_one_node_is_left_alone():
    """An id addresses one node by definition; scoping it would only add risk."""
    from noui_core.compile.browser_skill import _fold_hover_into_click

    out = _fold_hover_into_click([
        {"command": "hover", "params": {"selector": "#nav"}},
        {"command": "click_element", "params": {"selector": "#cards"}},
    ])
    assert out[0]["params"] == {"selector": "#cards", "hover_first": "#nav"}


def test_a_recorded_path_is_left_alone_too():
    """A css_path is a walk down the tree, not a style shared by many nodes."""
    from noui_core.compile.browser_skill import _fold_hover_into_click

    path = "#scroll-container > div > div:nth-of-type(2) > a"
    out = _fold_hover_into_click([
        {"command": "hover", "params": {"selector": "#nav"}},
        {"command": "click_element", "params": {"selector": path}},
    ])
    assert out[0]["params"]["selector"] == path
    assert "fallbacks" not in out[0]["params"]


def test_an_existing_fallback_is_kept_behind_the_unscoped_one():
    from noui_core.compile.browser_skill import _fold_hover_into_click

    out = _fold_hover_into_click([
        {"command": "hover", "params": {"selector": "#nav"}},
        {"command": "click_element",
         "params": {"selector": "a.item", "fallbacks": [{"text": "Credit Cards"}]}},
    ])
    assert out[0]["params"]["selector"] == "#nav a.item"
    assert out[0]["params"]["fallbacks"] == [{"selector": "a.item"}, {"text": "Credit Cards"}]


def test_scoping_a_recorded_control_inside_another_keeps_its_provenance():
    """The gate must not read the compiler's own output as a hand edit.

    Scoping composes two selectors the recording DID see, and the result can
    only match fewer nodes than the half on its right — so nothing was invented.
    """
    from noui_core.compile.provenance import _is_observed_scoped_inside_observed

    seen = {"#nav", "a.item"}
    assert _is_observed_scoped_inside_observed("#nav a.item", seen) is True
    # Neither half invented, and nothing looser: a control nobody saw stays
    # unobserved however it is combined.
    assert _is_observed_scoped_inside_observed("#nav a.invented", seen) is False
    assert _is_observed_scoped_inside_observed("#other a.item", seen) is False
    assert _is_observed_scoped_inside_observed("a.item", seen) is False


def test_the_merged_step_keeps_the_hovers_measurement_of_the_menu():
    """How long the menu takes was measured on the HOVER, not on the click.

    Folding threw it away with the rest of the hover's expectation, so the
    merged step carried no settle and fell back to the runtime's 3s floor —
    while ICICI's nav measured 2161ms and renders its submenu right around
    there. That is the coin flip.
    """
    from noui_core.compile.browser_skill import _fold_hover_into_click

    out = _fold_hover_into_click([
        {"command": "hover", "params": {"selector": "#nav"},
         "expect": {"settle_ms": 4322}},
        {"command": "click_element", "params": {"selector": "#cards"},
         "expect": {"url": "https://x.test/credit-card"}},
    ])
    assert out[0]["expect"] == {"url": "https://x.test/credit-card", "settle_ms": 4322}


def test_the_clicks_own_measurement_wins_when_it_has_one():
    """What the click observed is about where it landed — the better answer."""
    from noui_core.compile.browser_skill import _fold_hover_into_click

    out = _fold_hover_into_click([
        {"command": "hover", "params": {"selector": "#nav"}, "expect": {"settle_ms": 4322}},
        {"command": "click_element", "params": {"selector": "#cards"},
         "expect": {"settle_ms": 900}},
    ])
    assert out[0]["expect"]["settle_ms"] == 900
