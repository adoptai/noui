#!/usr/bin/env python3
"""Drain a Tabby recording bundle, compile it, and (for logins) register it.

Workflow recording → MCP and/or Skill:
    python scripts/capture_import.py <session_id> --as both --profile-slug <slug>

Login recording → Tabby App Template (per-user auto-provisioning blueprint):
    python scripts/capture_import.py <session_id> --name <app-name>

How the login/workflow/combined decision is made: --mode wins, else the mode
capture_record.py recorded when it provisioned the session, else the capture's
content. Tabby's own ``recording_mode`` is never consulted — it is unreliable
(warm-pool sessions always report 'login'). See noui_core.capture.classify.

End to end, no NoUI backend round-trip: Tabby captured the bundle server-side.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401

from noui_core.activate import register
from noui_core.capture import ledger, recording
from noui_core.capture.bundle import save_bundle
from noui_core.capture.classify import COMBINED, LOGIN, WORKFLOW
from noui_core.capture.split import split_bundle
from noui_core.compile.login import compile_login_bundle
from noui_core.compile.workflow import compile_workflow_bundle


def _resolve_mode(args: argparse.Namespace, inferred: str) -> tuple[str, str]:
    """Decide how to treat this capture, and say where the decision came from.

    Precedence — strongest declaration wins, and Tabby's ``recording_mode`` is
    not in the list at all (it is unreliable: a warm-pool session always reports
    ``login`` regardless of what was provisioned, which used to route workflow
    recordings into the login/App-Template path):

      1. ``--mode`` / ``--combined`` — the operator said so explicitly.
      2. the provision ledger — what ``capture_record.py`` actually asked Tabby for.
      3. content classification (``classify_bundle``) — the fallback for captures
         with no ledger entry (older sessions, bundles from another machine).
    """
    if args.mode != "auto":
        return args.mode, "--mode flag"
    if args.combined:
        return COMBINED, "--combined flag"
    declared = ledger.declared_mode(args.session_id)
    if declared:
        return declared, "provision ledger"
    return inferred, "bundle content"


def _report_mode(mode: str, source: str, inferred: str, bundle: dict) -> None:
    """Tell the operator what we decided, and flag any disagreement."""
    print(f"Treating this capture as '{mode}' (source: {source}).", file=sys.stderr)
    stamped = bundle.get("recording_mode")
    if stamped and stamped != mode:
        print(
            f"(Tabby stamped recording_mode='{stamped}' — ignored. That field is "
            "unreliable: warm-pool recording sessions always report 'login'.)",
            file=sys.stderr,
        )
    if inferred != mode:
        hint = {
            COMBINED: "pass --mode combined to split it into a login + a workflow asset",
            LOGIN: "pass --mode login to register it as an App Template",
            WORKFLOW: "pass --mode workflow to compile it as a workflow asset",
        }[inferred]
        print(
            f"(Content looks like '{inferred}' instead — honouring '{mode}'. "
            f"If that's wrong, {hint}.)",
            file=sys.stderr,
        )


def _maybe_activate_session(args: argparse.Namespace, profile_slug: str) -> None:
    """Surface the profile's activation sign-in now, not mid-test.

    The registered template auto-provisions a per-user session on first use, and
    that session — a different browser from the ones just recorded, with nothing
    stored — starts LOGIN_NEEDED. Finding that out during the generalize test loop
    reads as a bug ("I just signed in twice!"); finding out here is a step.
    Never fatal: the import already succeeded.
    """
    if not args.activate_session or not profile_slug:
        return
    from noui_core.activate.session import ensure_session, format_activation_notice

    print(f"Activating the session for profile '{profile_slug}' …", file=sys.stderr)
    try:
        print(format_activation_notice(ensure_session(profile_slug)), file=sys.stderr)
    except RuntimeError as exc:
        print(
            f"(could not check the session for '{profile_slug}': {exc} — run "
            f"`python scripts/activate_session.py {profile_slug}` before testing.)",
            file=sys.stderr,
        )


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
    _maybe_activate_session(args, profile_slug)

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
        "--mode",
        choices=["auto", "login", "workflow", "combined"],
        default="auto",
        help="how to treat this capture. auto (default): use the mode "
        "capture_record.py recorded at provision time, else infer it from the "
        "capture's content. Tabby's own recording_mode is never used — it is "
        "unreliable (warm-pool sessions always report 'login'). Pass an explicit "
        "value to override both.",
    )
    p.add_argument(
        "--combined",
        action="store_true",
        help="(alias for --mode combined) "
        "one recording that captured BOTH the login and the workflow: split it "
        "at the login boundary, register the login App Template, then compile the "
        "workflow (--auth-type session) bound to that profile. Uses the login options "
        "below for the login half. If no login segment is found, compiles workflow-only.",
    )
    p.add_argument(
        "--activate-session",
        dest="activate_session",
        action="store_true",
        help="(login/combined) after registering, bring the profile's OWN per-user "
        "Tabby session up and print its sign-in link if it needs one. That session is "
        "a different browser from the ones just recorded (nothing is stored), so it "
        "needs one interactive sign-in before the first live call — this surfaces it "
        "now instead of during your first test.",
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
        inferred, bundle = recording.fetch_bundle(args.session_id)
    except (RuntimeError, ValueError) as exc:
        print(f"Fetch failed: {exc}", file=sys.stderr)
        return 1

    # ALWAYS persist the drained bundle — recording bundles expire server-side
    # (Tabby TTL) and are the source for generalizing/regenerating the asset later.
    bundle_path = save_bundle(bundle, args.name or args.session_id)
    print(f"Saved capture bundle → {bundle_path}", file=sys.stderr)

    mode, source = _resolve_mode(args, inferred)
    _report_mode(mode, source, inferred, bundle)

    # One capture holding both login and workflow → split and do both.
    if mode == COMBINED:
        return _run_combined(args, bundle)

    if mode == WORKFLOW:
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
    _maybe_activate_session(args, prov.get("profile_id", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
