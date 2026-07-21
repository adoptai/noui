"""Regression tests for compiler/mcp/auth_plan.py.

Covers the acceptance criteria scenarios from the plan:
- Cookie session auth
- Bearer token captured during login (tabby_credentials)
- Bearer token captured only during later API call (static_secret_header)
- Static API key entered in a single field (static_secret_header)
- CSRF header plus cookie (tabby_credentials)
- Profile DB id vs slug kept separate
- Empty Tabby credentials despite healthy session (detection)
"""

from __future__ import annotations

import sys
from pathlib import Path

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.compile.auth_plan import (
    _env_var_name,
    _is_static_api_key_app,
    generate_auth_plan,
)

# ── HAR fixtures ─────────────────────────────────────────────────────────────


def _make_har(entries: list[dict]) -> dict:
    return {"log": {"entries": entries}}


def _make_entry(
    url: str = "https://example.com/api/data",
    method: str = "GET",
    request_headers: list[dict] | None = None,
    response_headers: list[dict] | None = None,
) -> dict:
    return {
        "request": {
            "url": url,
            "method": method,
            "headers": request_headers or [],
        },
        "response": {
            "headers": response_headers or [],
        },
    }


def _bearer_entry(url: str = "https://api.example.com/data") -> dict:
    return _make_entry(
        url=url,
        request_headers=[{"name": "Authorization", "value": "Bearer abc123"}],
    )


def _set_cookie_response() -> dict:
    return {"name": "Set-Cookie", "value": "session=xyz; Path=/; HttpOnly"}


# ── Static API key detection ─────────────────────────────────────────────────


class TestStaticApiKeyDetection:
    def test_bearer_without_set_cookie_is_static(self) -> None:
        """App uses static API key when Authorization header appears but no Set-Cookie."""
        har = _make_har([_bearer_entry()])
        assert _is_static_api_key_app(har) is True

    def test_bearer_with_set_cookie_is_not_static(self) -> None:
        """Session-based app: Authorization + Set-Cookie → tabby_credentials."""
        entry = _make_entry(
            request_headers=[{"name": "Authorization", "value": "Bearer token"}],
            response_headers=[_set_cookie_response()],
        )
        har = _make_har([entry])
        assert _is_static_api_key_app(har) is False

    def test_cookie_only_is_not_static(self) -> None:
        """Cookie-only auth (no Authorization header) → tabby_credentials."""
        entry = _make_entry(
            request_headers=[{"name": "Cookie", "value": "session=abc"}],
            response_headers=[_set_cookie_response()],
        )
        har = _make_har([entry])
        assert _is_static_api_key_app(har) is False

    def test_no_auth_is_not_static(self) -> None:
        har = _make_har([_make_entry()])
        assert _is_static_api_key_app(har) is False

    def test_x_api_key_without_set_cookie_is_static(self) -> None:
        entry = _make_entry(request_headers=[{"name": "X-Api-Key", "value": "key123"}])
        har = _make_har([entry])
        assert _is_static_api_key_app(har) is True


# ── Env var naming ───────────────────────────────────────────────────────────


class TestEnvVarNaming:
    def test_authorization_becomes_api_key(self) -> None:
        assert _env_var_name("example-bank", "Authorization") == "EXAMPLE_BANK_API_KEY"

    def test_x_api_key_header(self) -> None:
        assert _env_var_name("my-app", "X-Api-Key") == "MY_APP_X_API_KEY"

    def test_hyphen_in_slug(self) -> None:
        name = _env_var_name("some-cool-app", "Authorization")
        assert name == "SOME_COOL_APP_API_KEY"


# ── Auth plan generation — strategy selection ────────────────────────────────


class TestAuthPlanStrategy:
    def _auth_info_with_bearer(self) -> dict:
        return {
            "has_auth_headers": True,
            "has_cookies": False,
            "has_csrf": False,
            "auth_header_names": ["Authorization"],
            "csrf_header_names": [],
            "set_cookie_names": [],
            "auth_domains": ["api.example.com"],
        }

    def _auth_info_with_cookies(self) -> dict:
        return {
            "has_auth_headers": False,
            "has_cookies": True,
            "has_csrf": False,
            "auth_header_names": [],
            "csrf_header_names": [],
            "set_cookie_names": ["session"],
            "auth_domains": ["app.example.com"],
        }

    def test_static_api_key_app_gets_static_strategy(self) -> None:
        har = _make_har([_bearer_entry()])
        plan = generate_auth_plan(
            har=har,
            auth_info=self._auth_info_with_bearer(),
            profile_slug="example-bank",
            profile_db_id="some-uuid",
            app_slug="example-bank",
        )
        assert plan["strategy"] == "static_secret_header"

    def test_session_cookie_app_gets_tabby_strategy(self) -> None:
        entry = _make_entry(
            response_headers=[_set_cookie_response()],
        )
        har = _make_har([entry])
        plan = generate_auth_plan(
            har=har,
            auth_info=self._auth_info_with_cookies(),
            profile_slug="myapp",
            profile_db_id="",
            app_slug="myapp",
        )
        assert plan["strategy"] == "tabby_credentials"

    def test_bearer_plus_set_cookie_gets_tabby_strategy(self) -> None:
        """When both Authorization and Set-Cookie appear, it's session-based."""
        entry = _make_entry(
            request_headers=[{"name": "Authorization", "value": "Bearer tok"}],
            response_headers=[_set_cookie_response()],
        )
        har = _make_har([entry])
        auth_info = {
            **self._auth_info_with_bearer(),
            "has_cookies": True,
            "set_cookie_names": ["session"],
        }
        plan = generate_auth_plan(
            har=har,
            auth_info=auth_info,
            profile_slug="myapp",
            profile_db_id="",
            app_slug="myapp",
        )
        assert plan["strategy"] == "tabby_credentials"


# ── Profile slug vs DB id ────────────────────────────────────────────────────


class TestProfileIdentifiers:
    def test_slug_and_db_id_stored_separately(self) -> None:
        har = _make_har([_bearer_entry()])
        auth_info = {
            "has_auth_headers": True,
            "has_cookies": False,
            "has_csrf": False,
            "auth_header_names": ["Authorization"],
            "csrf_header_names": [],
            "set_cookie_names": [],
            "auth_domains": [],
        }
        plan = generate_auth_plan(
            har=har,
            auth_info=auth_info,
            profile_slug="example-bank",
            profile_db_id="8fdadf43-01f5-48ab-905b-fc7e4d4b3c70",
            app_slug="example-bank",
        )
        assert plan["profile_slug"] == "example-bank"
        assert plan["profile_db_id"] == "8fdadf43-01f5-48ab-905b-fc7e4d4b3c70"
        # The runtime identifier must use the SLUG, not the UUID
        assert plan["tabby_export"]["runtime_identifier"] == "example-bank"

    def test_runtime_identifier_is_slug_not_uuid(self) -> None:
        """credentials/request must use slug — UUID must never appear in runtime_identifier."""
        har = _make_har([_bearer_entry()])
        auth_info = {
            "has_auth_headers": True,
            "has_cookies": False,
            "has_csrf": False,
            "auth_header_names": ["Authorization"],
            "csrf_header_names": [],
            "set_cookie_names": [],
            "auth_domains": [],
        }
        plan = generate_auth_plan(
            har=har,
            auth_info=auth_info,
            profile_slug="my-service",
            profile_db_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            app_slug="my-service",
        )
        runtime_id = plan["tabby_export"]["runtime_identifier"]
        assert runtime_id == "my-service", (
            f"runtime_identifier must be the slug 'my-service', got {runtime_id!r}. "
            "The UUID must never be used for runtime credential requests."
        )


# ── Fallback recipes ─────────────────────────────────────────────────────────


class TestFallbacks:
    def test_static_secret_fallback_has_no_secret_value(self) -> None:
        """Fallbacks must contain value_template (recipe), never the actual secret."""
        har = _make_har([_bearer_entry()])
        auth_info = {
            "has_auth_headers": True,
            "has_cookies": False,
            "has_csrf": False,
            "auth_header_names": ["Authorization"],
            "csrf_header_names": [],
            "set_cookie_names": [],
            "auth_domains": [],
        }
        plan = generate_auth_plan(
            har=har,
            auth_info=auth_info,
            profile_slug="example-bank",
            profile_db_id="",
            app_slug="example-bank",
        )
        assert plan["fallbacks"], "Static secret app should have at least one fallback"
        fb = plan["fallbacks"][0]
        assert fb["type"] == "static_secret_header"
        assert "${" in fb["value_template"], "value_template must use ${ENV_VAR} placeholder"
        assert "secret_env_var" in fb
        # Ensure the fallback does not embed any actual secret value
        assert "abc123" not in str(fb), "Fallback must not embed actual secret values from HAR"

    def test_bearer_fallback_template_includes_bearer_prefix(self) -> None:
        har = _make_har([_bearer_entry()])
        auth_info = {
            "has_auth_headers": True,
            "has_cookies": False,
            "has_csrf": False,
            "auth_header_names": ["Authorization"],
            "csrf_header_names": [],
            "set_cookie_names": [],
            "auth_domains": [],
        }
        plan = generate_auth_plan(
            har=har,
            auth_info=auth_info,
            profile_slug="example-bank",
            profile_db_id="",
            app_slug="example-bank",
        )
        fb = plan["fallbacks"][0]
        assert fb["value_template"].startswith("Bearer "), (
            "Bearer scheme must be preserved in value_template for Authorization headers"
        )

    def test_tabby_app_has_no_static_fallback(self) -> None:
        entry = _make_entry(response_headers=[_set_cookie_response()])
        har = _make_har([entry])
        auth_info = {
            "has_auth_headers": False,
            "has_cookies": True,
            "has_csrf": False,
            "auth_header_names": [],
            "csrf_header_names": [],
            "set_cookie_names": ["session"],
            "auth_domains": [],
        }
        plan = generate_auth_plan(
            har=har,
            auth_info=auth_info,
            profile_slug="myapp",
            profile_db_id="",
            app_slug="myapp",
        )
        assert plan["fallbacks"] == [], (
            "tabby_credentials apps must not have static secret fallbacks"
        )


# ── Required auth fields ─────────────────────────────────────────────────────


class TestRequiredAuth:
    def test_required_headers_from_auth_info(self) -> None:
        har = _make_har([_bearer_entry()])
        auth_info = {
            "has_auth_headers": True,
            "has_cookies": False,
            "has_csrf": False,
            "auth_header_names": ["Authorization", "X-Request-Id"],
            "csrf_header_names": [],
            "set_cookie_names": [],
            "auth_domains": [],
        }
        plan = generate_auth_plan(
            har=har, auth_info=auth_info, profile_slug="", profile_db_id="", app_slug="app"
        )
        assert "Authorization" in plan["required_auth"]["headers"]

    def test_csrf_detected_in_tabby_strategy(self) -> None:
        entry = _make_entry(
            request_headers=[
                {"name": "X-CSRF-Token", "value": "tok"},
                {"name": "Cookie", "value": "session=abc"},
            ],
            response_headers=[_set_cookie_response()],
        )
        har = _make_har([entry])
        auth_info = {
            "has_auth_headers": False,
            "has_cookies": True,
            "has_csrf": True,
            "auth_header_names": [],
            "csrf_header_names": ["X-CSRF-Token"],
            "set_cookie_names": ["session"],
            "auth_domains": [],
        }
        plan = generate_auth_plan(
            har=har,
            auth_info=auth_info,
            profile_slug="csrf-app",
            profile_db_id="",
            app_slug="csrf-app",
        )
        # CSRF apps should use tabby_credentials (Tabby manages session + CSRF)
        assert plan["strategy"] == "tabby_credentials"


# ── login_credential_headers override (cross-capture dynamic bearer) ────────
#
# A workflow's own HAR is captured separately from its paired login — it never
# has the Set-Cookie response that would tell `_is_static_api_key_app` this is
# a session-based app, so a client-managed bearer token (e.g. QBO's) gets
# misclassified as needing a static secret even when the login profile already
# declares Tabby dynamically captures it (`credential_types.headers`).


class TestLoginCredentialHeadersOverride:
    def _bearer_auth_info(self) -> dict:
        return {
            "has_auth_headers": True,
            "has_cookies": False,
            "has_csrf": False,
            "auth_header_names": ["Authorization"],
            "csrf_header_names": [],
            "set_cookie_names": [],
            "auth_domains": [],
        }

    def test_header_covered_by_login_profile_gets_tabby_strategy(self) -> None:
        """The exact scenario this closes: a bearer-only HAR (no Set-Cookie)
        would normally be static_secret_header, but the paired login profile
        already declares it — so it should resolve to tabby_credentials."""
        har = _make_har([_bearer_entry()])
        plan = generate_auth_plan(
            har=har,
            auth_info=self._bearer_auth_info(),
            profile_slug="qbo-sandbox",
            profile_db_id="",
            app_slug="qbo-sandbox",
            login_credential_headers=["authorization", "x-csrf-token", "csrftoken"],
        )
        assert plan["strategy"] == "tabby_credentials"
        assert plan["fallbacks"] == []

    def test_header_covered_case_insensitively(self) -> None:
        har = _make_har([_bearer_entry()])
        plan = generate_auth_plan(
            har=har,
            auth_info=self._bearer_auth_info(),
            profile_slug="qbo-sandbox",
            profile_db_id="",
            app_slug="qbo-sandbox",
            login_credential_headers=["AUTHORIZATION"],
        )
        assert plan["strategy"] == "tabby_credentials"

    def test_header_not_covered_by_login_profile_keeps_static_strategy(self) -> None:
        """Login profile declares a different header — no override should apply."""
        har = _make_har([_bearer_entry()])
        plan = generate_auth_plan(
            har=har,
            auth_info=self._bearer_auth_info(),
            profile_slug="qbo-sandbox",
            profile_db_id="",
            app_slug="qbo-sandbox",
            login_credential_headers=["x-csrf-token"],
        )
        assert plan["strategy"] == "static_secret_header"
        assert plan["fallbacks"]

    def test_no_login_credential_headers_is_unchanged(self) -> None:
        """Default (None) behaves exactly like before this parameter existed."""
        har = _make_har([_bearer_entry()])
        plan = generate_auth_plan(
            har=har,
            auth_info=self._bearer_auth_info(),
            profile_slug="qbo-sandbox",
            profile_db_id="",
            app_slug="qbo-sandbox",
        )
        assert plan["strategy"] == "static_secret_header"

    def test_partial_coverage_does_not_trigger_override(self) -> None:
        """Only some required headers covered → keep the existing heuristic
        (the runtime has no hybrid dynamic+static resolution path)."""
        har = _make_har(
            [
                _bearer_entry(),
                _make_entry(request_headers=[{"name": "X-Api-Key", "value": "k"}]),
            ]
        )
        auth_info = {
            "has_auth_headers": True,
            "has_cookies": False,
            "has_csrf": False,
            "auth_header_names": ["Authorization", "X-Api-Key"],
            "csrf_header_names": [],
            "set_cookie_names": [],
            "auth_domains": [],
        }
        plan = generate_auth_plan(
            har=har,
            auth_info=auth_info,
            profile_slug="qbo-sandbox",
            profile_db_id="",
            app_slug="qbo-sandbox",
            login_credential_headers=["authorization"],  # X-Api-Key not covered
        )
        assert plan["strategy"] == "static_secret_header"


# ── declared_strategy override (explicit auth-type flag at capture/compile) ──
#
# The operator declares the auth model up front instead of relying on the HAR
# heuristic. This is what removes the false-positive where a separately-recorded
# session login (no Set-Cookie in the workflow HAR) looks like a static API key.


class TestDeclaredStrategyOverride:
    def _bearer_auth_info(self) -> dict:
        return {
            "has_auth_headers": True,
            "has_cookies": False,
            "has_csrf": False,
            "auth_header_names": ["Authorization"],
            "csrf_header_names": [],
            "set_cookie_names": [],
            "auth_domains": [],
        }

    def test_session_declaration_forces_tabby_despite_static_looking_har(self) -> None:
        """A bearer-only HAR (would heuristically be static) declared 'session'
        must resolve to tabby_credentials with no static fallbacks."""
        har = _make_har([_bearer_entry()])  # no Set-Cookie → heuristic says static
        assert _is_static_api_key_app(har) is True
        plan = generate_auth_plan(
            har=har,
            auth_info=self._bearer_auth_info(),
            profile_slug="qbo-sandbox",
            profile_db_id="",
            app_slug="qbo-sandbox",
            declared_strategy="tabby_credentials",
        )
        assert plan["strategy"] == "tabby_credentials"
        assert plan["fallbacks"] == []

    def test_api_key_declaration_forces_static_despite_set_cookie(self) -> None:
        """A session-looking HAR (has Set-Cookie) declared static must still
        produce static_secret_header with a fallback recipe."""
        entry = _make_entry(
            request_headers=[{"name": "Authorization", "value": "Bearer tok"}],
            response_headers=[_set_cookie_response()],
        )
        har = _make_har([entry])
        assert _is_static_api_key_app(har) is False
        plan = generate_auth_plan(
            har=har,
            auth_info=self._bearer_auth_info(),
            profile_slug="acme",
            profile_db_id="",
            app_slug="acme",
            declared_strategy="static_secret_header",
        )
        assert plan["strategy"] == "static_secret_header"
        assert plan["fallbacks"]

    def test_static_secret_headers_are_folded_into_required_and_fallbacks(self) -> None:
        """An explicitly declared header not seen in auth_info still gets a recipe."""
        har = _make_har([_make_entry()])  # no auth header in HAR at all
        auth_info = {
            "has_auth_headers": False,
            "has_cookies": False,
            "has_csrf": False,
            "auth_header_names": [],
            "csrf_header_names": [],
            "set_cookie_names": [],
            "auth_domains": [],
        }
        plan = generate_auth_plan(
            har=har,
            auth_info=auth_info,
            profile_slug="acme",
            profile_db_id="",
            app_slug="acme",
            declared_strategy="static_secret_header",
            static_secret_headers=["X-Api-Key"],
        )
        assert "X-Api-Key" in plan["required_auth"]["headers"]
        env_vars = [fb["secret_env_var"] for fb in plan["fallbacks"]]
        assert _env_var_name("acme", "X-Api-Key") in env_vars

    def test_static_secret_headers_ignored_for_session_declaration(self) -> None:
        """static_secret_headers only applies under a static declaration."""
        har = _make_har([_bearer_entry()])
        plan = generate_auth_plan(
            har=har,
            auth_info=self._bearer_auth_info(),
            profile_slug="acme",
            profile_db_id="",
            app_slug="acme",
            declared_strategy="tabby_credentials",
            static_secret_headers=["X-Api-Key"],
        )
        assert plan["strategy"] == "tabby_credentials"
        assert "X-Api-Key" not in plan["required_auth"]["headers"]

    def test_invalid_declared_strategy_raises(self) -> None:
        har = _make_har([_bearer_entry()])
        try:
            generate_auth_plan(
                har=har,
                auth_info=self._bearer_auth_info(),
                profile_slug="acme",
                profile_db_id="",
                app_slug="acme",
                declared_strategy="nonsense",
            )
        except ValueError as exc:
            assert "declared_strategy" in str(exc)
        else:
            raise AssertionError("expected ValueError for invalid declared_strategy")
