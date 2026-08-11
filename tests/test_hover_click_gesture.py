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
    assert out[0]["params"] == {"selector": ITEM, "hover_first": NAV}
    # The click's expectation describes where the gesture lands; the hover's
    # described opening a menu and is not the postcondition.
    assert out[0]["expect"] == {"url": "https://x/credit-card"}


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
