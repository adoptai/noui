"""Custom-section preservation for regenerated Markdown files.

Users hand-edit sections of generated SKILL.md / API.md. On regeneration we
need to carry those edits forward. Convention: wrap user-editable regions in

    <!-- custom:start:NAME -->
    ...free-form markdown...
    <!-- custom:end:NAME -->

fences. `merge_custom_sections(existing, new)` pulls every fenced region out
of `existing` and splices it into the matching fence in `new`, overwriting
whatever default placeholder the generator emitted.

If a fence appears in `existing` but not in `new`, it is dropped — the
generator decides which fence names are valid. If a fence appears in `new`
but not in `existing` (fresh regen), the generator's default content stands.

Malformed / nested fences in either input cause the merger to return `new`
unchanged and emit a warning via the provided logger, so we never silently
corrupt user content.
"""

from __future__ import annotations

import logging
import re

_FENCE_RE = re.compile(
    r"<!--\s*custom:start:(?P<name>[A-Za-z0-9_\-]+)\s*-->"
    r"(?P<body>.*?)"
    r"<!--\s*custom:end:(?P=name)\s*-->",
    re.DOTALL,
)

_log = logging.getLogger(__name__)


def merge_custom_sections(existing: str, new: str) -> str:
    """Return `new` with `<!-- custom:start:NAME -->` regions replaced from `existing`.

    Args:
        existing: Prior file contents (may be empty).
        new: Freshly rendered file contents with default/placeholder custom regions.

    Returns:
        Merged Markdown string.
    """
    if not existing:
        return new

    existing_regions = _extract_regions(existing)
    if existing_regions is None:
        _log.warning(
            "merge_custom_sections: malformed fences in existing file; keeping regenerated content verbatim"
        )
        return new

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name in existing_regions:
            return f"<!-- custom:start:{name} -->{existing_regions[name]}<!-- custom:end:{name} -->"
        return match.group(0)

    return _FENCE_RE.sub(replace, new)


def _extract_regions(text: str) -> dict[str, str] | None:
    """Return a name→body map of well-formed fenced regions, or None on ambiguity.

    Ambiguity checks:
        - A `custom:start:NAME` with no matching `custom:end:NAME` → None.
        - A `custom:end:NAME` with no preceding matching start → None.
        - Duplicate `custom:start:NAME` → None.
    """
    starts = list(re.finditer(r"<!--\s*custom:start:(?P<name>[A-Za-z0-9_\-]+)\s*-->", text))
    ends = list(re.finditer(r"<!--\s*custom:end:(?P<name>[A-Za-z0-9_\-]+)\s*-->", text))
    start_names = [m.group("name") for m in starts]
    end_names = [m.group("name") for m in ends]

    if sorted(start_names) != sorted(end_names):
        return None
    if len(start_names) != len(set(start_names)):
        return None

    regions: dict[str, str] = {}
    for match in _FENCE_RE.finditer(text):
        regions[match.group("name")] = match.group("body")

    if len(regions) != len(start_names):
        # FENCE_RE didn't match all pairs (e.g. interleaved or nested).
        return None

    return regions
