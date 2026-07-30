"""Summarise a capture bundle: what's in it, and what compile would make of it.

Pure (no I/O): ``summarize()`` returns a dict; ``scripts/bundle_inspect.py``
renders it. Two jobs:

1. **Answer "is this bundle what I think it is?"** — the mode block puts Tabby's
   stamp, NoUI's classification and the provisioned (ledger) mode side by side,
   so a mismatch is a one-line read instead of an ad-hoc script.
2. **Preview the operation set** — the endpoint table is built with the
   *compiler's own* filter, path templating and tool naming
   (``compile.har_to_tools``), so it cannot drift into being a second opinion
   about what compile will emit.

Never emits recorded values — only field roles and redaction status — so it is
safe to run on any bundle and paste the output.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from noui_core.capture.bundle import count_sensitive_unredacted
from noui_core.capture.classify import bundle_signals, classify_bundle, diagnose_missing_login
from noui_core.capture.split import CREDENTIAL_FIELD_ROLES

# Deliberately the compiler's own internals: the table must mirror compile, not
# re-implement it. If these move, this preview should move with them.
from noui_core.compile.har_to_tools import _is_api_call, _path_template, _tool_name


def _response_size(resp: dict[str, Any]) -> int:
    """Bytes of response body actually captured.

    Tabby's HAR carries ``content.text`` but no ``content.size``, so trusting
    ``size`` alone reports 0 for every entry — which reads as "empty response"
    when the body is right there. Measure the text, then fall back to bodySize.
    A genuine 0 is useful signal: nothing was captured for that call.
    """
    content = resp.get("content") or {}
    size = content.get("size") or 0
    if size:
        return int(size)
    text = content.get("text")
    if isinstance(text, str):
        return len(text)
    return max(int(resp.get("bodySize") or 0), 0)


def _endpoints(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """The operations compile would emit, in recording order.

    Same filter + dedup key + naming as ``har_to_tools.har_to_tool_defs``:
    ``(method, base_url, path_template)``, first occurrence wins.
    """
    entries = ((bundle.get("har") or {}).get("log") or {}).get("entries") or []
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    used_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not _is_api_call(entry):
            continue
        req = entry.get("request") or {}
        resp = entry.get("response") or {}
        parsed = urlparse(req.get("url", ""))
        method = (req.get("method") or "GET").upper()
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        path_template, path_params = _path_template(parsed.path)
        key = (method, base_url, path_template)
        if key in seen:
            seen[key]["occurrences"] += 1
            continue
        content = resp.get("content") or {}
        name = _tool_name(method, path_template, used_names)
        used_names.add(name)
        seen[key] = {
            "method": method,
            "host": parsed.netloc,
            "path_template": path_template,
            "path_params": path_params,
            "status": resp.get("status", 0),
            "mime": (content.get("mimeType") or "").split(";")[0],
            "size": _response_size(resp),
            "has_request_body": bool((req.get("postData") or {}).get("text")),
            "tool_name": name,
            "occurrences": 1,
        }
    return list(seen.values())


def _url_timeline(bundle: dict[str, Any], boundary: str | None) -> list[dict[str, Any]]:
    """Navigations in order, with the login boundary marked.

    Reads the real field names (``url_events`` carry ``from_url``/``to_url``,
    unlike ``click_events`` which carry a flat ``url``) — the exact trap that
    made hand-rolled inspection scripts report empty timelines.
    """
    out: list[dict[str, Any]] = []
    # Collapse "no boundary" (None) and an empty boundary into one falsy string, so
    # the comparisons below are plain str-to-str and an empty boundary can't mark
    # every navigation as post-login.
    bound = boundary or ""
    for ev in bundle.get("url_events") or []:
        if not isinstance(ev, dict) or not ev.get("to_url"):
            continue
        ts = ev.get("timestamp") or ""
        out.append(
            {
                "timestamp": ts,
                "from_url": ev.get("from_url") or "",
                "to_url": ev["to_url"],
                "is_login_boundary": bool(bound) and ts == bound,
                "post_login": bool(bound) and bool(ts) and ts > bound,
            }
        )
    return out


def _credential_events(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Credential-field interactions by role. Counts and redaction only, never values."""
    roles: dict[str, dict[str, int]] = {}
    for ev in bundle.get("click_events") or []:
        if not isinstance(ev, dict):
            continue
        role = ev.get("field_role")
        if role not in CREDENTIAL_FIELD_ROLES:
            continue
        bucket = roles.setdefault(str(role), {"count": 0, "redacted": 0})
        bucket["count"] += 1
        if ev.get("is_redacted"):
            bucket["redacted"] += 1
    return [{"field_role": r, **counts} for r, counts in sorted(roles.items())]


def summarize(
    bundle: dict[str, Any], *, ledger_entry: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Everything ``bundle_inspect.py`` prints, as data."""
    signals = bundle_signals(bundle)
    classification = classify_bundle(bundle)
    stamped = bundle.get("recording_mode")
    declared = (ledger_entry or {}).get("declared_mode", "")
    har_entries = ((bundle.get("har") or {}).get("log") or {}).get("entries") or []
    endpoints = _endpoints(bundle)

    return {
        "session_id": bundle.get("session_id", ""),
        "started_at": bundle.get("started_at", ""),
        "stopped_at": bundle.get("stopped_at", ""),
        "mode": {
            "tabby_reported": stamped,
            "noui_classification": classification,
            "ledger_declared": declared,
            # Tabby's stamp is expected to disagree for combined and warm-pool
            # captures; worth surfacing, never worth trusting.
            "tabby_disagrees": bool(stamped) and stamped != classification,
            "ledger_disagrees": bool(declared) and declared != classification,
        },
        "counts": {
            "har_entries": len(har_entries),
            "api_calls": signals["api_calls_total"],
            "operations": len(endpoints),
            "click_events": len(bundle.get("click_events") or []),
            "url_events": len(bundle.get("url_events") or []),
            "cookies": len(bundle.get("cookies") or []),
        },
        "signals": signals,
        # Why no login boundary was found, when the capture suggests there should
        # have been one (silenced recorder, SSO/passwordless, warm-pool session).
        "missing_login": diagnose_missing_login(bundle),
        "url_timeline": _url_timeline(bundle, signals["login_boundary"]),
        "credential_events": _credential_events(bundle),
        "endpoints": endpoints,
        "unredacted_secrets": count_sensitive_unredacted(bundle),
        "ledger": ledger_entry or None,
    }
