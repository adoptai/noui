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
    # The raw vnc_url embeds a JWT (#token=...) that the Agent Harness secret-redactor
    # strips, breaking the link. Mint a short redirect URL (.../s/<id>) instead — it is
    # redaction-safe and is what should be surfaced to the user.
    login_url = ""
    try:
        login_url = recording.short_link(session_id)
    except Exception as exc:  # best-effort; fall back to raw vnc_url
        print(f"(warning: could not mint short login link: {exc})", file=sys.stderr)

    print(f"Recording session ready ({args.mode}):")
    print(f"  session_id : {session_id}")
    if login_url:
        print(f"  login_url  : {login_url}")
        print("  (^ give the user THIS link — redaction-safe. The raw vnc_url below")
        print("     contains a token the harness will redact, so do not surface it.)")
    print(f"  vnc_url    : {vnc_url}")
    print()
    print("Give the user login_url, have them sign in + click 'Finish & export', then run:")
    print(f"  python scripts/capture_import.py {session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
