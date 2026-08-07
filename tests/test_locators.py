"""Tests for locator-candidate selection (noui_core.compile.locators).

Tabby's recorder emits several ways to address each clicked element, every one
carrying `match_count` — how many nodes it matched when it was recorded. These
pin the rule that a candidate matching more than one node is never preferred over
one that matched exactly one, which is what stops a skill compiling cleanly and
then misclicking in production.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "noui"))

from noui_core.compile.locators import (  # noqa: E402
    AMBIGUOUS,
    UNIQUE,
    UNKNOWN,
    choose_locator,
)


def c(kind: str, value: str, match_count: int) -> dict:
    return {"kind": kind, "value": value, "match_count": match_count}


def test_prefers_a_unique_match_over_a_more_durable_ambiguous_one():
    # A test-id is the most durable KIND, but one that matched three nodes cannot
    # identify this element. Uniqueness outranks durability — it is the whole
    # reason match_count is recorded.
    got = choose_locator([c("testid", '[data-testid="row"]', 3), c("id", "#pay-now", 1)])
    assert got["value"] == "#pay-now"
    assert got["confidence"] == UNIQUE


def test_prefers_the_most_durable_among_unique_candidates():
    got = choose_locator(
        [c("css_path", "div > a:nth-of-type(2)", 1), c("testid", '[data-testid="pay"]', 1)]
    )
    assert got["kind"] == "testid"


def test_never_selects_a_candidate_that_matched_nothing():
    # match_count 0 means it did not match even the element it was recorded from
    # — normal for a node inside a shadow root, where the document query cannot
    # see it. Emitting one guarantees a runtime failure.
    got = choose_locator([c("id", "#ghost", 0), c("text", "Continue", 2)])
    assert got["value"] == "Continue"
    assert got["confidence"] == AMBIGUOUS


def test_returns_none_when_nothing_is_addressable():
    assert choose_locator([c("id", "#ghost", 0)]) is None
    assert choose_locator([]) is None
    assert choose_locator(None) is None


def test_an_uncountable_candidate_beats_a_known_ambiguous_one():
    # -1 is "the page could not evaluate this", which is not the same as wrong.
    got = choose_locator([c("id", "#weird", -1), c("text", "Open", 5)])
    assert got["kind"] == "id"
    assert got["confidence"] == UNKNOWN


def test_falls_back_to_ambiguous_rather_than_dropping_the_step():
    # Emitting a flagged step beats emitting nothing: the caller surfaces it for
    # re-pointing, where a dropped step just makes the skill silently incomplete.
    got = choose_locator([c("text", "Download", 4)])
    assert got["confidence"] == AMBIGUOUS
    assert got["match_count"] == 4


def test_marks_which_candidates_are_css_expressible():
    assert choose_locator([c("id", "#x", 1)])["is_css"] is True
    assert choose_locator([c("role_name", "link|Statements", 1)])["is_css"] is False


def test_ignores_malformed_candidates():
    # A bundle can arrive with anything in it; a bad entry must not sink the rest.
    got = choose_locator(
        [
            "not-a-dict",
            {"kind": "id"},  # no value
            {"kind": "id", "value": "#a", "match_count": "1"},  # count not an int
            {"kind": "id", "value": "#b", "match_count": True},  # bool is not a count
            c("text", "Go", 1),
        ]
    )
    assert got["value"] == "Go"
