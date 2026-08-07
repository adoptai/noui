"""Choosing how a compiled step should address an element.

Tabby's recorder (schema_version >= 4) stops guessing. Instead of one selector
picked in-page by a fixed ladder, each recorded interaction carries several
CANDIDATES — test-id, id, name, aria-label, role+accessible-name, label, text,
css path — every one of them with `match_count`, the number of nodes it actually
matched at the instant it was recorded.

That count is the whole point. The old recorder could emit `a.mb-0` for a link on
ICICI's dashboard with no idea it matched forty elements; the skill compiled
cleanly and misclicked in production. Here, a candidate that matched more than
one node is known to be untrustworthy BEFORE the skill ships.

This module is deliberately pure and knows nothing about steps or skills: given
candidates, return the best way to address the element and how much to trust it.
"""

from __future__ import annotations

from typing import Any

# Durability order, most durable first. Not preference-by-taste: this is roughly
# "how likely is this to survive a redesign".
#   testid      — put there for automation; changes only deliberately
#   id/name     — stable when authored, though frameworks do generate them
#   aria_label  — accessibility text; survives visual restyling
#   role_name   — semantic identity; survives DOM restructuring
#   label       — the form label a human reads
#   text        — visible copy; survives restructuring, dies on rewording
#   css_path    — structural; the first thing a redesign breaks
_KIND_RANK: dict[str, int] = {
    "testid": 0,
    "id": 1,
    "name": 2,
    "aria_label": 3,
    "role_name": 4,
    "label": 5,
    "text": 6,
    "css_path": 7,
}

# Kinds whose `value` is a CSS selector, so a step can address them with
# `click_element`. The rest carry human-readable text that the runtime resolves
# semantically (click_by_text / type_into_label).
_CSS_KINDS = frozenset({"testid", "id", "name", "aria_label", "css_path"})

#: Returned confidence levels, worst to best.
UNIQUE = "unique"
UNKNOWN = "unknown"
AMBIGUOUS = "ambiguous"


def _rank(kind: Any) -> int:
    return _KIND_RANK.get(str(kind), len(_KIND_RANK))


def choose_locator(candidates: Any) -> dict | None:
    """The most durable trustworthy way to address the element, or None.

    Returns ``{"kind", "value", "match_count", "confidence", "is_css"}``.

    Selection order:

    1. Candidates that matched EXACTLY ONE node, best kind first. These are the
       only ones known to identify this element.
    2. Failing that, candidates whose count could not be taken (``-1`` — the
       page could not evaluate the selector). Unknown is not the same as wrong,
       and it beats a locator we know to be ambiguous.
    3. Failing that, the best-ranked AMBIGUOUS candidate, so the caller can still
       emit something and flag it rather than dropping the step entirely.

    ``match_count == 0`` is never selected. It means the candidate did not match
    even the element it was recorded from — which happens legitimately for
    elements inside a shadow root or a cross-document iframe, where the top
    document's query cannot see them. Emitting one guarantees a runtime failure.

    None means there is nothing usable at all: no candidates, or every one of
    them matched zero nodes.
    """
    usable: list[dict] = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        value = c.get("value")
        kind = c.get("kind")
        if not value or not isinstance(value, str) or not kind:
            continue
        count = c.get("match_count")
        if not isinstance(count, int) or isinstance(count, bool):
            continue
        if count == 0:
            continue
        usable.append({"kind": str(kind), "value": value, "match_count": count})

    if not usable:
        return None

    def _pick(pool: list[dict], confidence: str) -> dict | None:
        if not pool:
            return None
        best = min(pool, key=lambda c: _rank(c["kind"]))
        return {
            "kind": best["kind"],
            "value": best["value"],
            "match_count": best["match_count"],
            "confidence": confidence,
            "is_css": best["kind"] in _CSS_KINDS,
        }

    return (
        _pick([c for c in usable if c["match_count"] == 1], UNIQUE)
        or _pick([c for c in usable if c["match_count"] < 0], UNKNOWN)
        or _pick([c for c in usable if c["match_count"] > 1], AMBIGUOUS)
    )
