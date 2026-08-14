"""The splitter says what it saw, so a wrong answer is diagnosable.

The split turns on ONE thing — whether an interaction was tagged as a credential
field — and when that tagging fails the bundle is declared login-free however
plainly its URL timeline shows a sign-in. A member who HAD recorded a login was
asked to record it again, with nothing anywhere explaining why.
"""

from noui_core.capture.split import split_diagnosis

BASE = "https://retailnetbanking.icici.bank.in"


def test_it_names_the_boundary_when_a_login_is_found():
    bundle = {
        "click_events": [
            {"seq": 1, "field_role": "username", "timestamp": "2026-08-10T10:00:00Z"},
            {"seq": 2, "field_role": "password", "timestamp": "2026-08-10T10:00:05Z"},
        ],
        "url_events": [
            {"seq": 3, "to_url": f"{BASE}/overview", "timestamp": "2026-08-10T10:00:06Z"}
        ],
    }
    out = split_diagnosis(bundle)
    assert "login ends at seq=" in out
    assert "2 credential interaction(s)" in out


def test_it_explains_a_sign_in_it_could_not_slice():
    """The ICICI case: the login is plainly in the timeline and untagged."""
    bundle = {
        "click_events": [{"seq": 2, "text": "Login"}, {"seq": 4, "text": "Cards"}],
        "url_events": [
            {"seq": 1, "to_url": f"{BASE}/login-page"},
            {"seq": 3, "to_url": f"{BASE}/overview"},
        ],
    }
    out = split_diagnosis(bundle)
    assert "NO login segment" in out
    assert "0 tagged as credential fields" in out
    # and it points at the evidence that contradicts the conclusion
    assert "look like a sign-in" in out
    assert "login-page" in out
    assert "virtual keyboard" in out


def test_it_reports_the_field_roles_it_did_see():
    bundle = {
        "click_events": [{"seq": 1, "field_role": "search"}],
        "url_events": [{"seq": 2, "to_url": f"{BASE}/overview"}],
    }
    out = split_diagnosis(bundle)
    assert "field roles seen: search" in out


def test_a_workflow_only_capture_is_reported_plainly():
    # No sign-in urls either: nothing surprising, nothing to explain away.
    bundle = {
        "click_events": [{"seq": 1, "text": "Cards"}],
        "url_events": [{"seq": 2, "to_url": f"{BASE}/overview"}],
    }
    out = split_diagnosis(bundle)
    assert "NO login segment" in out
    assert "look like a sign-in" not in out


def test_a_deep_app_route_containing_auth_is_not_the_login_exit():
    """ICICI's statement portal is infinity.icici.bank.in/corp/AuthenticationController.

    Its path contains "auth", so it read as a sign-in page, and the LAST exit
    from a sign-in page fell at the end of the workflow — slicing the entire
    statement journey into the login half and leaving the workflow half empty.
    A sign-in bounces within its own host; a different host reached by clicking
    around inside the app is the app.
    """
    from noui_core.capture.split import _boundary_event

    bundle = {
        "click_events": [{"seq": 2, "text": "Credit Cards"}],
        "url_events": [
            {"seq": 1, "from_url": "about:blank", "to_url": f"{BASE}/login-page"},
            {"seq": 3, "from_url": f"{BASE}/login-page", "to_url": f"{BASE}/overview"},
            {
                "seq": 9,
                "from_url": f"{BASE}/credit-card",
                "to_url": "https://infinity.icici.bank.in/corp/AuthenticationController",
            },
            {
                "seq": 22,
                "from_url": "https://infinity.icici.bank.in/corp/AuthenticationController",
                "to_url": "https://infinity.icici.bank.in/corp/Finacle",
            },
        ],
    }
    assert _boundary_event(bundle)["seq"] == 3


def test_an_otp_bounce_on_the_login_host_still_extends_the_login():
    # The reason the LAST exit is taken: a sign-in commonly returns to an OTP or
    # consent screen before finally landing.
    from noui_core.capture.split import _boundary_event

    bundle = {
        "click_events": [{"seq": 2, "text": "Continue"}],
        "url_events": [
            {"seq": 1, "from_url": "about:blank", "to_url": f"{BASE}/login-page"},
            {"seq": 3, "from_url": f"{BASE}/login-page", "to_url": f"{BASE}/interstitial"},
            {"seq": 5, "from_url": f"{BASE}/interstitial", "to_url": f"{BASE}/otp"},
            {"seq": 7, "from_url": f"{BASE}/otp", "to_url": f"{BASE}/overview"},
        ],
    }
    assert _boundary_event(bundle)["seq"] == 7
