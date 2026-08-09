"""Bind a compiled browser skill to the recording it was compiled from.

WHY THIS EXISTS. A browser skill's operations are only worth anything because a
human once did the task and the recorder watched: the selectors are ones that
resolved, the page order is one that happened, and "this click downloads a file"
is an observation rather than a hope. A skill written from memory of how a site
probably looks has the same shape and none of that, and it is not reviewable --
nobody can tell a real ICICI selector from a plausible one by reading it.

That is not hypothetical. An agent asked for an "ICICI browser skill" wrote
SKILL.md and operations.json itself, never recorded anything, and uploaded it to
an org catalog.

WHAT CAN AND CANNOT BE PROVEN. Nothing inside the skill directory proves
provenance -- every file there is written by whoever authored the skill, so a
fabricated skill can carry a fabricated stamp. Proof needs an INDEPENDENT
artifact, and the only one available is the recording bundle: a large, awkward
object produced by actually driving a browser. So this module stamps a pointer
to that bundle plus a digest of it, and the installer verifies the bundle exists
and still hashes the same. Faking that means synthesising a whole self-
consistent recording -- far more work than recording, which is the point.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

PROVENANCE_VERSION = "1"

#: Written beside the skill: the exact recording the operations were compiled
#: from. Not uploaded to the catalog — it is evidence for the installer, which
#: verifies the digest below still matches it.
BUNDLE_FILE = "recording_bundle.json"


def bundle_digest(bundle: dict[str, Any]) -> str:
    """A digest of the recording, reproducible from the bundle file alone.

    Canonical JSON rather than raw file bytes: the compiler holds a parsed dict
    and the verifier holds a file, and those need to agree. Sorted keys and tight
    separators make the two paths land on the same string.
    """
    blob = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def observed_selectors(bundle: dict[str, Any]) -> set[str]:
    """Every locator value the recorder actually saw, in any form.

    Union of the candidates it ranked, the locator it chose and the visible text
    it read. Deliberately generous: this is used to catch selectors that were
    INVENTED, so a false accusation is far worse than missing one fabrication.
    """
    seen: set[str] = set()
    for click in bundle.get("click_events") or []:
        if not isinstance(click, dict):
            continue
        for cand in click.get("candidates") or []:
            if isinstance(cand, dict) and cand.get("value"):
                seen.add(str(cand["value"]))
                # role_name candidates are "role|name"; the step keeps the name.
                if "|" in str(cand["value"]):
                    seen.add(str(cand["value"]).split("|", 1)[1])
        loc = click.get("locator")
        if isinstance(loc, dict) and loc.get("value"):
            seen.add(str(loc["value"]))
            if "|" in str(loc["value"]):
                seen.add(str(loc["value"]).split("|", 1)[1])
        for key in ("selector", "text", "css_path"):
            if click.get(key):
                seen.add(str(click[key]).strip())
    return {s for s in seen if s}


def build(bundle: dict[str, Any], *, bundle_file: str) -> dict[str, Any]:
    """The provenance block stamped into a browser skill's manifest.

    bundle_file is relative to the workbench root (``bundles/<name>-<sid>.json``)
    so the installer can find it without knowing absolute paths in a sandbox it
    did not create.
    """
    return {
        "provenance_version": PROVENANCE_VERSION,
        "source": "recording",
        "recording_session_id": str(bundle.get("session_id") or ""),
        "bundle_file": bundle_file,
        "bundle_sha256": bundle_digest(bundle),
        "recorded_events": len(bundle.get("click_events") or []),
        "recorded_urls": len(bundle.get("url_events") or []),
        "observed_selectors": len(observed_selectors(bundle)),
        "schema_version": bundle.get("schema_version"),
    }
