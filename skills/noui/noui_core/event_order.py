"""Ordering recorded interaction/URL events.

Bundles from Tabby schema_version >= 2 carry a `seq` on every click_event and
url_event: a single strictly-increasing counter, assigned by the injected
recorder AT INTERACTION TIME and rebased onto a session-global sequence by the
worker. Interaction events additionally carry `event_time`, the wall clock of the
interaction itself.

`timestamp` cannot order the stream, and its meaning is deliberately left
unchanged. The recorder debounces `input` by 500ms and builds the payload inside
that debounce, so `timestamp` on an input is the FLUSH: a human who fills a field
and clicks submit within 500ms (the ordinary login) produces a click stamped
EARLIER than the input that preceded it. Rather than redefine that field — which
existing consumers, including the login compiler here, already read — Tabby added
`seq` and `event_time` alongside it. `seq` is also immune to clock granularity (two
events in the same millisecond) and to the delivery order of the no-cors beacon
channel.

Bundles predating `seq`, and those synthesized by drivers that do not number
their events, have none — every helper here degrades to the recorded list order,
which is what the compilers relied on previously. Detection is per event, not off
`schema_version`: a bundle can lose `seq` in transit (the `/clicks` ingestion in
noui_core.capture.bundle projects onto a fixed column list), so the version can
say 2 while the events in hand carry no ordinal.
"""

from __future__ import annotations

from typing import Any


def event_seq(ev: Any) -> int | None:
    """The event's ordinal, or None when it carries none (or a malformed one)."""
    if not isinstance(ev, dict):
        return None
    raw = ev.get("seq")
    # bool is an int subclass — a `seq: true` is malformed, not ordinal 1.
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return raw


def has_seq(events: list[dict] | None) -> bool:
    """True when EVERY event carries an ordinal, so `seq` is a total order.

    All-or-nothing on purpose: a partially-numbered list has no consistent order
    to sort by, and mixing ordinals with list positions would silently reorder
    the un-numbered events. Empty lists are trivially ordered.
    """
    return all(event_seq(ev) is not None for ev in (events or []))


def order_events(events: list[dict] | None) -> list[dict]:
    """The events in interaction order — by `seq` when available, else as recorded.

    Stable, so events sharing an ordinal keep their recorded order.
    """
    evs = [ev for ev in (events or []) if isinstance(ev, dict)]
    if not has_seq(evs):
        return evs
    return sorted(evs, key=lambda ev: event_seq(ev) or 0)


def merged_order(*event_lists: list[dict] | None) -> bool:
    """True when every event across all lists carries an ordinal.

    Use before comparing ordinals ACROSS lists (clicks vs URL transitions): they
    share one counter only when both sides were numbered by the same recorder.
    """
    return all(has_seq(evs) for evs in event_lists)
