"""Generate auth_plan.json for a compiled MCP server.

Pure functions — no web framework or DB dependencies.

The AuthPlan captures:
  - Which auth strategy to use (tabby_credentials vs static_secret_header)
  - What headers/cookies the workflow HAR requires
  - What Tabby profile to use (slug for runtime, db_id for admin ops)
  - Fallback recipes for static-secret apps

The plan never stores secret values — only source metadata and recipes.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# ── Constants ────────────────────────────────────────────────────────────────

_AUTH_HEADER_NAMES = frozenset({"authorization", "x-api-key", "x-auth-token"})
_BEARER_RE = re.compile(r"^Bearer\s+\S+", re.IGNORECASE)
# Cookies that rotate per request (Akamai/CDN) are marked VOLATILE in
# credential_types; everything else STABLE. Tabby's consumer requires
# {name, volatility} objects for cookies.
_VOLATILE_COOKIE_NAMES = frozenset({"bm_sz", "ak_bmsc", "bm_so", "bm_s", "_abck"})


# ── Helpers ──────────────────────────────────────────────────────────────────


def _detect_bearer_scheme(header_value: str) -> str | None:
    """Return 'Bearer' if the value matches Bearer <token>, else None."""
    if _BEARER_RE.match(header_value.strip()):
        return "Bearer"
    return None


def _extract_observed_auth_headers(har: dict) -> dict[str, dict]:
    """Return auth headers observed in workflow requests, with metadata (no values)."""
    observed: dict[str, dict] = {}
    entries = har.get("log", {}).get("entries", [])
    for entry in entries:
        for h in entry.get("request", {}).get("headers", []):
            name = h.get("name", "")
            if name.lower() in _AUTH_HEADER_NAMES:
                value = h.get("value", "")
                if name not in observed:
                    observed[name] = {
                        "source": "workflow_har",
                        "scheme": _detect_bearer_scheme(value),
                        "value_redacted": True,
                    }
    return observed


def _is_static_api_key_app(har: dict) -> bool:
    """Heuristic: returns True when HAR shows auth headers but no Set-Cookie responses.

    Rationale: session-based apps set cookies after login; static API-key apps never
    issue Set-Cookie — the key is supplied directly on every request.
    """
    entries = har.get("log", {}).get("entries", [])
    has_auth_header = False
    has_set_cookie = False
    for entry in entries:
        for h in entry.get("request", {}).get("headers", []):
            if h.get("name", "").lower() in _AUTH_HEADER_NAMES:
                has_auth_header = True
        for h in entry.get("response", {}).get("headers", []):
            if h.get("name", "").lower() == "set-cookie":
                has_set_cookie = True
    return has_auth_header and not has_set_cookie


def _env_var_name(app_slug: str, header_name: str) -> str:
    """Derive a conventional env var name from the app slug and header name.

    Examples:
        ("example-bank", "Authorization") → "EXAMPLE_BANK_API_KEY"
        ("example-bank", "X-Api-Key")     → "EXAMPLE_BANK_X_API_KEY"
    """
    slug_part = re.sub(r"[^A-Z0-9]+", "_", app_slug.upper()).strip("_")
    if header_name.lower() == "authorization":
        return f"{slug_part}_API_KEY"
    header_part = re.sub(r"[^A-Z0-9]+", "_", header_name.upper()).strip("_")
    return f"{slug_part}_{header_part}"


def _extract_target_domains(har: dict) -> list[str]:
    """Collect unique netloc values from all request URLs in the HAR."""
    domains: list[str] = []
    seen: set[str] = set()
    for entry in har.get("log", {}).get("entries", []):
        url = entry.get("request", {}).get("url", "")
        try:
            netloc = urlparse(url).netloc
        except Exception:
            continue
        if netloc and netloc not in seen:
            seen.add(netloc)
            domains.append(netloc)
    return domains


# ── Main generator ───────────────────────────────────────────────────────────


def generate_auth_plan(
    har: dict,
    auth_info: dict,
    profile_slug: str,
    profile_db_id: str,
    app_slug: str,
    target_domains: list[str] | None = None,
    login_credential_headers: list[str] | None = None,
    declared_strategy: str | None = None,
    static_secret_headers: list[str] | None = None,
    sandbox_secrets: bool = False,
) -> dict:
    """Generate an auth_plan dict from a workflow HAR and auth signal analysis.

    Args:
        har: Parsed HAR dict (standard HAR 1.2 format).
        auth_info: Output of detect_auth_from_har() — auth signal summary.
        profile_slug: Tabby profile slug used for runtime credential requests.
        profile_db_id: Tabby profile DB UUID used for admin/profile version ops.
        app_slug: URL-safe lowercase slug for the app (used for env var naming).
        target_domains: Override list of target domains; auto-detected if None.
        declared_strategy: Explicit auth model declared by the operator at
            capture/compile time ("tabby_credentials" | "static_secret_header").
            When set, it OVERRIDES the `_is_static_api_key_app` HAR heuristic
            entirely — the operator told us the app's auth model, so we never
            guess (and the false-positive where a separately-recorded session
            login looks "static" cannot happen). `None` (the default) keeps the
            legacy heuristic + `login_credential_headers` fallback below.
        static_secret_headers: Header names to turn into `${SECRET:name}` recipes
            when `declared_strategy == "static_secret_header"`. Use when the login
            recording was skipped (static API-key mode) so the workflow HAR may be
            the only place the header appears — these names are folded into
            required_headers. Ignored for any other strategy.
        login_credential_headers: Header names the paired login profile already
            declares in its Tabby `credential_types.headers` (i.e. Tabby's
            worker actively captures these from real page traffic via
            `export_policy.request_header_allowlist` and serves them dynamically
            via `/credentials/request` — see `login_assets.py::generate()`).
            When every header this workflow requires is already covered here,
            the workflow needs no static secret even though `_is_static_api_key_app`
            would otherwise flag it (its heuristic only sees the workflow's own
            HAR, which — since login happens in a separate capture — never has
            the Set-Cookie response that would tell it this is a session app).

    Returns:
        auth_plan dict (safe to serialise to JSON — no secret values).
    """
    required_headers = list(dict.fromkeys(auth_info.get("auth_header_names", [])))
    required_cookies = list(dict.fromkeys(auth_info.get("set_cookie_names", [])))
    observed = _extract_observed_auth_headers(har)

    if target_domains is None:
        target_domains = _extract_target_domains(har)

    if declared_strategy not in (None, "tabby_credentials", "static_secret_header"):
        raise ValueError(
            "declared_strategy must be 'tabby_credentials', 'static_secret_header', or None; "
            f"got {declared_strategy!r}"
        )

    # A static-secret declaration may name the auth header explicitly (e.g. the
    # login was skipped, so the workflow HAR is the only signal). Fold those names
    # into required_headers so each gets a ${SECRET:name} recipe below.
    if declared_strategy == "static_secret_header" and static_secret_headers:
        required_headers = list(dict.fromkeys([*required_headers, *static_secret_headers]))

    login_headers_lower = {h.lower() for h in (login_credential_headers or [])}
    covered_by_login = bool(required_headers) and all(
        h.lower() in login_headers_lower for h in required_headers
    )

    # Explicit declaration wins; otherwise fall back to the HAR heuristic (with
    # the login-coverage veto). This is the seam that removes the guessing.
    if declared_strategy is not None:
        is_static = declared_strategy == "static_secret_header"
    else:
        is_static = _is_static_api_key_app(har) and not covered_by_login
    strategy = "static_secret_header" if is_static else "tabby_credentials"

    # Build fallbacks for headers that need static secrets
    fallbacks: list[dict] = []
    if is_static:
        for header_name in required_headers:
            base_env_var = _env_var_name(app_slug, header_name)
            if sandbox_secrets:
                # Harness/sandbox execution can ONLY read vault secrets whose key
                # is prefixed with SANDBOX_ (the platform's list_sandbox_secrets is
                # hard-scoped to that prefix). Emit the prefixed name so an admin
                # registers exactly this key and the sandbox can inject it. The
                # prefix stays uppercase (the inject scope is literally "SANDBOX_");
                # the base keeps the lowercase ${SECRET:...} token convention.
                env_var = f"SANDBOX_{base_env_var}"
                secret_ref = f"SANDBOX_{base_env_var.lower()}"
            else:
                env_var = base_env_var
                secret_ref = base_env_var.lower()
            scheme = observed.get(header_name, {}).get("scheme")
            value_template = f"{scheme} ${{{env_var}}}" if scheme else f"${{{env_var}}}"
            fallbacks.append(
                {
                    "type": "static_secret_header",
                    "header": header_name,
                    "value_template": value_template,
                    "secret_env_var": env_var,
                    # Exact ${SECRET:<name>} token the harness must resolve — the
                    # vault key, verbatim (case preserved). Consumers must use this
                    # rather than re-deriving via secret_env_var.lower(), so a
                    # SANDBOX_ prefix survives casing.
                    "secret_ref": secret_ref,
                }
            )

    return {
        "profile_slug": profile_slug or app_slug,
        "profile_db_id": profile_db_id or "",
        "strategy": strategy,
        "target_domains": target_domains,
        "required_auth": {
            "headers": required_headers,
            "cookies": required_cookies,
        },
        "observed_workflow_headers": observed,
        "tabby_export": {
            "credential_types": {
                "headers": required_headers,
                "cookies": [
                    {
                        "name": c,
                        "volatility": "VOLATILE" if c in _VOLATILE_COOKIE_NAMES else "STABLE",
                    }
                    for c in required_cookies
                ],
            },
            "runtime_identifier": profile_slug or app_slug,
        },
        "fallbacks": fallbacks,
    }
