"""The auto-resolve `wait_for_url` must never be a pattern a login can't satisfy.

Live failure this pins: an `airbnb-best-places` profile on dev compiled
`https://www.airbnb.com/login/**` as its *logged-in* check. That matches
`/login/otp` but not `/`, `/hosting/listings` or `/s/homes`, so no real sign-in
could ever satisfy it. (That profile's session was separately stuck in STARTING
for other reasons; this pattern is what would have kept it from auto-resolving
once it got going — every login would burn the 30s wait and fall through to
"Mark as Resolved".)

Emitting no `wait_for_url` is the safe outcome: login then completes on the
human's click, which always works.

The `_matches` helper below mirrors the two matchers that actually consume these
globs, so a pattern shape that looks fine but can never fire cannot regress in.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch

import pytest
from noui_core.compile.login_assets import _derive_post_login_pattern


def _matches(url: str, pattern: str) -> bool:
    """True only if BOTH consumers of the glob match `url`.

    Mirrors Tabby's `urlGlobToRegex` (worker login-dsl-runner.ts: `**` → `.*`,
    unanchored `new RegExp`) and Playwright's anchored `waitForURL` glob.
    """
    body = re.sub(r"[.+^${}()|\[\]\\]", lambda m: "\\" + m.group(0), pattern)
    body = body.replace("**", "\0").replace("*", "[^/]*").replace("\0", ".*").replace("?", "[^/]")
    return bool(re.search(body, url)) and bool(re.fullmatch(body, url))


class TestTheLiveAirbnbFailure:
    def test_login_flow_landing_yields_no_pattern(self):
        assert (
            _derive_post_login_pattern(
                "https://www.airbnb.com/", "https://www.airbnb.com/login/otp"
            )
            == ""
        )

    def test_the_bad_pattern_could_not_have_matched_a_real_sign_in(self):
        """Why it hung, asserted rather than asserted-about."""
        bad = "https://www.airbnb.com/login/**"
        for logged_in in (
            "https://www.airbnb.com/",
            "https://www.airbnb.com/hosting/listings",
            "https://www.airbnb.com/s/homes",
        ):
            assert not fnmatch(logged_in, bad)


class TestRejections:
    @pytest.mark.parametrize(
        "landing",
        [
            "https://app.test/login",
            "https://app.test/signin/step2",
            "https://app.test/sign-in",
            "https://app.test/auth/callback",
            "https://app.test/oauth2/authorize",
            "https://app.test/sso/saml",
            "https://app.test/verifyotp?x=1",
            "https://app.test/mfa",
            "https://app.test/challenge/abc",
            "https://app.test/enterpassword?redirectTo=%2F",
            "https://app.test/register",
        ],
    )
    def test_login_flow_routes_are_rejected(self, landing):
        assert _derive_post_login_pattern("https://app.test/", landing) == ""

    def test_landing_sharing_the_login_route_is_rejected(self):
        """Covers custom auth routes the keyword list doesn't know."""
        assert (
            _derive_post_login_pattern(
                "https://app.test/gateway/entry", "https://app.test/gateway/landed"
            )
            == ""
        )

    def test_pattern_that_also_matches_the_login_page_is_rejected(self):
        """It would auto-resolve instantly, before the human has logged in."""
        assert (
            _derive_post_login_pattern("https://app.test/app/login", "https://app.test/app/home")
            == ""
        )

    def test_case_insensitive(self):
        assert _derive_post_login_pattern("https://app.test/", "https://app.test/LogIn/OTP") == ""


class TestAccepted:
    @pytest.mark.parametrize(
        ("login", "landing", "expected"),
        [
            (
                "https://app.test/login",
                "https://app.test/dashboard",
                "https://app.test/dashboard**",
            ),
            (
                "https://www.airbnb.com/login",
                "https://www.airbnb.com/hosting/listings",
                "https://www.airbnb.com/hosting**",
            ),
            # Expedia's real post-login landing — auth-adjacent looking, but genuine.
            (
                "https://www.expedia.com/login",
                "https://www.expedia.com/onboarding?originUrl=%2F",
                "https://www.expedia.com/onboarding**",
            ),
            # Salesforce Lightning: different host after login.
            (
                "https://login.salesforce.com/",
                "https://acme.lightning.force.com/lightning/page/home",
                "https://acme.lightning.force.com/lightning**",
            ),
        ],
    )
    def test_genuine_landings_produce_a_usable_pattern(self, login, landing, expected):
        pattern = _derive_post_login_pattern(login, landing)
        assert pattern == expected
        # The pattern must actually fire on the URL the human lands on …
        assert _matches(landing, pattern)
        # … and must not fire on the login page (that would auto-resolve early).
        assert not _matches(login, pattern)

    def test_bare_host_landing_is_parsed_not_mangled(self):
        """A scheme-less URL must not put the host in `scheme`."""
        assert _derive_post_login_pattern("https://app.test/login", "app.test/home") == (
            "http://app.test/home**"
        )


def test_auth_redirect_pattern_catches_session_expiry_pages():
    """A dead cookie must fail health, even when the portal says 200.

    Regression: the pattern only listed login-ish paths, so ICICI's
    /session-expire — served with HTTP 200, no auth word anywhere in the path —
    satisfied both expect_status and the redirect check. The session reported
    HEALTHY for 41 minutes while the browser sat on "Your session has expired",
    and because the controller only opens a HITL step on AUTH_FAIL, the human
    had no "Mark as Resolved" button to recover with.
    """
    import re

    from noui_core.compile.login import compile_login_bundle

    res = compile_login_bundle(
        session_id="s",
        bundle={
            "recording_mode": "login",
            "url_events": [
                {"from_url": "", "to_url": "https://bank.test/login-page"},
                {
                    "from_url": "https://bank.test/login-page",
                    "to_url": "https://bank.test/dashboard",
                },
            ],
            "click_events": [],
            "har": {"log": {"entries": []}},
            "cookies": [],
        },
        name="bank",
        login_url="https://bank.test/login-page",
        manual_takeover=True,
    )
    check = res["application_draft"]["keepalive_config"]["health_checks"][0]
    assert check["type"] == "url_check"
    pattern = check["auth_redirect_pattern"]

    for expired in (
        "/session-expire",
        "/session-expired",
        "/session-timeout",
        "/expired",
        "/logout",
    ):
        assert re.search(pattern, f"https://bank.test{expired}", re.I), expired
    # The authenticated page must still pass.
    assert not re.search(pattern, "https://bank.test/dashboard", re.I)
