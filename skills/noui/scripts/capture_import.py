#!/usr/bin/env python3
"""Drain a Tabby recording bundle, compile it, and (for logins) register it.

Workflow recording → MCP and/or Skill:
    python scripts/capture_import.py <session_id> --as both --profile-slug <slug>

Login recording → Tabby App Template (per-user auto-provisioning blueprint):
    python scripts/capture_import.py <session_id> --name <app-name>

End to end, no NoUI backend round-trip: Tabby captured the bundle server-side.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401

from noui_core.activate import register
from noui_core.capture import recording
from noui_core.capture.bundle import save_bundle
from noui_core.compile.login import compile_login_bundle
from noui_core.compile.workflow import compile_workflow_bundle


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("session_id", help="Tabby recording session id")
    p.add_argument("--name", default="", help="name for the app/asset")
    p.add_argument("--url", default="", help="(login) login URL; else inferred from URL flow")
    # workflow options
    p.add_argument("--as", dest="target", choices=["mcp", "skill", "both"], default="mcp")
    p.add_argument("--profile-slug", dest="profile_slug", default="")
    p.add_argument(
        "--execution-mode",
        dest="execution_mode",
        choices=["tabby", "http", "harness"],
        default="tabby",
    )
    # login options
    p.add_argument(
        "--auth-mode",
        dest="auth_mode",
        choices=["agent_token", "platform_jwt"],
        default="agent_token",
    )
    p.add_argument(
        "--credential-mode",
        dest="credential_mode",
        choices=["takeover", "manual", "stored", "auto"],
        default="takeover",
        help="(login) takeover (default): manual:, human logs in via VNC + clicks "
        "'Mark as Resolved' (single confirm step); manual: per-field request_human_input "
        "(Slack/MCP-delivered values); stored: k8s:secret username/password (explicit "
        "opt-in — never the default); auto: same as manual (no stored secret)",
    )
    # (login) registration is always template-first — we create a tenant-wide App
    # Template and let Tabby auto-provision a private per-user App+Profile on each
    # member's first request. No direct App/Profile creation, no promote step.
    p.add_argument(
        "--tenant-id",
        dest="tenant_id",
        default="",
        help="(login) Admin-only: register App/Profile/Template in this tenant "
        "(default: the agent token's own tenant, so the agent can drive them)",
    )
    p.add_argument(
        "--post-login-url-pattern",
        dest="post_login_url_pattern",
        default="",
        help="(login/takeover) glob the LOGGED-IN url matches but the login page does NOT "
        "(e.g. '**/lightning/**'). Enables auto-resolve: reaching it completes login with no "
        "'Mark as Resolved' click. Needed for same-origin apps where it can't be auto-derived.",
    )
    args = p.parse_args()

    print(f"Fetching recording bundle from Tabby ({args.session_id}) …", file=sys.stderr)
    try:
        session_type, bundle = recording.fetch_bundle(args.session_id)
    except (RuntimeError, ValueError) as exc:
        print(f"Fetch failed: {exc}", file=sys.stderr)
        return 1

    # ALWAYS persist the drained bundle — recording bundles expire server-side
    # (Tabby TTL) and are the source for generalizing/regenerating the asset later.
    bundle_path = save_bundle(bundle, args.name or args.session_id)
    print(f"Saved capture bundle → {bundle_path}", file=sys.stderr)

    if session_type == "workflow":
        try:
            result = compile_workflow_bundle(
                session_id=args.session_id,
                bundle=bundle,
                name=args.name,
                target=args.target,
                profile_slug=args.profile_slug,
                execution_mode=args.execution_mode,
                start_url=args.url,
            )
        except Exception as exc:  # noqa: BLE001 — surface any compile failure
            print(f"Workflow compile failed: {exc}", file=sys.stderr)
            return 1
        mcp = result.get("mcp") or {}
        skill = result.get("skill") or {}
        if mcp:
            print(f"MCP server: {mcp.get('server_id', '?')} ({len(mcp.get('tools', []))} tool(s))")
        if skill:
            print(f"Skill: {skill.get('skill_id', '?')} ({len(skill.get('operations', []))} op(s))")
        if not args.profile_slug:
            print("No --profile-slug: tools run unauthenticated.", file=sys.stderr)
        scope_ext = result.get("scope_extension")
        if scope_ext:
            print(f"Login profile scope extension: {scope_ext}", file=sys.stderr)
        return 0

    # login
    manual_takeover = args.credential_mode == "takeover"
    manual_credentials = {"takeover": True, "manual": True, "stored": False, "auto": None}[
        args.credential_mode
    ]
    try:
        compiled = compile_login_bundle(
            session_id=args.session_id,
            bundle=bundle,
            name=args.name,
            login_url=args.url,
            auth_mode=args.auth_mode,
            manual_credentials=manual_credentials,
            manual_takeover=manual_takeover,
            post_login_url_pattern=args.post_login_url_pattern,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Login compile failed: {exc}", file=sys.stderr)
        return 1

    # Default to the agent token's tenant so the registered App/Profile/Template
    # land where the agent token can resolve + drive them (avoids tenant mismatch).
    tenant_id = args.tenant_id
    if not tenant_id:
        from noui_core import auth

        tenant_id = auth.tenant_id_from_token(recording.resolve_agent_token())

    try:
        prov = register.register_login(compiled, tenant_id=tenant_id)
    except RuntimeError as exc:
        print(f"Register failed: {exc}", file=sys.stderr)
        # Still emit the compiled drafts so the user can register manually.
        print(json.dumps({"compiled": compiled.get("service_profile_draft", {})}, indent=2))
        return 1

    print("Registered App Template:")
    print(json.dumps(prov, indent=2))
    print(
        f"Profile slug '{prov.get('profile_id', '')}' is now tenant-wide. Tabby "
        "auto-provisions a private per-user profile (→ ACTIVE) on each member's "
        "first request — no promote needed.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
