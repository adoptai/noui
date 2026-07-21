#!/usr/bin/env python3
"""Provision a Tabby VNC recording session and print the viewer URL.

    python scripts/capture_record.py --mode workflow --url https://example.com
    python scripts/capture_record.py --mode login --url https://example.com/login --name example
    python scripts/capture_record.py --mode workflow --from <login-session-id>

Open the printed VNC URL, drive the browser, click "Finish & export", then:
    python scripts/capture_import.py <session_id>

--mode login with --name set checks Tabby for an existing App Template with a
similar name and the same URL first; if one is found, the capture is skipped
and a command to reuse it for the workflow is printed instead (--force to
bypass).
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401  (sys.path side effect)

from noui_core.capture import recording
from noui_core.capture.template_match import find_similar_templates


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mode",
        choices=["login", "workflow", "combined"],
        default="workflow",
        help="login: record a login only. workflow: record a workflow only (seed auth "
        "via --profile/--from). combined: ONE session capturing both — sign in, then "
        "drive the workflow, and import with `capture_import.py --combined` (NoUI splits "
        "it into a login App Template + a workflow asset; Tabby records it as 'login').",
    )
    p.add_argument("--url", default="", help="login/start URL to open")
    p.add_argument(
        "--auth-type",
        dest="auth_type",
        choices=["session", "api-key"],
        default="session",
        help="how the app authenticates. session (default): record a login/session as "
        "usual. api-key: the app uses a static API key sent on every request — NO login "
        "recording is needed; record only the workflow and compile it with "
        "--auth-type api-key (this flag just prints those next steps and exits).",
    )
    p.add_argument(
        "--api-key-header",
        dest="api_key_header",
        default="",
        help="(--auth-type api-key) auth header carrying the key (default: Authorization)",
    )
    p.add_argument(
        "--name",
        default="",
        help="(login) name of the app/site being captured — used to check Tabby for an "
        "existing App Template with a similar name and the same URL before recording, "
        "so a login already covered by a template isn't captured again",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="(login) record anyway even if a matching App Template is found",
    )
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

    # Static API-key app: there is no session to record. Skip the login capture
    # entirely and tell the operator to record just the workflow, then declare
    # the auth model at compile time. The key value goes into the harness secret
    # store (an admin step) — it is never recorded or held by NoUI.
    if args.auth_type == "api-key":
        header_flag = f" --api-key-header {args.api_key_header}" if args.api_key_header else ""
        print(
            "Static API-key app — no login recording needed. Do this instead:",
            file=sys.stderr,
        )
        print(
            "  1. Record the workflow you want as a tool:\n"
            "       python scripts/capture_record.py --mode workflow --url <workflow-url>\n"
            "  2. Compile it, declaring the static-key auth model:\n"
            f"       python scripts/capture_import.py <session_id> --as skill "
            f"--execution-mode harness --auth-type api-key{header_flag}\n"
            "  3. An admin registers the key in the harness secret store "
            "(AGENT_HARNESS_WEB_API_SECRETS) under the ${SECRET:...} name the compile prints.",
            file=sys.stderr,
        )
        return 0

    if args.mode == "login" and args.name and args.url and not args.force:
        try:
            token = recording.resolve_agent_token()
            matches = find_similar_templates(args.name, args.url, token)
        except RuntimeError:
            matches = []
        if matches:
            best = matches[0]
            slug = best.get("profile_name_pattern", "")
            print(
                f"An existing App Template looks like a match for {args.name!r} at {args.url}:",
                file=sys.stderr,
            )
            print(f"  name  : {best.get('name')}", file=sys.stderr)
            print(f"  slug  : {slug}", file=sys.stderr)
            print(f"  id    : {best.get('id')}", file=sys.stderr)
            print(file=sys.stderr)
            print(
                "Skipping the login capture — reuse this profile for the workflow instead:",
                file=sys.stderr,
            )
            print(
                f"  python scripts/capture_record.py --mode workflow --url <workflow-url> "
                f"--profile {slug}",
                file=sys.stderr,
            )
            print("(pass --force to record the login anyway)", file=sys.stderr)
            return 0

    # provision_live_link verifies the session can serve a viewer and refreshes a
    # stale one (restart, else re-provision) before returning — so login_url is
    # always a live, redaction-safe short link (.../s/<id> → ?mode=recording, the
    # "Finish & export" viewer), never a dead raw vnc_url.
    # A combined capture is provisioned as a normal 'login' session — Tabby's
    # recording_mode is behaviorally inert, so one session records login + workflow
    # in one HAR; NoUI does the login/workflow split at import (--combined).
    tabby_mode = "login" if args.mode == "combined" else args.mode
    try:
        result = recording.provision_live_link(
            tabby_mode, args.url, profile=args.profile, from_session=args.from_session
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Provisioning failed: {exc}", file=sys.stderr)
        return 1

    session_id = result.get("session_id", "")
    login_url = result.get("login_url", "")
    if result.get("refreshed"):
        print(
            f"(note: initial session was stale; refreshed via {result['refreshed']})",
            file=sys.stderr,
        )

    print(f"Recording session ready ({args.mode}):")
    print(f"  session_id : {session_id}")
    print(f"  login_url  : {login_url}    <-- open THIS (recording viewer, redaction-safe)")
    print()
    print("If the viewer shows 'Disconnected' at first, the browser is still starting —")
    print("it connects on its own within ~30-60s (no need to re-provision).")
    if args.mode == "combined":
        print(
            "Open the login_url, SIGN IN, then keep going and DRIVE THE WORKFLOW you want "
            "as a tool (run the search / open the report), click 'Finish & export', then:"
        )
        print(f"  python scripts/capture_import.py {session_id} --combined --as skill --name <app>")
    else:
        print("Open the login_url, sign in, drive the flow, click 'Finish & export', then run:")
        print(f"  python scripts/capture_import.py {session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
