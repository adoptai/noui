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
from noui_core.capture.split import split_bundle
from noui_core.compile.login import compile_login_bundle
from noui_core.compile.workflow import compile_workflow_bundle


def _credential_flags(credential_mode: str) -> tuple[bool, bool | None]:
    manual_takeover = credential_mode == "takeover"
    manual_credentials = {"takeover": True, "manual": True, "stored": False, "auto": None}[
        credential_mode
    ]
    return manual_takeover, manual_credentials


def _default_tenant(tenant_id: str) -> str:
    """The explicit tenant, else the agent token's own tenant (so it can drive them)."""
    if tenant_id:
        return tenant_id
    from noui_core import auth

    return auth.tenant_id_from_token(recording.resolve_agent_token())


def _report_workflow(result: dict, args: argparse.Namespace) -> int:
    mcp = result.get("mcp") or {}
    skill = result.get("skill") or {}
    if mcp:
        print(f"MCP server: {mcp.get('server_id', '?')} ({len(mcp.get('tools', []))} tool(s))")
    if skill:
        print(f"Skill: {skill.get('skill_id', '?')} ({len(skill.get('operations', []))} op(s))")
    if args.auth_type == "api-key":
        secrets = (skill.get("secrets_required") if skill else None) or (
            mcp.get("secrets_required") if mcp else None
        )
        target = f"secret(s) {', '.join(secrets)}" if secrets else "the API-key secret"
        print(
            f"Static API-key mode: an admin must register {target} in the harness "
            "secret store (AGENT_HARNESS_WEB_API_SECRETS) — the compiled asset "
            "carries only a ${SECRET:...} placeholder, never the key.",
            file=sys.stderr,
        )
    scope_ext = result.get("scope_extension")
    if scope_ext:
        print(f"Login profile scope extension: {scope_ext}", file=sys.stderr)
    return 0


def _run_combined(args: argparse.Namespace, bundle: dict) -> int:
    """One capture → both a registered login App Template and a workflow asset.

    Splits the bundle at the login boundary, registers the login half, then
    compiles the workflow half (auth_type=session) bound to the new profile,
    feeding the login's own declared headers straight in (no Tabby round-trip).
    Falls back to workflow-only when the capture has no login segment.
    """
    parts = split_bundle(bundle)
    if parts is None:
        print(
            "No login segment detected — compiling workflow-only. Pass --profile-slug "
            "if the capture used an existing profile's auth.",
            file=sys.stderr,
        )
        try:
            result = compile_workflow_bundle(
                session_id=args.session_id,
                bundle=bundle,
                name=args.name,
                target=args.target,
                profile_slug=args.profile_slug,
                execution_mode=args.execution_mode,
                auth_type=args.auth_type,
                api_key_header=args.api_key_header,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Workflow compile failed: {exc}", file=sys.stderr)
            return 1
        return _report_workflow(result, args)

    login_bundle, workflow_bundle = parts
    manual_takeover, manual_credentials = _credential_flags(args.credential_mode)
    try:
        compiled = compile_login_bundle(
            session_id=args.session_id,
            bundle=login_bundle,
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
    try:
        prov = register.register_login(compiled, tenant_id=_default_tenant(args.tenant_id))
    except RuntimeError as exc:
        print(f"Login register failed: {exc}", file=sys.stderr)
        print(json.dumps({"compiled": compiled.get("service_profile_draft", {})}, indent=2))
        return 1

    profile_slug = prov.get("profile_id", "")
    print(f"Registered login App Template → profile slug '{profile_slug}'.", file=sys.stderr)

    # The login compile ran on the login SLICE (not the workflow hosts), so its
    # target_urls won't cover the workflow's hosts — passing its declared headers
    # through lets compile_workflow_bundle widen the profile's scope for them
    # (extend_login_scope_for_workflow), no Tabby round-trip needed to discover them.
    login_headers = (
        (compiled.get("service_profile_draft") or {}).get("credential_types") or {}
    ).get("headers") or []
    try:
        result = compile_workflow_bundle(
            session_id=args.session_id,
            bundle=workflow_bundle,
            name=args.name,
            target=args.target,
            profile_slug=profile_slug,
            execution_mode=args.execution_mode,
            auth_type="session",
            login_credential_headers=login_headers,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Workflow compile failed: {exc}", file=sys.stderr)
        return 1
    rc = _report_workflow(result, args)
    print(
        f"Combined import complete: login profile '{profile_slug}' + workflow asset "
        "from a single capture.",
        file=sys.stderr,
    )
    return rc


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
    p.add_argument(
        "--auth-type",
        dest="auth_type",
        choices=["session", "api-key", "auto"],
        default="session",
        help="(workflow) how the app authenticates — declared, not guessed. "
        "session (default): a login/session was recorded → tabby_credentials, so a "
        "separately-recorded login is never miscategorised as a static API key. "
        "api-key: no login recorded; the app uses a static key sent on every request "
        "→ static_secret_header (see --api-key-header); the admin registers the value "
        "in the harness secret store. auto: legacy HAR heuristic.",
    )
    p.add_argument(
        "--api-key-header",
        dest="api_key_header",
        default="",
        help="(workflow, --auth-type api-key) auth header carrying the key "
        "(default: Authorization). Emitted as a ${SECRET:name} placeholder.",
    )
    p.add_argument(
        "--combined",
        action="store_true",
        help="one recording that captured BOTH the login and the workflow: split it "
        "at the login boundary, register the login App Template, then compile the "
        "workflow (--auth-type session) bound to that profile. Uses the login options "
        "below for the login half. If no login segment is found, compiles workflow-only.",
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

    # One capture holding both login and workflow → split and do both.
    if args.combined:
        return _run_combined(args, bundle)

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
                auth_type=args.auth_type,
                api_key_header=args.api_key_header,
            )
        except Exception as exc:  # noqa: BLE001 — surface any compile failure
            print(f"Workflow compile failed: {exc}", file=sys.stderr)
            return 1
        if args.auth_type != "api-key" and not args.profile_slug:
            print("No --profile-slug: tools run unauthenticated.", file=sys.stderr)
        return _report_workflow(result, args)

    # login
    manual_takeover, manual_credentials = _credential_flags(args.credential_mode)
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
    try:
        prov = register.register_login(compiled, tenant_id=_default_tenant(args.tenant_id))
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
