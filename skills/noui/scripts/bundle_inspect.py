#!/usr/bin/env python3
"""Summarise a capture bundle — what it holds, and what compile would make of it.

    python scripts/bundle_inspect.py workbench/bundles/<name>.json
    python scripts/bundle_inspect.py --session <session_id>     # drain from Tabby first
    python scripts/bundle_inspect.py <bundle.json> --json       # machine-readable

Use it whenever a compile produced something unexpected. The mode block shows
Tabby's stamp, NoUI's own classification and the mode the session was
provisioned as, side by side — a mismatch there explains most "why did my
workflow become an App Template?" surprises. The endpoint table is built with
the compiler's own filter and naming, so it previews the operation set compile
will emit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from noui_core.capture import ledger, recording
from noui_core.capture.bundle import save_bundle
from noui_core.capture.inspect import summarize

_MAX_URL = 78
_MAX_PATH = 52


def _short(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _render_mode(summary: dict) -> list[str]:
    mode = summary["mode"]
    lines = [
        "",
        "MODE",
        f"  recording_mode (Tabby, IGNORED) : {mode['tabby_reported'] or '—'}",
        f"  NoUI classification            : {mode['noui_classification']}",
        f"  ledger (declared at provision) : {mode['ledger_declared'] or '— (no ledger entry)'}",
    ]
    if mode["tabby_disagrees"]:
        lines += [
            f"  ⚠ Tabby reports '{mode['tabby_reported']}' but the content says "
            f"'{mode['noui_classification']}'. That field is known-unreliable — warm-pool",
            "    recording sessions always report 'login' — and NoUI does not route on it.",
        ]
    if mode["ledger_disagrees"]:
        lines += [
            f"  ⚠ This session was provisioned as '{mode['ledger_declared']}' but its content "
            f"looks like '{mode['noui_classification']}'.",
            f"    capture_import.py will honour the ledger ('{mode['ledger_declared']}'); pass "
            f"--mode {mode['noui_classification']} to override.",
        ]
    return lines


def _render_timeline(summary: dict) -> list[str]:
    timeline = summary["url_timeline"]
    if not timeline:
        return ["", "URL TIMELINE", "  (no url_events recorded)"]
    lines = ["", f"URL TIMELINE ({len(timeline)} navigation(s))"]
    for ev in timeline:
        marker = "  "
        if ev["is_login_boundary"]:
            marker = "→ "  # login completes here; everything after is workflow
        elif ev["post_login"]:
            marker = "· "
        lines.append(f"{marker}{_short(ev['from_url'] or '(start)', _MAX_URL)}")
        lines.append(f"    → {_short(ev['to_url'], _MAX_URL)}")
        if ev["is_login_boundary"]:
            lines.append("    ^^ login boundary (login slice ends here)")
    return lines


def _render_credentials(summary: dict) -> list[str]:
    creds = summary["credential_events"]
    if not creds:
        lines = ["", "CREDENTIAL INTERACTIONS", "  none — no login segment in this capture"]
        # "none" is ambiguous on its own: it reads as "no login happened" when it
        # can equally mean "the login happened and we could not see it".
        diagnosis = summary.get("missing_login")
        if diagnosis:
            lines += [f"  ⚠ {diagnosis['detail']}"]
            if diagnosis["login_urls"]:
                lines += [f"    sign-in URL(s) in the timeline: {diagnosis['login_urls'][0]}"]
            lines += [
                "    → this capture can still register an App Template: re-import it with",
                "      --mode login (no re-recording needed).",
            ]
        return lines
    lines = ["", "CREDENTIAL INTERACTIONS (roles + redaction only, never values)"]
    for c in creds:
        lines.append(f"  {c['field_role']:<20} {c['count']} event(s), {c['redacted']} redacted")
    return lines


def _render_endpoints(summary: dict) -> list[str]:
    endpoints = summary["endpoints"]
    counts = summary["counts"]
    lines = [
        "",
        f"API ENDPOINTS — {counts['operations']} operation(s) from {counts['api_calls']} API "
        f"call(s) of {counts['har_entries']} HAR entries",
        "  (compile's own filter, dedup and naming — this is the operation set it will emit)",
        "",
        f"  {'METHOD':<7} {'PATH':<{_MAX_PATH}} {'STATUS':>6} {'SIZE':>8}  {'×':>3}  TOOL",
    ]
    if not endpoints:
        lines.append("  (none — nothing here looks like an API call)")
        return lines
    for e in endpoints:
        body = "*" if e["has_request_body"] else " "
        lines.append(
            f"  {e['method']:<7}{body}{_short(e['path_template'], _MAX_PATH):<{_MAX_PATH}} "
            f"{e['status']:>6} {e['size']:>8}  {e['occurrences']:>3}  {e['tool_name']}"
        )
    hosts = sorted({e["host"] for e in endpoints})
    lines += ["", f"  hosts: {', '.join(hosts)}", "  (* = request carries a body)"]
    return lines


def render(summary: dict) -> str:
    counts = summary["counts"]
    lines = [
        f"BUNDLE {summary['session_id'] or '(no session_id)'}",
        f"  captured : {summary['started_at'] or '?'} → {summary['stopped_at'] or '?'}",
        f"  contents : {counts['har_entries']} HAR entries, {counts['click_events']} clicks, "
        f"{counts['url_events']} url events, {counts['cookies']} cookies",
    ]
    lines += _render_mode(summary)
    lines += _render_timeline(summary)
    lines += _render_credentials(summary)
    lines += _render_endpoints(summary)
    if summary["unredacted_secrets"]:
        lines += [
            "",
            f"⚠ {summary['unredacted_secrets']} password/OTP value(s) are NOT redacted — "
            "capture_import will refuse this bundle.",
        ]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bundle", nargs="?", default="", help="path to a saved bundle JSON")
    p.add_argument(
        "--session",
        default="",
        help="drain this recording session from Tabby and inspect it (also saves the bundle)",
    )
    p.add_argument("--json", action="store_true", help="emit the summary as JSON")
    args = p.parse_args()

    if not args.bundle and not args.session:
        p.error("give a bundle path or --session <session_id>")

    session_id = args.session
    if args.session:
        print(f"Fetching recording bundle from Tabby ({args.session}) …", file=sys.stderr)
        try:
            _, bundle = recording.fetch_bundle(args.session)
        except (RuntimeError, ValueError) as exc:
            print(f"Fetch failed: {exc}", file=sys.stderr)
            return 1
        print(f"Saved capture bundle → {save_bundle(bundle, args.session)}", file=sys.stderr)
    else:
        path = Path(args.bundle)
        try:
            bundle = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            print(f"Could not read bundle {path}: {exc}", file=sys.stderr)
            return 1
        if not isinstance(bundle, dict):
            print(f"{path} does not contain a bundle object", file=sys.stderr)
            return 1
        session_id = str(bundle.get("session_id") or "")

    summary = summarize(bundle, ledger_entry=ledger.lookup(session_id))
    print(json.dumps(summary, indent=2) if args.json else render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
