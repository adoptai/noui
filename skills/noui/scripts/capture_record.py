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
    p.add_argument(
        "--profile",
        default="",
        help="(workflow) record using an EXISTING Tabby profile's auth — skip the "
        "login recording entirely (e.g. --profile adopt-bank). Use this when the "
        "App Template / profile is already set up.",
    )
    p.add_argument(
        "--from",
        dest="from_session",
        default="",
        help="(workflow) seed cookies from a prior LOGIN recording (its session id), "
        "when you just recorded the login in this same flow",
    )
    args = p.parse_args()

    # provision_live_link verifies the session can serve a viewer and refreshes a
    # stale one (restart, else re-provision) before returning — so login_url is
    # always a live, redaction-safe short link (.../s/<id> → ?mode=recording, the
    # "Finish & export" viewer), never a dead raw vnc_url.
    try:
        result = recording.provision_live_link(
            args.mode, args.url, profile=args.profile, from_session=args.from_session
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Provisioning failed: {exc}", file=sys.stderr)
        return 1

    session_id = result.get("session_id", "")
    login_url = result.get("login_url", "")
    if result.get("refreshed"):
        print(f"(note: initial session was stale; refreshed via {result['refreshed']})", file=sys.stderr)

    print(f"Recording session ready ({args.mode}):")
    print(f"  session_id : {session_id}")
    print(f"  login_url  : {login_url}    <-- open THIS (recording viewer, redaction-safe)")
    print()
    print("If the viewer shows 'Disconnected' at first, the browser is still starting —")
    print("it connects on its own within ~30-60s (no need to re-provision).")
    print("Open the login_url, sign in, drive the flow, click 'Finish & export', then run:")
    print(f"  python scripts/capture_import.py {session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
