"""Tests for noui_core.capture.split — merged login+workflow bundle splitter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.capture.split import find_login_boundary, split_bundle

# ISO-8601 UTC timestamps — the real bundle format, lexicographically comparable.
T = {k: f"2026-01-01T00:00:0{k}.000Z" for k in range(8)}


def _click(ts: str, field_role: str | None = None, tag: str = "input") -> dict:
    return {"timestamp": ts, "field_role": field_role, "tag_name": tag, "event_type": "input"}


def _url(ts: str, from_url: str, to_url: str) -> dict:
    return {"timestamp": ts, "from_url": from_url, "to_url": to_url}


def _entry(ts: str, method: str, url: str) -> dict:
    return {"startedDateTime": ts, "request": {"method": method, "url": url}, "response": {}}


def _merged_bundle() -> dict:
    """Login (username→password→submit→land on /dashboard) then a workflow."""
    return {
        "session_id": "sess-1234abcd",
        "recording_mode": "login",
        "started_at": T[0],
        "stopped_at": T[7],
        "cookies": [{"name": "session", "domain": "app.example.com"}],
        "click_events": [
            _click(T[1], "username"),
            _click(T[2], "password"),
            _click(T[3], None, tag="button"),  # submit
            _click(T[5], None, tag="a"),  # workflow interaction
        ],
        "url_events": [
            _url(T[0], "about:blank", "https://app.example.com/login"),
            _url(T[2], "https://app.example.com/login", "https://app.example.com/enterpassword"),
            _url(
                T[4], "https://app.example.com/enterpassword", "https://app.example.com/dashboard"
            ),
            _url(T[6], "https://app.example.com/dashboard", "https://app.example.com/reports"),
        ],
        "har": {
            "log": {
                "entries": [
                    _entry(T[0], "GET", "https://app.example.com/login"),
                    _entry(T[3], "POST", "https://app.example.com/login"),  # credential submit
                    _entry(T[5], "GET", "https://app.example.com/api/dashboard"),
                    _entry(T[7], "GET", "https://app.example.com/api/reports"),
                ]
            }
        },
    }


class TestBoundary:
    def test_boundary_is_first_stable_nav_after_last_credential(self) -> None:
        # last credential interaction is the password at T[2]; first stable nav
        # after that is /enterpassword -> /dashboard at T[4].
        assert find_login_boundary(_merged_bundle()) == T[4]

    def test_no_credential_fields_returns_none(self) -> None:
        b = _merged_bundle()
        for c in b["click_events"]:
            c["field_role"] = None
        assert find_login_boundary(b) is None

    def test_no_nav_after_credentials_falls_back_to_last_credential(self) -> None:
        b = _merged_bundle()
        b["url_events"] = [u for u in b["url_events"] if u["timestamp"] <= T[2]]
        assert find_login_boundary(b) == T[2]


class TestSplit:
    def test_workflow_only_bundle_returns_none(self) -> None:
        b = _merged_bundle()
        for c in b["click_events"]:
            c["field_role"] = None
        assert split_bundle(b) is None

    def test_login_slice_keeps_credentials_and_login_request(self) -> None:
        login, _ = split_bundle(_merged_bundle())
        roles = {c.get("field_role") for c in login["click_events"]}
        assert {"username", "password"} <= roles
        login_urls = [e["request"]["url"] for e in login["har"]["log"]["entries"]]
        assert "https://app.example.com/login" in login_urls
        # cookies ride with the login slice (the only compiler that reads them)
        assert login["cookies"]

    def test_workflow_slice_excludes_the_login_submit(self) -> None:
        _, workflow = split_bundle(_merged_bundle())
        wf_urls = [e["request"]["url"] for e in workflow["har"]["log"]["entries"]]
        # the credential POST must NOT survive as a candidate operation
        assert all(u.endswith("/api/dashboard") or u.endswith("/api/reports") for u in wf_urls)
        assert "https://app.example.com/login" not in wf_urls
        assert not workflow.get("cookies")

    def test_slices_partition_events_without_overlap(self) -> None:
        b = _merged_bundle()
        login, workflow = split_bundle(b)
        total_clicks = len(login["click_events"]) + len(workflow["click_events"])
        total_urls = len(login["url_events"]) + len(workflow["url_events"])
        total_entries = len(login["har"]["log"]["entries"]) + len(workflow["har"]["log"]["entries"])
        assert total_clicks == len(b["click_events"])
        assert total_urls == len(b["url_events"])
        assert total_entries == len(b["har"]["log"]["entries"])

    def test_passthrough_metadata_preserved(self) -> None:
        login, workflow = split_bundle(_merged_bundle())
        assert login["session_id"] == workflow["session_id"] == "sess-1234abcd"


class TestSeqOrdering:
    """`seq` decides the boundary when the bundle carries it — timestamps place a
    debounced credential fill up to 500ms late, which can drag the boundary past
    the landing nav and hand the login's own submit to the workflow slice."""

    @staticmethod
    def _numbered() -> dict:
        """The merged bundle, with the password fill's TIMESTAMP flushed late.

        The 500ms debounce stamps the password at T[5] — after the landing nav at
        T[4] — while `seq` records that the human typed it before submitting.
        """
        b = _merged_bundle()
        order = [
            (b["url_events"][0], 1),  # → /login
            (b["click_events"][0], 2),  # username
            (b["url_events"][1], 3),  # → /enterpassword
            (b["click_events"][1], 4),  # password (timestamp lands at T[5])
            (b["click_events"][2], 5),  # submit
            (b["url_events"][2], 6),  # → /dashboard   ← the boundary
            (b["url_events"][3], 7),  # → /reports
            (b["click_events"][3], 8),  # workflow interaction
        ]
        for ev, seq in order:
            ev["seq"] = seq
        b["click_events"][1]["timestamp"] = T[5]
        return b

    def test_boundary_uses_seq_not_the_late_flush_timestamp(self) -> None:
        # On timestamps alone the last credential is T[5], past every nav, so the
        # boundary would collapse onto the fill itself.
        assert find_login_boundary(self._numbered()) == T[4]

    def test_login_slice_keeps_the_late_stamped_password_fill(self) -> None:
        login, workflow = split_bundle(self._numbered())
        assert "password" in {c.get("field_role") for c in login["click_events"]}
        assert "password" not in {c.get("field_role") for c in workflow["click_events"]}

    def test_workflow_slice_still_excludes_the_login_submit(self) -> None:
        _, workflow = split_bundle(self._numbered())
        wf_urls = [e["request"]["url"] for e in workflow["har"]["log"]["entries"]]
        assert "https://app.example.com/login" not in wf_urls

    def test_slices_still_partition_without_overlap(self) -> None:
        b = self._numbered()
        login, workflow = split_bundle(b)
        assert len(login["click_events"]) + len(workflow["click_events"]) == len(b["click_events"])
        assert len(login["url_events"]) + len(workflow["url_events"]) == len(b["url_events"])

    def test_partially_numbered_bundle_falls_back_to_timestamps(self) -> None:
        # One un-numbered event means seq is not a total order for this bundle.
        b = self._numbered()
        b["click_events"][1]["timestamp"] = T[2]  # undo the late flush
        del b["url_events"][0]["seq"]
        assert find_login_boundary(b) == T[4]


class TestRealFixture:
    """Sanity check against a real captured bundle (recorded recording_mode=login)."""

    _FIXTURE = (
        _NOUI_ROOT / "skills" / "noui" / "workbench" / "bundles" / "expedia-final-92cb644d.json"
    )

    def test_real_bundle_splits_without_error(self) -> None:
        if not self._FIXTURE.exists():
            return  # fixture optional
        bundle = json.loads(self._FIXTURE.read_text())
        parts = split_bundle(bundle)
        assert parts is not None, "expedia capture has username/password → a login segment"
        login, workflow = parts
        # login slice must retain the credential events and the login-page traffic
        assert any(c.get("field_role") in ("username", "password") for c in login["click_events"])
        # partition holds
        assert len(login["har"]["log"]["entries"]) + len(workflow["har"]["log"]["entries"]) == len(
            bundle["har"]["log"]["entries"]
        )
