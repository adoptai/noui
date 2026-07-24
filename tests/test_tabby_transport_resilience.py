"""Transport resilience + diagnosability at the NoUI→Tabby/broker seam.

Regression cover for the harness failure where ``capture_import.py`` died with a
raw ``ConnectionRefusedError`` traceback mid-flow (right after a human had driven
a recording). Two defects: transport errors are ``OSError`` subclasses, so they
escaped every ``except RuntimeError`` handler in the scripts, and the message
named neither the URL nor the auth mode — which got it misdiagnosed as a bearer
problem. Nothing retried, either, so a ~10s upstream blip was fatal.
"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest
from noui_core import tabby_client
from noui_core.config import settings


class _Resp:
    """Minimal urlopen context-manager stand-in."""

    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://tabby.test/x", code, "boom", {}, BytesIO(b'{"detail":"upstream"}')
    )


@pytest.fixture(autouse=True)
def _no_sleep():
    """Collapse the backoff so tests don't actually wait 1-2-4-8-16s."""
    with patch.object(tabby_client.time, "sleep") as slept:
        yield slept


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setattr(settings, "tabby_auth_mode", "broker")
    monkeypatch.setattr(settings, "broker_token", "cap-tok")
    monkeypatch.setattr(
        settings, "tabby_api_host", "http://broker.internal:8000/internal/noui-broker"
    )


class TestTransportErrorsBecomeRuntimeError:
    def test_connection_refused_is_runtime_error(self, broker):
        """The exact harness failure: ECONNREFUSED must not escape as an OSError."""
        err = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with (
            patch.object(tabby_client.urllib.request, "urlopen", side_effect=err),
            pytest.raises(RuntimeError) as exc,
        ):
            tabby_client.get_recording_bundle("sess-1", "cap-tok")
        msg = str(exc.value)
        # Diagnosable: names the host, the path, the auth mode, and rules out auth.
        assert "broker.internal:8000" in msg
        assert "/recording/sessions/sess-1/bundle" in msg
        assert "broker mode" in msg
        assert "not an auth problem" in msg
        assert "401/403" in msg

    def test_runtime_error_is_catchable_by_the_scripts(self, broker):
        """capture_import catches (RuntimeError, ValueError) — this must land there."""
        with (
            patch.object(
                tabby_client.urllib.request, "urlopen", side_effect=ConnectionResetError("reset")
            ),
            pytest.raises((RuntimeError, ValueError)),
        ):
            tabby_client.get_recording_bundle("sess-1", "cap-tok")

    def test_socket_timeout_is_runtime_error(self, broker):
        with (
            patch.object(tabby_client.urllib.request, "urlopen", side_effect=TimeoutError("slow")),
            pytest.raises(RuntimeError, match="Cannot reach"),
        ):
            tabby_client.get_session_status("acme", "cap-tok")

    def test_message_reports_the_retry_budget(self, broker, _no_sleep):
        with (
            patch.object(tabby_client.urllib.request, "urlopen", side_effect=OSError("nope")),
            pytest.raises(RuntimeError, match=r"Retried 6 time\(s\)"),
        ):
            tabby_client.get_recording_bundle("sess-1", "cap-tok")


class TestRetry:
    def test_transport_error_then_success(self, broker, _no_sleep):
        """A blip mid-flow must not throw away a drained bundle."""
        outcomes = [OSError("refused"), OSError("refused"), _Resp({"har": {"log": {}}})]
        with patch.object(tabby_client.urllib.request, "urlopen", side_effect=outcomes) as opened:
            bundle = tabby_client.get_recording_bundle("sess-1", "cap-tok")
        assert bundle == {"har": {"log": {}}}
        assert opened.call_count == 3
        assert _no_sleep.call_count == 2

    def test_backoff_is_exponential(self, broker, _no_sleep):
        outcomes = [OSError("x"), OSError("x"), OSError("x"), _Resp({"har": {}})]
        with patch.object(tabby_client.urllib.request, "urlopen", side_effect=outcomes):
            tabby_client.get_recording_bundle("sess-1", "cap-tok")
        assert [c.args[0] for c in _no_sleep.call_args_list] == [1.0, 2.0, 4.0]

    def test_502_from_broker_is_retried(self, broker, _no_sleep):
        """The broker answers 502 for a transient bearer-resolution failure."""
        outcomes = [_http_error(502), _Resp({"state": "HEALTHY"})]
        with patch.object(tabby_client.urllib.request, "urlopen", side_effect=outcomes) as opened:
            assert tabby_client.get_session_status("acme", "cap-tok")["state"] == "HEALTHY"
        assert opened.call_count == 2

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 409])
    def test_client_errors_are_not_retried(self, broker, code):
        """401/403 are answers, not outages — retrying them just wastes time."""
        with (
            patch.object(
                tabby_client.urllib.request, "urlopen", side_effect=_http_error(code)
            ) as opened,
            pytest.raises(RuntimeError, match=f"HTTP {code}"),
        ):
            tabby_client.get_session_status("acme", "cap-tok")
        assert opened.call_count == 1

    def test_recording_session_creation_is_not_retried(self, broker):
        """Retrying a provision would leak shell apps / warm-pool claims."""
        with (
            patch.object(
                tabby_client.urllib.request, "urlopen", side_effect=OSError("refused")
            ) as opened,
            pytest.raises(RuntimeError, match="Not retried"),
        ):
            tabby_client.create_recording_session("workflow", "https://x.test", "cap-tok")
        assert opened.call_count == 1

    @pytest.mark.parametrize(
        "call",
        [
            lambda: tabby_client.register_app_template({"name": "x"}, "tok"),
            lambda: tabby_client.update_app_template("tid", {"name": "x"}, "tok"),
            lambda: tabby_client.scale_sessions("app-1", 1, "tok"),
            lambda: tabby_client.execute_browser("slug", "navigate", token="tok"),
        ],
    )
    def test_writes_are_not_retried(self, broker, call):
        with (
            patch.object(
                tabby_client.urllib.request, "urlopen", side_effect=OSError("refused")
            ) as opened,
            pytest.raises(RuntimeError),
        ):
            call()
        assert opened.call_count == 1
