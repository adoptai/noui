"""Local record of what each recording session was **provisioned as**.

``capture_record.py`` writes one small JSON file per provisioned session, so
``capture_import.py`` can later ask "what did the operator actually ask for?"
instead of trusting the mode Tabby stamps on the drained bundle (which is
unreliable — see ``noui_core.capture.classify``).

This is intentionally a flat file next to the bundles rather than server state:
the declared mode is a *client* fact, and keeping it client-side is what makes
NoUI independent of whether Tabby reports the mode correctly.

Entries are advisory. A missing ledger (an old session, a bundle copied from
another machine, a session provisioned outside NoUI) is normal, and callers fall
back to content classification.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from noui_core.capture.classify import MODES


def _root() -> Path:
    from noui_core.config import settings

    return Path(settings.workbench_dir) / "sessions"


def path_for(session_id: str) -> Path:
    return _root() / f"{session_id}.json"


def record(
    session_id: str,
    *,
    declared_mode: str,
    url: str = "",
    profile: str = "",
    from_session: str = "",
    residential: bool = False,
    browser_driven: bool = False,
) -> Path:
    """Persist what this session was provisioned as. Returns the written path."""
    if declared_mode not in MODES:
        raise ValueError(f"declared_mode must be one of {MODES}, got {declared_mode!r}")
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    path = path_for(session_id)
    path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "declared_mode": declared_mode,
                "url": url,
                "profile": profile,
                "from_session": from_session,
                "residential": residential,
                "browser_driven": bool(browser_driven),
                "created_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
        + "\n"
    )
    return path


def lookup(session_id: str) -> dict[str, Any] | None:
    """Return the ledger entry for a session, or None if there isn't one."""
    if not session_id:
        return None
    path = path_for(session_id)
    try:
        entry = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) else None


def declared_browser_driven(session_id: str) -> bool:
    """Was this session recorded FOR a browser-driven skill?

    The kind is a decision, usually the member's own words ("a browser based
    skill"), and it was taken at record time and then thrown away: capture_record
    used the flag to provision and never wrote it down, so capture_import had to
    be told again on its own command line. Forget it there -- easily done, since
    nothing in the recording says so -- and an app that must be driven compiles
    to replayed API calls instead. An ICICI capture asked for as a BROWSER BASED
    skill shipped as two Finacle POSTs.
    """
    return bool((lookup(session_id) or {}).get("browser_driven"))


def declared_mode(session_id: str) -> str:
    """The mode this session was provisioned as, or "" when unknown/unusable."""
    entry = lookup(session_id) or {}
    mode = entry.get("declared_mode", "")
    return mode if mode in MODES else ""
