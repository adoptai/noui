"""NoUI classifies captures from content — Tabby's recording_mode is ignored.

Regression cover for the harness failure where a workflow recording provisioned
with ``--mode workflow`` came back stamped ``recording_mode: "login"`` (every
warm-pool session does) and was therefore registered as a login App Template
instead of compiled into a skill. The HAR was fine; only the label lied.
"""

from __future__ import annotations

import pytest
from noui_core.capture.bundle import validate_bundle
from noui_core.capture.classify import bundle_signals, classify_bundle

T = {k: f"2026-01-01T00:00:{k:02d}.000Z" for k in range(12)}


def _entry(ts: str, url: str, mime: str = "application/json") -> dict:
    return {
        "startedDateTime": ts,
        "request": {"method": "GET", "url": url},
        "response": {"status": 200, "content": {"mimeType": mime, "size": 12}},
    }


def _bundle(*, stamped: str = "login", clicks=None, urls=None, entries=None) -> dict:
    return {
        "session_id": "sess-1",
        "recording_mode": stamped,
        "click_events": clicks or [],
        "url_events": urls or [],
        "har": {"log": {"entries": entries or []}},
    }


def _login_clicks() -> list[dict]:
    return [
        {"timestamp": T[1], "tag_name": "input", "field_role": "username"},
        {"timestamp": T[2], "tag_name": "input", "field_role": "password", "is_redacted": True},
    ]


class TestWorkflowMisstampedAsLogin:
    """The exact production case: a workflow capture Tabby labelled 'login'."""

    def _workflow_capture(self) -> dict:
        return _bundle(
            stamped="login",  # warm-pool session: always reports 'login'
            clicks=[{"timestamp": T[3], "tag_name": "button", "text_content": "Listings"}],
            urls=[
                {
                    "timestamp": T[2],
                    "from_url": "https://airbnb.test/",
                    "to_url": "https://airbnb.test/hosting/listings",
                }
            ],
            entries=[_entry(T[4], "https://airbnb.test/api/v3/UnifiedListOfListingsQuery")],
        )

    def test_classified_as_workflow_despite_the_stamp(self):
        assert classify_bundle(self._workflow_capture()) == "workflow"

    def test_stamp_value_never_changes_the_answer(self):
        bundle = self._workflow_capture()
        answers = set()
        for stamped in ("login", "workflow", "combined", None, "nonsense"):
            bundle["recording_mode"] = stamped
            answers.add(classify_bundle(bundle))
        assert answers == {"workflow"}

    def test_stamp_may_be_absent_entirely(self):
        bundle = self._workflow_capture()
        del bundle["recording_mode"]
        assert classify_bundle(bundle) == "workflow"


class TestLogin:
    def test_credential_entry_then_landing_is_login(self):
        bundle = _bundle(
            clicks=_login_clicks(),
            urls=[
                {
                    "timestamp": T[3],
                    "from_url": "https://app.test/login",
                    "to_url": "https://app.test/home",
                }
            ],
            entries=[_entry(T[4], "https://app.test/api/me")],
        )
        # Post-login dashboard XHRs alone must NOT make this look 'combined' —
        # otherwise a plain login capture gets wrongly split at import.
        assert classify_bundle(bundle) == "login"

    def test_many_post_login_api_calls_still_login_without_driving(self):
        bundle = _bundle(
            clicks=_login_clicks(),
            urls=[
                {
                    "timestamp": T[3],
                    "from_url": "https://app.test/login",
                    "to_url": "https://app.test/home",
                }
            ],
            entries=[_entry(T[i], f"https://app.test/api/widget/{i}") for i in (4, 5, 6, 7)],
        )
        assert classify_bundle(bundle) == "login"


class TestRealLoginCaptureShapes:
    """Shapes taken from bundles NoUI actually captured — both must stay 'login'.

    A false 'combined' is the expensive mistake: it splits a pure login capture
    and registers a truncated App Template.
    """

    def test_login_that_settles_through_one_final_bounce(self):
        """Expedia: /enterpassword → /onboarding (boundary) → / , 37 XHRs, no clicks."""
        bundle = _bundle(
            clicks=_login_clicks(),
            urls=[
                {
                    "timestamp": T[3],
                    "from_url": "https://www.expedia.test/enterpassword?redirectTo=%2F",
                    "to_url": "https://www.expedia.test/onboarding?originUrl=%2F",
                },
                {
                    "timestamp": T[4],
                    "from_url": "https://www.expedia.test/onboarding?originUrl=%2F",
                    "to_url": "https://www.expedia.test/?challengeReferer=noref",
                },
            ],
            entries=[
                _entry(T[i], f"https://www.expedia.test/api/bootstrap/{i}") for i in (5, 6, 7)
            ],
        )
        assert classify_bundle(bundle) == "login"

    def test_login_landing_on_a_busy_dashboard(self):
        """Airbnb: lands on a search page that fires 21 XHRs, no post-login events."""
        bundle = _bundle(
            clicks=_login_clicks(),
            urls=[
                {
                    "timestamp": T[3],
                    "from_url": "https://www.airbnb.test/",
                    "to_url": "https://www.airbnb.test/s/San-Francisco--CA/homes",
                }
            ],
            entries=[
                _entry(T[i], f"https://www.airbnb.test/api/v3/Query{i}") for i in (4, 5, 6, 7)
            ],
        )
        assert classify_bundle(bundle) == "login"


class TestCombined:
    def test_login_then_driving_is_combined(self):
        bundle = _bundle(
            clicks=[
                *_login_clicks(),
                {"timestamp": T[6], "tag_name": "button", "text_content": "Search"},
            ],
            urls=[
                {
                    "timestamp": T[3],
                    "from_url": "https://app.test/login",
                    "to_url": "https://app.test/home",
                },
                {
                    "timestamp": T[5],
                    "from_url": "https://app.test/home",
                    "to_url": "https://app.test/reports",
                },
            ],
            entries=[_entry(T[7], "https://app.test/api/reports")],
        )
        assert classify_bundle(bundle) == "combined"


class TestAutopilotBundles:
    def test_synthesized_workflow_bundle(self):
        """capture/autopilot builds bundles with no timestamps and no credentials."""
        bundle = {
            "recording_mode": "workflow",
            "har": {"log": {"entries": [_entry(T[1], "https://app.test/api/search")]}},
            "click_events": [{"event_type": "click", "selector": "#go"}],
            "url_events": [{"from_url": "", "to_url": "https://app.test/search"}],
        }
        assert classify_bundle(bundle) == "workflow"


class TestSignals:
    def test_signals_expose_the_reasoning(self):
        sig = bundle_signals(
            _bundle(
                clicks=[
                    *_login_clicks(),
                    {"timestamp": T[6], "tag_name": "a", "text_content": "Reports"},
                ],
                urls=[
                    {
                        "timestamp": T[3],
                        "from_url": "https://app.test/login",
                        "to_url": "https://app.test/home",
                    },
                    {
                        "timestamp": T[5],
                        "from_url": "https://app.test/home",
                        "to_url": "https://app.test/reports",
                    },
                ],
                entries=[_entry(T[7], "https://app.test/api/reports")],
            )
        )
        assert sig["credential_events"] == 2
        assert sig["login_boundary"] == T[3]
        assert sig["post_login_clicks"] == 1
        assert sig["post_login_url_events"] == 1
        assert sig["post_login_stable_navs"] == 1
        assert sig["post_login_api_calls"] == 1

    def test_two_stable_navigations_count_as_driving(self):
        """No clicks recorded (some sites swallow them) but the human moved twice."""
        bundle = _bundle(
            clicks=_login_clicks(),
            urls=[
                {
                    "timestamp": T[3],
                    "from_url": "https://app.test/login",
                    "to_url": "https://app.test/home",
                },
                {
                    "timestamp": T[5],
                    "from_url": "https://app.test/home",
                    "to_url": "https://app.test/reports",
                },
                {
                    "timestamp": T[6],
                    "from_url": "https://app.test/reports",
                    "to_url": "https://app.test/reports/42",
                },
            ],
            entries=[_entry(T[7], "https://app.test/api/reports/42")],
        )
        assert bundle_signals(bundle)["post_login_stable_navs"] == 2
        assert classify_bundle(bundle) == "combined"

    def test_analytics_calls_do_not_count_as_api_traffic(self):
        """The count mirrors the compiler's own filter, so noise can't tip a verdict."""
        sig = bundle_signals(
            _bundle(entries=[_entry(T[1], "https://app.test/analytics/collect?x=1")])
        )
        assert sig["api_calls_total"] == 0


class TestValidateBundleIsShapeOnly:
    def test_no_longer_returns_a_mode(self):
        assert validate_bundle(_bundle(entries=[_entry(T[1], "https://app.test/api/x")])) is None

    @pytest.mark.parametrize("stamped", ["nonsense", None, "", "workflow"])
    def test_any_stamp_passes_validation(self, stamped):
        validate_bundle(_bundle(stamped=stamped))

    def test_missing_har_still_rejected(self):
        with pytest.raises(ValueError, match="bundle.har"):
            validate_bundle({"recording_mode": "login", "har": {}})

    def test_non_dict_rejected(self):
        with pytest.raises(ValueError, match="JSON object"):
            validate_bundle([])  # type: ignore[arg-type]


# --- capture-downgrade warning -----------------------------------------------
#
# Tabby gates its workflow-only capture (locator candidates, element state,
# interaction outcomes, downloads, popups) on the pod's recording mode. A pooled
# spare boots as a login recording and adopts its real mode at bind. If that
# regresses the recording still succeeds and still compiles — just from far
# poorer evidence — and the skill misbehaves in a way that looks like a bad
# recording rather than a bug. This makes it say so.

from noui_core.capture.recording import warn_if_capture_was_downgraded  # noqa: E402


def test_warns_when_a_workflow_capture_has_no_workflow_shaped_capture(capsys):
    bundle = {"schema_version": 5, "har": {"log": {"entries": []}}}
    warn_if_capture_was_downgraded(bundle, "workflow")
    assert "recording_mode=workflow" in capsys.readouterr().out


def test_silent_when_the_pod_knew_it_was_a_workflow_recording(capsys):
    bundle = {"schema_version": 5, "download_events": [], "har": {"log": {"entries": []}}}
    warn_if_capture_was_downgraded(bundle, "workflow")
    assert capsys.readouterr().out == ""


def test_silent_for_a_login_capture(capsys):
    bundle = {"schema_version": 5, "har": {"log": {"entries": []}}}
    warn_if_capture_was_downgraded(bundle, "login")
    assert capsys.readouterr().out == ""


def test_silent_for_recordings_made_before_rich_capture_existed(capsys):
    # Nothing to expect from them, so warning would be pure noise.
    warn_if_capture_was_downgraded({"schema_version": 2}, "workflow")
    warn_if_capture_was_downgraded({}, "workflow")
    assert capsys.readouterr().out == ""
