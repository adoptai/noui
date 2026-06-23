"""Tests for compiler/recording/bundle_adapter.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.capture.bundle import (
    click_payloads,
    count_sensitive_unredacted,
    har_log,
    url_payloads,
    validate_bundle,
)


def _bundle(**overrides):
    base = {
        "session_id": "tabby-sess",
        "recording_mode": "login",
        "started_at": "2026-06-15T00:00:00Z",
        "stopped_at": "2026-06-15T00:05:00Z",
        "har": {"log": {"version": "1.2", "entries": [{"a": 1}]}},
        "click_events": [
            {
                "event_type": "input",
                "tag_name": "INPUT",
                "selector": "#pwd",
                "url": "https://x.com/login",
                "value": "[REDACTED]",
                "field_role": "password",
                "is_redacted": True,
                "timestamp": "2026-06-15T00:01:00Z",
            }
        ],
        "url_events": [
            {"from_url": "https://x.com/login", "to_url": "https://x.com/home", "timestamp": "t"}
        ],
    }
    base.update(overrides)
    return base


class TestValidate:
    def test_returns_mode(self):
        assert validate_bundle(_bundle()) == "login"

    def test_rejects_bad_mode(self):
        with pytest.raises(ValueError):
            validate_bundle(_bundle(recording_mode="nope"))

    def test_rejects_missing_har(self):
        with pytest.raises(ValueError):
            validate_bundle(_bundle(har={}))


class TestClickPayloads:
    def test_injects_session_and_projects_fields(self):
        out = click_payloads(_bundle(), "noui-1", "login")
        assert len(out) == 1
        p = out[0]
        assert p["session_id"] == "noui-1"
        assert p["session_type"] == "login"
        assert p["selector"] == "#pwd"
        assert p["field_role"] == "password"
        assert p["is_redacted"] is True
        # None/unknown fields are not emitted
        assert "href" not in p

    def test_skips_events_without_tag_name(self):
        b = _bundle(click_events=[{"event_type": "click", "selector": ".x"}])
        assert click_payloads(b, "noui-1", "login") == []


class TestUrlPayloads:
    def test_maps_transitions(self):
        out = url_payloads(_bundle(), "noui-1", "login")
        assert out == [
            {
                "session_id": "noui-1",
                "session_type": "login",
                "from_url": "https://x.com/login",
                "to_url": "https://x.com/home",
            }
        ]

    def test_skips_without_to_url(self):
        b = _bundle(url_events=[{"from_url": "a"}])
        assert url_payloads(b, "noui-1", "login") == []


class TestHarAndCompliance:
    def test_har_log_passthrough(self):
        assert har_log(_bundle())["log"]["version"] == "1.2"

    def test_no_leak_when_redacted(self):
        assert count_sensitive_unredacted(_bundle()) == 0

    def test_detects_unredacted_secret(self):
        b = _bundle(
            click_events=[
                {
                    "tag_name": "INPUT",
                    "field_role": "password",
                    "is_redacted": False,
                    "value": "hunter2",
                }
            ]
        )
        assert count_sensitive_unredacted(b) == 1
