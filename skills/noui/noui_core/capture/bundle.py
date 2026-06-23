"""Map a Tabby VNC recording bundle into NoUI ingestion payloads.

A Tabby recording bundle has the shape::

    {
      "session_id": "...",
      "recording_mode": "login" | "workflow",
      "started_at": "...", "stopped_at": "...",
      "har": {"log": {...}},
      "click_events": [ {event_type, tag_name, selector, value, field_role, ...}, ... ],
      "url_events": [ {from_url, to_url, timestamp}, ... ],
    }

Tabby's event field names were deliberately frozen to mirror NoUI's
``ClickEventCreate`` / ``UrlEventCreate`` schemas, so these adapters are mostly
field-projection + injecting the NoUI ``session_id``/``session_type``. Keeping
them pure (no I/O) makes them unit-testable; the CLI orchestrator does the HTTP.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Fields accepted by backend ClickEventCreate (backend/shared/schemas.py).
_CLICK_FIELDS = (
    "event_type",
    "url",
    "tag_name",
    "element_id",
    "class_name",
    "text_content",
    "href",
    "selector",
    "x",
    "y",
    "input_type",
    "value",
    "field_name",
    "field_role",
    "is_redacted",
    "autocomplete",
    "placeholder",
    "aria_label",
    "role_attr",
    "data_attrs_json",
    "timestamp",
)

_VALID_MODES = ("login", "workflow")


def save_bundle(bundle: dict[str, Any], name: str, output_root: str | None = None) -> Path:
    """Persist a capture bundle to ``<workbench>/bundles/<name>-<session>.json``.

    Capture bundles ({har, click_events, url_events}) are the source of truth for
    *regenerating* and *generalizing* assets — renaming tools, parameterizing
    request bodies, fixing anti-bot issues — so every capture should be saved, not
    just compiled-and-discarded. Returns the written path.
    """
    from noui_core.config import settings

    root = Path(output_root or settings.workbench_dir) / "bundles"
    root.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "capture").lower()).strip("-") or "capture"
    sid = str(bundle.get("session_id") or "")[:8]
    fname = f"{slug}-{sid}.json" if sid else f"{slug}.json"
    path = root / fname
    path.write_text(json.dumps(bundle, indent=2) + "\n")
    return path


def validate_bundle(bundle: dict[str, Any]) -> str:
    """Return the session_type ('login'|'workflow') or raise ValueError."""
    mode = bundle.get("recording_mode")
    if mode not in _VALID_MODES:
        raise ValueError(f"bundle.recording_mode must be one of {_VALID_MODES}, got {mode!r}")
    har = bundle.get("har")
    if not isinstance(har, dict) or "log" not in har:
        raise ValueError("bundle.har must be an object with a 'log' key (HAR 1.2)")
    return mode


def click_payloads(
    bundle: dict[str, Any], session_id: str, session_type: str
) -> list[dict[str, Any]]:
    """One POST /clicks body per recorded interaction event."""
    out: list[dict[str, Any]] = []
    for ev in bundle.get("click_events") or []:
        if not isinstance(ev, dict):
            continue
        payload: dict[str, Any] = {"session_id": session_id, "session_type": session_type}
        for key in _CLICK_FIELDS:
            if key in ev and ev[key] is not None:
                payload[key] = ev[key]
        # tag_name is required by the schema; skip malformed events without one.
        if not payload.get("tag_name"):
            continue
        out.append(payload)
    return out


def url_payloads(
    bundle: dict[str, Any], session_id: str, session_type: str
) -> list[dict[str, Any]]:
    """One POST /url-events body per recorded URL transition."""
    out: list[dict[str, Any]] = []
    for ev in bundle.get("url_events") or []:
        if not isinstance(ev, dict) or not ev.get("to_url"):
            continue
        out.append(
            {
                "session_id": session_id,
                "session_type": session_type,
                "from_url": ev.get("from_url", "") or "",
                "to_url": ev["to_url"],
            }
        )
    return out


def har_log(bundle: dict[str, Any]) -> dict[str, Any]:
    """The HAR document to upload (POST /sessions/{id}/har)."""
    har = bundle.get("har")
    if not isinstance(har, dict):
        raise ValueError("bundle.har is missing or not an object")
    return har


def count_sensitive_unredacted(bundle: dict[str, Any]) -> int:
    """Compliance guard: number of password/otp events NOT redacted in-pod.

    Should always be 0 — the worker redacts before the bundle leaves the pod.
    The CLI surfaces this as a hard failure if non-zero.
    """
    leaks = 0
    for ev in bundle.get("click_events") or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("field_role") in ("password", "otp") and not ev.get("is_redacted"):
            value = ev.get("value")
            if value not in (None, "", "[REDACTED]"):
                leaks += 1
    return leaks
