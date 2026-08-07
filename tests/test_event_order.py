"""Tests for noui_core.event_order — interaction ordering of recorded events.

The bug these guard: Tabby's injected recorder debounces `input` by 500ms and
builds the event payload inside that debounce, so `timestamp` records the FLUSH.
A human who fills a field and clicks submit within 500ms — the ordinary login —
produces a click stamped EARLIER than the input before it. `timestamp` keeps that
meaning (existing consumers read it); the recorder now also emits `seq`, assigned
at interaction time. These tests pin the consumer side to `seq` and to graceful
degradation for bundles recorded before it existed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.event_order import event_seq, has_seq, merged_order, order_events


class TestEventSeq:
    def test_reads_the_ordinal(self) -> None:
        assert event_seq({"seq": 7}) == 7
        assert event_seq({"seq": 0}) == 0

    def test_missing_or_malformed_is_none(self) -> None:
        assert event_seq({}) is None
        assert event_seq({"seq": None}) is None
        assert event_seq({"seq": "3"}) is None
        assert event_seq({"seq": 1.5}) is None
        assert event_seq({"seq": -1}) is None
        assert event_seq("not a dict") is None

    def test_bool_is_not_an_ordinal(self) -> None:
        # bool subclasses int — `seq: True` is a malformed event, not ordinal 1.
        assert event_seq({"seq": True}) is None


class TestHasSeq:
    def test_all_numbered(self) -> None:
        assert has_seq([{"seq": 1}, {"seq": 2}])

    def test_empty_is_trivially_ordered(self) -> None:
        assert has_seq([])
        assert has_seq(None)

    def test_partial_numbering_is_not_an_order(self) -> None:
        # Mixing ordinals with list positions would silently reorder the rest.
        assert not has_seq([{"seq": 1}, {"tag_name": "INPUT"}])


class TestOrderEvents:
    def test_sorts_by_seq(self) -> None:
        evs = [{"seq": 3, "n": "c"}, {"seq": 1, "n": "a"}, {"seq": 2, "n": "b"}]
        assert [e["n"] for e in order_events(evs)] == ["a", "b", "c"]

    def test_recovers_a_debounced_fill_that_arrived_after_its_own_click(self) -> None:
        # Arrival order: the click beat the 500ms input flush.
        arrived = [
            {"seq": 2, "event_type": "click", "text_content": "Sign in"},
            {"seq": 1, "event_type": "input", "field_role": "password"},
        ]
        assert [e["event_type"] for e in order_events(arrived)] == ["input", "click"]

    def test_unnumbered_bundle_keeps_recording_order(self) -> None:
        evs = [{"n": "a"}, {"n": "b"}, {"n": "c"}]
        assert [e["n"] for e in order_events(evs)] == ["a", "b", "c"]

    def test_partially_numbered_bundle_keeps_recording_order(self) -> None:
        evs = [{"seq": 9, "n": "a"}, {"n": "b"}]
        assert [e["n"] for e in order_events(evs)] == ["a", "b"]

    def test_ties_keep_recording_order(self) -> None:
        evs = [{"seq": 1, "n": "a"}, {"seq": 1, "n": "b"}]
        assert [e["n"] for e in order_events(evs)] == ["a", "b"]

    def test_drops_non_dicts(self) -> None:
        assert order_events([{"seq": 1}, "junk", None]) == [{"seq": 1}]

    def test_does_not_mutate_the_input(self) -> None:
        evs = [{"seq": 2, "n": "b"}, {"seq": 1, "n": "a"}]
        order_events(evs)
        assert [e["n"] for e in evs] == ["b", "a"]


class TestMergedOrder:
    def test_true_only_when_both_sides_are_numbered(self) -> None:
        clicks = [{"seq": 1}]
        urls = [{"seq": 2}]
        assert merged_order(clicks, urls)
        assert not merged_order(clicks, [{"to_url": "x"}])
