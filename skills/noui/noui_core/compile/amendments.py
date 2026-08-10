"""Steps discovered at replay, kept apart from the ones that were recorded.

WHY A SEPARATE FILE. `operations.json` is what a human was observed doing, and
the digest stamped over it is what makes that claim checkable. The moment
anything else is written into it the claim is gone -- which is exactly what kept
happening: a compiled locator died at replay, the agent found a control that
worked, and wrote it into operations.json. The skill then had no provenance and
the gate refused it, so the discovery was lost along with the evidence.

The discovery was not worthless. It was just a different KIND of evidence:
observed working once, at replay, rather than observed being used by a human.
Two classes, never merged silently -- so a member approving the skill can see
precisely which steps nobody watched a human perform.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

AMENDMENTS_FILE = "amendments.json"


def amendment_id(a: dict[str, Any]) -> str:
    """A stable id for one amendment, so an approval names what it approved."""
    material = {
        "operation": a.get("operation"),
        "step_index": a.get("step_index"),
        "replacement": a.get("replacement"),
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def load(raw: bytes | str | None) -> list[dict[str, Any]]:
    """Parse an amendments file, tolerating absence and malformation.

    A malformed file reads as none: an amendment nobody can interpret must never
    become an amendment nobody reviewed.
    """
    if not raw:
        return []
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        doc = json.loads(text)
    except (ValueError, UnicodeDecodeError, AttributeError):
        return []
    items = doc.get("amendments") if isinstance(doc, dict) else doc
    if not isinstance(items, list):
        return []
    out = []
    for a in items:
        if isinstance(a, dict) and a.get("operation") and a.get("replacement"):
            out.append(a)
    return out


def is_verified(a: dict[str, Any]) -> bool:
    """Did the replay that recorded this amendment actually RUN the new step?

    An amendment is only worth what the replay proved about it. One build
    confirmed a single control by hand and then amended the same step across six
    operations; two of those ran, four were blocked long before reaching the
    amended step, and all six went into the file saying "confirmed working in
    live session". Absent means unverified: an amendment written before this
    field existed has no evidence either.
    """
    return bool(a.get("verified"))


def describe(a: dict[str, Any]) -> str:
    """One line a member can decide on."""
    rep = a.get("replacement") or {}
    params = rep.get("params") or {}
    target = params.get("selector") or params.get("text") or params.get("label") or "?"
    why = str(a.get("why") or "the recorded locator matched nothing")
    if is_verified(a):
        mark = "ran OK in the replay"
    else:
        mark = "NOT EXERCISED — the replay never reached this step"
    return (
        f"{a.get('operation')} step {a.get('step_index')}: "
        f"{rep.get('command', '?')} {target} — {why} [{mark}]"
    )
