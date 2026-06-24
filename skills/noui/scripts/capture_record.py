#!/usr/bin/env python3
"""Provision a Tabby VNC recording session and print the viewer URL.

    python scripts/capture_record.py --mode workflow --url https://example.com
    python scripts/capture_record.py --mode login --url https://example.com/login
    python scripts/capture_record.py --mode workflow --from <login-session-id>

Open the printed VNC URL, drive the browser, click "Finish & export", then:
    python scripts/capture_import.py <session_id>
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401  (sys.path side effect)

from noui_core.capture import recording


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["login", "workflow"], default="workflow")
    p.add_argument("--url", default="", help="login/start URL to open")
    p.add_argument("--profile", default="", help="(reserved) existing Tabby profile id")
    p.add_argument(
        "--from",
        dest="from_session",
        default="",
        help="seed cookies from a prior login recording (its session id)",
    )
    args = p.parse_args()

    try:
        result = recording.start(
            args.mode, args.url, profile=args.profile, from_session=args.from_session
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Provisioning failed: {exc}", file=sys.stderr)
        return 1

    session_id = result.get("session_id", "")
    vnc_url = result.get("vnc_url", "")
    # Surface a short, redaction-safe login URL in RECORDING mode (.../s/<id> →
    # ?mode=recording, the viewer with the "Finish & export" toolbar). Unlike the raw
    # vnc_url (whose #token= JWT the harness secret-redactor strips), the short code
    # survives redaction. Requires Tabby's mode-aware short-link endpoint.
    login_url = ""
    try:
        login_url = recording.short_link(session_id, mode="recording")
    except Exception as exc:  # best-effort; fall back to the raw vnc_url
        print(f"(warning: could not mint short login link: {exc})", file=sys.stderr)

    print(f"Recording session ready ({args.mode}):")
    print(f"  session_id : {session_id}")
    if login_url:
        print(f"  login_url  : {login_url}    <-- give the user THIS (recording viewer, redaction-safe)")
    else:
        print(f"  vnc_url    : {vnc_url}    <-- fallback (raw link; may be redacted in the harness)")
    print()
    print("Have the user open the login_url, sign in, drive the flow, click 'Finish & export', then run:")
    print(f"  python scripts/capture_import.py {session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
