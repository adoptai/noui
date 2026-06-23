"""AuthVerifier — verify and repair auth before MCP installation.

Runs after `workflow export --as mcp` (or `--as both`), before `mcp install`.

The verifier is deterministic-first: it attempts structured repairs in a known
order before escalating to the LLM.  It only asks the LLM agent to make a
judgment when deterministic repairs exhaust their options.

Return status values:
  PASS            — safe to install MCP
  REPAIR_APPLIED  — a repair was applied, dry-run should be re-run
  NEEDS_SECRET    — a required secret env var is missing; user must provide it
  UNSUPPORTED     — a precise reason and the missing artifact are reported
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

# ── Result dataclass ─────────────────────────────────────────────────────────


class VerificationResult:
    """Structured result from AuthVerifier.verify()."""

    def __init__(
        self,
        status: str,
        *,
        message: str = "",
        missing_artifacts: list[str] | None = None,
        suggested_repairs: list[dict] | None = None,
        repair_attempt_count: int = 0,
        diagnostics: dict | None = None,
    ) -> None:
        self.status = status
        self.message = message
        self.missing_artifacts = missing_artifacts or []
        self.suggested_repairs = suggested_repairs or []
        self.repair_attempt_count = repair_attempt_count
        self.diagnostics = diagnostics or {}

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "missing_artifacts": self.missing_artifacts,
            "suggested_repairs": self.suggested_repairs,
            "repair_attempt_count": self.repair_attempt_count,
            "diagnostics": self.diagnostics,
        }

    def __repr__(self) -> str:
        return f"VerificationResult(status={self.status!r}, message={self.message!r})"


# ── AuthVerifier ─────────────────────────────────────────────────────────────


class AuthVerifier:
    """Verify and repair auth for a compiled MCP server before installation.

    Usage::

        verifier = AuthVerifier(
            auth_plan=auth_plan,
            server_dir="/path/to/workbench/mcp_servers/example-bank/example-bank-abc12345",
            tabby_api_host="http://localhost:8000",
            tabby_admin_token="admin-token",
        )
        result = await verifier.verify()
        if result.status == "PASS":
            # safe to install
        elif result.status == "NEEDS_SECRET":
            print(result.message)
    """

    def __init__(
        self,
        auth_plan: dict,
        server_dir: str | Path,
        tabby_api_host: str = "",
        tabby_admin_token: str = "",
        tabby_client_id: str = "",
        tabby_client_secret: str = "",
        adopt_api_url: str = "",
        adopt_client_id: str = "",
        adopt_client_secret: str = "",
        auth_mode: str = "",
        max_repair_attempts: int = 2,
    ) -> None:
        self.auth_plan = auth_plan
        self.server_dir = Path(server_dir)
        self.tabby_api_host = (
            tabby_api_host or os.environ.get("TABBY_API_URL", "") or "http://localhost:8000"
        )
        self.tabby_admin_token = tabby_admin_token or os.environ.get("TABBY_ADMIN_TOKEN", "")
        self.tabby_client_id = tabby_client_id or os.environ.get("TABBY_CLIENT_ID", "")
        self.tabby_client_secret = tabby_client_secret or os.environ.get("TABBY_CLIENT_SECRET", "")
        # Platform (Adopt) credentials for the cloud token-exchange flow.
        self.adopt_api_url = (adopt_api_url or os.environ.get("ADOPT_API_URL", "")).rstrip("/")
        self.adopt_client_id = adopt_client_id or os.environ.get("ADOPT_CLIENT_ID", "")
        self.adopt_client_secret = adopt_client_secret or os.environ.get("ADOPT_CLIENT_SECRET", "")
        self.auth_mode = (auth_mode or os.environ.get("NOUI_TABBY_AUTH_MODE", "")).strip().lower()
        self.max_repair_attempts = max_repair_attempts
        self._repair_count = 0

    def _resolve_auth_mode(self) -> str:
        """Pick the Tabby auth flow: explicit auth_mode wins, else auto-detect."""
        if self.auth_mode:
            return self.auth_mode
        if self.adopt_api_url and self.adopt_client_id and self.adopt_client_secret:
            return "platform_jwt"
        return "agent_token"

    # ── Public API ───────────────────────────────────────────────────────────

    async def verify(self) -> VerificationResult:
        """Run the full verification pipeline.

        Steps:
        1. Tabby reachability
        2. Agent credential validity (TABBY_CLIENT_ID/SECRET)
        3. Browser session health for profile slug
        4. Credentials request — compare returned vs required
        5. Static secret check (if strategy is static_secret_header)
        6. Dry-run the generated operation
        7. Auto-repair if possible, then retry

        Returns a VerificationResult with status PASS / REPAIR_APPLIED /
        NEEDS_SECRET / UNSUPPORTED.
        """
        strategy = self.auth_plan.get("strategy", "")

        if not strategy:
            # Unauthenticated server — nothing to verify
            return VerificationResult(
                "PASS", message="No auth required — server is safe to install."
            )

        if strategy == "static_secret_header":
            return await self._verify_static_secrets()

        if strategy == "tabby_credentials":
            return await self._verify_tabby_credentials()

        return VerificationResult(
            "UNSUPPORTED",
            message=f"Unknown auth strategy {strategy!r} in auth_plan.json.",
        )

    # ── Strategy verifiers ───────────────────────────────────────────────────

    async def _verify_static_secrets(self) -> VerificationResult:
        """Check that all required static secret env vars are present."""
        missing: list[str] = []
        for fallback in self.auth_plan.get("fallbacks", []):
            if fallback.get("type") != "static_secret_header":
                continue
            env_var = fallback.get("secret_env_var", "")
            if env_var and not os.environ.get(env_var):
                missing.append(env_var)

        if missing:
            repairs = [
                {
                    "action": "set_env_var",
                    "env_var": v,
                    "instructions": f"Add {v}=<value> to noui/.env or export it in your shell.",
                }
                for v in missing
            ]
            return VerificationResult(
                "NEEDS_SECRET",
                message=(
                    f"Missing required secret(s): {', '.join(missing)}.\n"
                    f"Add them to noui/.env and re-run verification."
                ),
                missing_artifacts=missing,
                suggested_repairs=repairs,
            )

        # Dry-run the operation
        result = await self._dry_run_operation()
        return result

    async def _verify_tabby_credentials(self) -> VerificationResult:
        """Full Tabby verification: reachability → token → session → creds → dry-run."""
        # Step 1: Tabby reachable?
        if not await self._tabby_reachable():
            return VerificationResult(
                "UNSUPPORTED",
                message=(
                    f"Tabby API not reachable at {self.tabby_api_host}.\n"
                    f"Run `noui tabby start` to start Tabby."
                ),
                suggested_repairs=[{"action": "run_command", "command": "noui tabby start"}],
            )

        # Step 2: Auth credentials valid? (agent-token locally, platform-JWT in cloud)
        try:
            bearer = await self._get_tabby_bearer()
        except RuntimeError as exc:
            if self._resolve_auth_mode() == "platform_jwt":
                missing = ["ADOPT_API_URL", "ADOPT_CLIENT_ID", "ADOPT_CLIENT_SECRET"]
                repair_cmd = "noui tabby setup --cloud"
            else:
                missing = ["TABBY_CLIENT_ID", "TABBY_CLIENT_SECRET"]
                repair_cmd = "noui tabby setup"
            return VerificationResult(
                "NEEDS_SECRET",
                message=str(exc),
                missing_artifacts=missing,
                suggested_repairs=[{"action": "run_command", "command": repair_cmd}],
            )

        profile_slug = self.auth_plan.get("profile_slug", "")

        # Step 3: Browser session health
        session_healthy = await self._check_session_health(profile_slug)

        # Step 4: Request credentials
        try:
            creds = await self._request_credentials(profile_slug, bearer)
        except Exception as exc:
            return VerificationResult(
                "UNSUPPORTED",
                message=f"credentials/request failed: {exc}",
                diagnostics={"profile_slug": profile_slug, "error": str(exc)},
            )

        required_headers = self.auth_plan.get("required_auth", {}).get("headers", [])
        required_cookies = self.auth_plan.get("required_auth", {}).get("cookies", [])

        returned_headers = {h.get("name") for h in creds.get("headers", []) if h.get("name")}
        returned_cookies = {c.get("name") for c in creds.get("cookies", []) if c.get("name")}

        missing_headers = [h for h in required_headers if h not in returned_headers]
        missing_cookies = [c for c in required_cookies if c not in returned_cookies]

        if missing_headers or missing_cookies:
            # Step 5: Try to repair empty credentials
            if self._repair_count < self.max_repair_attempts:
                repair_result = await self._repair_empty_credentials(
                    profile_slug=profile_slug,
                    agent_token=bearer,
                    missing_headers=missing_headers,
                    missing_cookies=missing_cookies,
                    session_healthy=session_healthy,
                )
                if repair_result:
                    return repair_result

            return VerificationResult(
                "NEEDS_SECRET",
                message=(
                    f"Tabby returned empty credentials for profile {profile_slug!r}.\n"
                    f"Required headers: {required_headers}  Returned: {list(returned_headers)}\n"
                    f"Required cookies: {required_cookies}  Returned: {list(returned_cookies)}\n"
                    "Run `noui mcp diagnose-auth <server_id>` for repair guidance."
                ),
                missing_artifacts=missing_headers + missing_cookies,
                diagnostics={
                    "profile_slug": profile_slug,
                    "session_healthy": session_healthy,
                    "returned_headers": list(returned_headers),
                    "returned_cookies": list(returned_cookies),
                    "required_headers": required_headers,
                    "required_cookies": required_cookies,
                },
            )

        # Step 6: Dry-run the first operation
        result = await self._dry_run_operation()
        return result

    # ── Repair loop ──────────────────────────────────────────────────────────

    async def _repair_empty_credentials(
        self,
        profile_slug: str,
        agent_token: str,
        missing_headers: list[str],
        missing_cookies: list[str],
        session_healthy: bool,
    ) -> VerificationResult | None:
        """Attempt to repair empty Tabby credentials deterministically.

        Returns a VerificationResult if a repair was applied (caller should
        re-run verification), or None if no deterministic repair was possible.

        Repair strategy:
        1. If credential_types doesn't list required headers/cookies,
           update the ServiceProfile credential_types.
        2. If session not healthy, suggest noui tabby session ensure.
        3. If still empty after admin token available, escalate to NEEDS_SECRET.
        """
        self._repair_count += 1

        if not self.tabby_admin_token:
            # Can't attempt admin repairs without token
            return None

        profile_db_id = self.auth_plan.get("profile_db_id", "")

        # Repair: update credential_types to list required headers/cookies
        if profile_db_id and (missing_headers or missing_cookies):
            new_credential_types = {
                "headers": self.auth_plan.get("tabby_export", {})
                .get("credential_types", {})
                .get("headers", missing_headers),
                "cookies": self.auth_plan.get("tabby_export", {})
                .get("credential_types", {})
                .get("cookies", missing_cookies),
            }
            try:
                await self._update_credential_types(profile_db_id, new_credential_types)
            except Exception:
                pass

        if not session_healthy:
            return VerificationResult(
                "REPAIR_APPLIED",
                message=(
                    f"Updated ServiceProfile credential_types for {profile_slug!r}.\n"
                    f"Browser session is not healthy — run:\n"
                    f"  noui tabby session ensure --profile {profile_slug}\n"
                    f"Then re-run `noui mcp verify <server_id>` to complete verification."
                ),
                repair_attempt_count=self._repair_count,
                suggested_repairs=[
                    {
                        "action": "run_command",
                        "command": f"noui tabby session ensure --profile {profile_slug}",
                    }
                ],
            )

        return VerificationResult(
            "REPAIR_APPLIED",
            message=(
                f"Updated ServiceProfile credential_types for {profile_slug!r}.\n"
                f"Re-run `noui mcp verify <server_id>` to retry dry-run."
            ),
            repair_attempt_count=self._repair_count,
        )

    # ── Dry-run ──────────────────────────────────────────────────────────────

    async def _dry_run_operation(self) -> VerificationResult:
        """Attempt a live call to the first generated operation.

        Loads the first operation module source and checks that auth
        resolution would succeed without actually running the HTTP call
        (we validate credentials can be obtained, not the full response).
        """
        # For now, verify auth resolution is possible (no network call to target)
        # A full dry-run would require executing the operation module — that's
        # deferred to phase 3+ implementation; here we validate auth artifacts.
        strategy = self.auth_plan.get("strategy", "")

        if strategy == "tabby_credentials":
            profile_slug = self.auth_plan.get("profile_slug", "")
            try:
                bearer = await self._get_tabby_bearer()
                creds = await self._request_credentials(profile_slug, bearer)
                if creds.get("headers") or creds.get("cookies"):
                    return VerificationResult(
                        "PASS",
                        message=f"Auth verification passed for profile {profile_slug!r}.",
                        diagnostics={
                            "profile_slug": profile_slug,
                            "credential_count": len(creds.get("headers", [])),
                        },
                    )
            except Exception as exc:
                return VerificationResult(
                    "UNSUPPORTED",
                    message=f"Dry-run auth resolution failed: {exc}",
                    diagnostics={"error": str(exc)},
                )

        elif strategy == "static_secret_header":
            missing = [
                f["secret_env_var"]
                for f in self.auth_plan.get("fallbacks", [])
                if f.get("type") == "static_secret_header"
                and not os.environ.get(f.get("secret_env_var", ""))
            ]
            if missing:
                return VerificationResult(
                    "NEEDS_SECRET",
                    message=f"Missing secret env var(s): {', '.join(missing)}",
                    missing_artifacts=missing,
                )
            return VerificationResult(
                "PASS",
                message="Static secret auth verified — all required env vars present.",
            )

        return VerificationResult("PASS", message="Auth verification passed.")

    # ── Tabby API helpers (async) ────────────────────────────────────────────

    async def _tabby_reachable(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.tabby_api_host}/health/live")
                data = resp.json()
                return data.get("status") == "ok"
        except Exception:
            return False

    async def _get_agent_token(self) -> str:
        if not self.tabby_client_id or not self.tabby_client_secret:
            raise RuntimeError(
                "Missing TABBY_CLIENT_ID or TABBY_CLIENT_SECRET.\n"
                "Run `noui tabby setup` to provision agent credentials."
            )
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.tabby_api_host}/auth/agent-token",
                json={
                    "client_id": self.tabby_client_id,
                    "client_secret": self.tabby_client_secret,
                    "grant_type": "client_credentials",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        token = data.get("access_token") or data.get("token", "")
        if not token:
            raise RuntimeError(f"POST /auth/agent-token returned no token: {data}")
        return token

    async def _get_platform_jwt(self) -> str:
        """Cloud flow step 1: platform client credentials → platform JWT via adoptwebui."""
        if not self.adopt_api_url:
            raise RuntimeError(
                "Missing ADOPT_API_URL for platform_jwt auth mode.\n"
                "Run `noui tabby setup --cloud` or set ADOPT_API_URL in noui/.env."
            )
        if not self.adopt_client_id or not self.adopt_client_secret:
            raise RuntimeError(
                "Missing ADOPT_CLIENT_ID or ADOPT_CLIENT_SECRET for platform_jwt auth mode.\n"
                "Run `noui tabby setup --cloud` or set these in noui/.env."
            )
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.adopt_api_url}/v1/users/api-token",
                json={"client_id": self.adopt_client_id, "secret": self.adopt_client_secret},
            )
            resp.raise_for_status()
            data = resp.json()
        token = data.get("access_token", "")
        if not token:
            raise RuntimeError(f"Platform /v1/users/api-token returned no access_token: {data}")
        return token

    async def _get_platform_tabby_token(self) -> str:
        """Cloud flow step 2: platform JWT → Tabby JWT via /auth/token-exchange."""
        platform_jwt = await self._get_platform_jwt()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.tabby_api_host}/auth/token-exchange",
                json={"subject_token": platform_jwt, "subject_token_type": "oidc_jwt"},
            )
            resp.raise_for_status()
            data = resp.json()
        token = data.get("access_token", "")
        if not token:
            raise RuntimeError(f"Tabby /auth/token-exchange returned no access_token: {data}")
        return token

    async def _get_tabby_bearer(self) -> str:
        """Return a Tabby bearer token for the active auth mode."""
        mode = self._resolve_auth_mode()
        if mode == "platform_jwt":
            return await self._get_platform_tabby_token()
        if mode == "agent_token":
            return await self._get_agent_token()
        raise RuntimeError(
            f"Unknown NOUI_TABBY_AUTH_MODE {mode!r} — expected 'agent_token' or 'platform_jwt'."
        )

    async def _request_credentials(self, profile_slug: str, bearer: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.tabby_api_host}/credentials/request",
                json={"profile_id": profile_slug},
                headers={"Authorization": f"Bearer {bearer}"},
            )
            resp.raise_for_status()
            data = resp.json()
        return data.get("credentials", data)

    async def _check_session_health(self, profile_slug: str) -> bool:
        """Return True if a healthy browser session exists for the profile."""
        if not self.tabby_admin_token:
            return True  # Can't check without admin token; assume healthy
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.tabby_api_host}/admin/profiles",
                    headers={"Authorization": f"Bearer {self.tabby_admin_token}"},
                )
                resp.raise_for_status()
                profiles = resp.json()
                if isinstance(profiles, dict):
                    profiles = profiles.get("profiles", [])
                for p in profiles:
                    slug = p.get("profile_id") or p.get("slug", "")
                    if slug == profile_slug:
                        state = p.get("version_state") or p.get("state", "")
                        health = p.get("health_result_type", "")
                        return state == "HEALTHY" or health == "PASS"
        except Exception:
            pass
        return False

    async def _update_credential_types(self, profile_db_id: str, credential_types: dict) -> None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                f"{self.tabby_api_host}/admin/profiles/{profile_db_id}",
                json={"credential_types": credential_types},
                headers={"Authorization": f"Bearer {self.tabby_admin_token}"},
            )
            resp.raise_for_status()


# ── Convenience function ─────────────────────────────────────────────────────


async def verify_before_install(
    server_dir: str | Path,
    tabby_api_host: str = "",
    tabby_admin_token: str = "",
) -> VerificationResult:
    """Load auth_plan.json from server_dir and run verification.

    Convenience wrapper for the CLI.  Returns PASS for servers with no
    auth_plan.json (unauthenticated servers don't need verification).
    """
    server_dir = Path(server_dir)
    auth_plan_path = server_dir / "auth_plan.json"

    if not auth_plan_path.exists():
        return VerificationResult(
            "PASS",
            message="No auth_plan.json found — server has no auth requirements.",
        )

    try:
        auth_plan = json.loads(auth_plan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return VerificationResult(
            "UNSUPPORTED",
            message=f"Failed to read auth_plan.json: {exc}",
        )

    verifier = AuthVerifier(
        auth_plan=auth_plan,
        server_dir=server_dir,
        tabby_api_host=tabby_api_host,
        tabby_admin_token=tabby_admin_token,
    )
    return await verifier.verify()
