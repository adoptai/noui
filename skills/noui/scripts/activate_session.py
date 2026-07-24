#!/usr/bin/env python3
"""Bring a profile's own Tabby session up; print the sign-in link if it needs one.

    python scripts/activate_session.py <profile-slug>

Run this after registering a login App Template (or any time a live call comes
back `login_required`). The profile's session is a DIFFERENT browser from the
recording sessions the human drove during capture — Tabby auto-provisions it per
user, with nothing stored — so it needs one interactive sign-in before the first
live call. `capture_import.py --activate-session` does this for you at import
time; this script is for doing it later, or again.

Exit codes: 0 = resolved (healthy, or a sign-in link is printed), 1 = the session
is terminal or still provisioning.
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401

from noui_core.activate.session import ensure_session, format_activation_notice


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("profile_slug", help="Tabby profile slug (e.g. the slug capture_import printed)")
    p.add_argument(
        "--wait-seconds",
        type=int,
        default=45,
        help="how long to wait for the session to leave STARTING (default 45)",
    )
    args = p.parse_args()

    try:
        result = ensure_session(args.profile_slug, wait_seconds=args.wait_seconds)
    except RuntimeError as exc:
        print(f"Could not check profile '{args.profile_slug}': {exc}", file=sys.stderr)
        return 1

    print(format_activation_notice(result))
    return 0 if result["needs_login"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
