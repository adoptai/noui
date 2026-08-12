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
from noui_core.capture.split import SplitError, split_bundle, split_diagnosis
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


def _report_kind(result: dict, skill: dict | None, *, declared: bool) -> None:
    """Say which KIND was compiled, why, and ask when nobody chose it.

    This used to speak only when browser mode was auto-selected, so choosing
    REPLAY was silent -- and an ICICI capture asked for as a browser skill
    compiled to two Finacle POSTs with nothing anywhere saying a decision had
    been taken. A default is fine; a default nobody is told about is not.

    Auto-detection remains the default answer. What changes is that the answer
    is stated, with its basis, and when the member never said which kind they
    wanted the agent is told to confirm before installing -- the kind decides
    whether the skill drives the page or replays requests, and changing it means
    compiling again.
    """
    det = result.get("browser_detection") or {}
    app = (skill.get("skill_id") if skill else None) or "this app"
    browser = bool(det.get("unreplayable")) or declared

    if browser:
        from noui_core.compile.unreplayable import recommendation_message

        basis = "you asked for it" if declared else recommendation_message(det, app_name=app)
        print(f"KIND: browser-driven — {basis}", file=sys.stderr)
    else:
        reasons = "; ".join(det.get("reasons") or []) or "no unreplayable fingerprint in the HAR"
        print(
            f"KIND: replay (call_web_api) — auto-detected: {reasons}. The skill will "
            f"fire the recorded requests rather than drive the page.",
            file=sys.stderr,
        )

    if not declared:
        print(
            "CONFIRM THE KIND WITH THE MEMBER BEFORE INSTALLING. Nobody chose this; "
            "it was detected. If they asked for a browser-driven skill -- or the app "
            "encrypts or signs its requests in the page, so replay will 403 later -- "
            "re-import with --browser-driven. Changing it afterwards means compiling "
            "again, and a replay skill that looks fine today fails the first time the "
            "app rotates what it signs.",
            file=sys.stderr,
        )


def _report_workflow(result: dict, args: argparse.Namespace) -> int:
    mcp = result.get("mcp") or {}
    skill = result.get("skill") or {}
    if mcp:
        print(f"MCP server: {mcp.get('server_id', '?')} ({len(mcp.get('tools', []))} tool(s))")
    if skill:
        print(f"Skill: {skill.get('skill_id', '?')} ({len(skill.get('operations', []))} op(s))")
    # Tell the operator when the app was auto-routed to browser mode, and why —
    # this is the recommendation surfaced to a user authoring a skill in the
    # harness, so browser mode is never picked silently.
    _report_kind(result, skill, declared=bool(getattr(args, "browser_driven", False)))
    if not _apply_folds(skill, getattr(args, "fold", []) or []):
        _report_folds(skill)
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


def _resolve_keepalive_style(args: argparse.Namespace, workflow_bundle: dict) -> str:
    """Pick the App Template's keepalive style for the login being registered.

    Explicit --keepalive wins. Otherwise: a browser-driven workflow (reads the
    DOM, expires/refresh-sensitive like a bank SPA) gets the human-like "activity"
    nudge; a HAR-replay workflow keeps "goto" so its captured request headers stay
    fresh. Mirrors how compile_workflow_bundle decides browser vs replay.
    """
    ka = getattr(args, "keepalive", "auto")
    if getattr(args, "browser_driven", False):
        # Explicit --browser-driven decides the SKILL KIND, and a browser skill
        # must never get a reload-on-interval goto keepalive. Checked before the
        # explicit --keepalive value because _run_combined derives the skill kind
        # from this function's answer: with the old order,
        # `--browser-driven --keepalive goto` silently compiled a HAR-REPLAY
        # skill, ignoring the flag the caller actually passed.
        return "activity"
    if ka in ("goto", "activity"):
        return ka
    if getattr(args, "auto_detect_browser", True):
        try:
            from noui_core.compile.login_assets import _url_origin
            from noui_core.compile.unreplayable import detect_unreplayable

            urls = workflow_bundle.get("url_events", []) or []
            first = args.url or next((u.get("to_url", "") for u in urls if u.get("to_url")), "")
            origin = _url_origin(first) if first else ""
            if detect_unreplayable(workflow_bundle.get("har"), app_origin=origin).get(
                "unreplayable"
            ):
                return "activity"
        except Exception:  # noqa: BLE001 — detection is best-effort; default to goto
            pass
    return "goto"


def _run_combined(args: argparse.Namespace, bundle: dict) -> int:
    """One capture → both a registered login App Template and a workflow asset.

    Splits the bundle at the login boundary, registers the login half, then
    compiles the workflow half (auth_type=session) bound to the new profile,
    feeding the login's own declared headers straight in (no Tabby round-trip).
    Falls back to workflow-only when the capture has no login segment.
    """
    try:
        parts = split_bundle(bundle)
    except SplitError as exc:
        print(split_diagnosis(bundle), file=sys.stderr)
        print(f"Cannot split this capture: {exc}", file=sys.stderr)
        return 1
    # Always say what the splitter saw. The decision turns on one thing and used
    # to be invisible, so a member who HAD recorded a login was asked to record
    # it again with nothing anywhere explaining why.
    print(split_diagnosis(bundle), file=sys.stderr)
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
                browser_driven=getattr(args, "browser_driven", False),
                auto_detect_browser=getattr(args, "auto_detect_browser", True),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Workflow compile failed: {exc}", file=sys.stderr)
            return 1
        return _report_workflow(result, args)

    login_bundle, workflow_bundle = parts
    manual_takeover, manual_credentials = _credential_flags(args.credential_mode)
    keepalive_style = _resolve_keepalive_style(args, workflow_bundle)
    # Single browser-vs-replay decision. _resolve_keepalive_style already folds in
    # --browser-driven, --keepalive, and HAR auto-detect, so treat "activity" as
    # THE authoritative "this is a browser skill" signal and drive BOTH the login
    # app template (keepalive + downloads) AND the workflow skill kind from it.
    # Deciding them separately is what let hsbcnet compile as a browser skill with
    # a "goto" keepalive that reloaded — and duplicate-session-killed — the portal
    # every 120s (DTC_AUTH_PL_1_075).
    is_browser = keepalive_style == "activity"
    print(
        f"Keepalive style: {keepalive_style} (browser-driven={is_browser}).",
        file=sys.stderr,
    )
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
            keepalive_style=keepalive_style,
            enable_downloads=is_browser,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Login compile failed: {exc}", file=sys.stderr)
        return 1
    try:
        prov = register.register_login(compiled, tenant_id=_default_tenant(args.tenant_id))
        profile_slug = prov.get("profile_id", "")
        print(f"Registered login App Template → profile slug '{profile_slug}'.", file=sys.stderr)
    except RuntimeError as exc:
        # An App Template that already exists is not a failure -- it is the
        # SECOND import of the same recording, which is what re-compiling after
        # a compiler fix looks like. Failing here left operations.json stale at
        # the previous compile while the run reported an error about the login
        # half, so the fix appeared not to have worked.
        if "409" not in str(exc) and "already exists" not in str(exc).lower():
            print(f"Login register failed: {exc}", file=sys.stderr)
            print(json.dumps({"compiled": compiled.get("service_profile_draft", {})}, indent=2))
            return 1
        profile_slug = (compiled.get("service_profile_draft") or {}).get("profile_id", "") or (
            args.profile_slug or ""
        )
        print(
            f"Login App Template '{profile_slug}' already exists — keeping it and "
            "compiling the workflow half. The login was recorded once; re-registering "
            "it would only overwrite a profile that is already serving sessions.",
            file=sys.stderr,
        )

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
            # Honor the single browser decision from the keepalive resolver so the
            # skill kind can never disagree with the keepalive style (a browser
            # skill paired with a reload-on-interval goto keepalive).
            browser_driven=is_browser,
            auto_detect_browser=False,
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


def _apply_folds(skill: dict, specs: list[str]) -> bool:
    """Fold named pairs in the written skill. True when anything was folded.

    Rewrites operations.json AND re-stamps the manifest's steps digest: folding
    changes the steps, and a skill whose digest no longer matches its manifest
    cannot install however well it replays. The compiler is doing this fold, so
    re-stamping is the honest record -- these are still exactly the recorded
    steps, in the recorded order, with a condition naming which variant each
    belongs to.
    """
    if not specs:
        return False
    from pathlib import Path

    from noui_core.compile import provenance
    from noui_core.config import settings
    from noui_core.compile.browser_skill import apply_fold, fold_candidates

    skill_dir = Path(settings.workbench_dir) / "skills" / str(skill.get("skill_id") or "")
    ops_path, man_path = skill_dir / "operations.json", skill_dir / "manifest.json"
    try:
        doc = json.loads(ops_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Cannot fold: {ops_path} is unreadable ({exc}).", file=sys.stderr)
        return False

    operations = doc.get("operations") or []
    for raw in specs:
        try:
            spec = json.loads(raw)
        except ValueError as exc:
            print(f"--fold expects a JSON object, got {raw[:60]!r}: {exc}", file=sys.stderr)
            return False
        wanted = [str(n) for n in (spec.get("operations") or [])]
        match = next(
            (f for f in fold_candidates(operations) if list(f["operations"]) == wanted), None
        )
        if match is None:
            print(
                f"{' and '.join(wanted) or 'those operations'} are not a foldable pair. "
                "Run the import without --fold to see which are, in the order the "
                "report names them.",
                file=sys.stderr,
            )
            return False
        values = [str(v) for v in (spec.get("values") or [])]
        if len(values) != 2 or not spec.get("name") or not spec.get("param"):
            print(
                "--fold needs 'name', 'param', and exactly two 'values' -- one per "
                "branch, in the order the report listed them.",
                file=sys.stderr,
            )
            return False
        operations = apply_fold(
            operations, match, name=str(spec["name"]), param=str(spec["param"]), values=values
        )
        print(
            f"Folded {' + '.join(wanted)} -> {spec['name']} "
            f"({spec['param']}: {', '.join(values)}).",
            file=sys.stderr,
        )

    doc["operations"] = operations
    ops_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
        manifest.setdefault("provenance", {})["steps_sha256"] = provenance.steps_digest(operations)
        man_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(
            f"Folded, but could not re-stamp {man_path} ({exc}) -- the skill will be "
            "refused at install until it is recompiled.",
            file=sys.stderr,
        )
    return True


def _report_folds(skill: dict) -> None:
    """Surface operations that are one workflow with a choice in the middle.

    Reported, never applied: naming the choice is a judgement from a person. A
    parameter called "corp_finacle" -- the page an annual statement happened to
    be served from -- is worse than the two operations it replaced, because the
    model reads these names to decide what to call.
    """
    try:
        from noui_core.compile.browser_skill import fold_candidates
    except Exception:  # noqa: BLE001 — a missing fold report never fails an import
        return
    for f in fold_candidates(skill.get("operations") or []):
        a, b = f["operations"]
        print(
            f"\n{a} and {b} are one workflow with a choice in the middle: "
            f"{f['prefix']} identical steps to reach the same page, then they "
            f"diverge, then the same ending.\n"
            f"  As two operations each replays the whole journey, so every call "
            f"has to start from the landing page. As ONE operation with a "
            f"parameter, the caller just picks the variant.\n"
            f"  ASK THE MEMBER what the choice is called and what to call each "
            f"side (e.g. timeframe=monthly|annual). The recorded names say where "
            f"the pages were served from, not what they mean.",
            file=sys.stderr,
        )


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
        "--keepalive",
        dest="keepalive",
        choices=["auto", "goto", "activity"],
        default="auto",
        help=(
            "Keepalive style for the registered login App Template. auto "
            "(default): browser-driven skills get 'activity' (a human-like mouse/"
            "scroll nudge that holds SPA/bank sessions without a reload), "
            "HAR-replay skills get 'goto' (revisit the landing page to keep "
            "captured request headers fresh). Override with 'goto' or 'activity'."
        ),
    )
    p.add_argument(
        "--no-auto-browser",
        dest="auto_detect_browser",
        action="store_false",
        help=(
            "Disable automatic browser-mode detection. By default, if the capture "
            "shows the app encrypts its requests in-page (opaque {data,key} bodies "
            "+ a key-fetch endpoint), the skill is compiled browser-driven because "
            "replay cannot work. This forces the legacy replay compile anyway."
        ),
    )
    p.add_argument(
        "--browser-driven",
        dest="browser_driven",
        action="store_true",
        help=(
            "Emit a browser-driven skill (drives the page via call_web_browser "
            "and reads the rendered DOM) instead of a HAR-replay call_web_api "
            "skill. Use for apps whose requests cannot be replayed — SPAs that "
            "mint per-request encryption or per-session headers in JS (e.g. a "
            "bank wrapping every body in a per-session key). Requires "
            "--profile-slug."
        ),
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
    p.add_argument(
        "--fold",
        action="append",
        default=[],
        metavar="JSON",
        help="fold two operations that are one workflow with a choice into ONE, "
        'as a JSON object: {"operations": ["a", "b"], "name": "download_statement", '
        '"param": "timeframe", "values": ["monthly", "annual"]}. Run the import '
        "once without this to see which pairs are foldable, ASK THE MEMBER what "
        "the choice is called, then re-run. The recorded names say where the "
        "pages were served from, not what they mean.",
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

    # The kind travels with the recording, not with this command line.
    #
    # capture_record took --browser-driven, provisioned with it and never wrote
    # it down, so the decision had to be repeated here. Forget it -- easily
    # done, since nothing in the recording says so -- and an app that must be
    # driven compiles to replayed API calls instead: an ICICI capture asked for
    # as a BROWSER BASED skill shipped as two Finacle POSTs.
    if not getattr(args, "browser_driven", False) and ledger.declared_browser_driven(
        args.session_id
    ):
        args.browser_driven = True
        print(
            "browser-driven: carried from the recording (it was provisioned that way).",
            file=sys.stderr,
        )

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
                browser_driven=getattr(args, "browser_driven", False),
                auto_detect_browser=getattr(args, "auto_detect_browser", True),
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
            # The login-only path honours the same flags as the combined path:
            # without these a login recording for a browser portal always
            # registered goto/120s with downloads off, no matter what the caller
            # passed.
            keepalive_style=_resolve_keepalive_style(args, bundle),
            enable_downloads=_resolve_keepalive_style(args, bundle) == "activity",
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
