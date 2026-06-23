"""Behavioural tests for the generated noui_runtime/execute.py adapter.

The adapter is emitted as a source *string* by
``noui_core.activate.execute_adapter.generate_execute_adapter``. To test runtime
behaviour we exec that string into a fresh module namespace and drive
``execute_fetch`` with a fake ``httpx.AsyncClient``. Coroutines are run via
``asyncio.run`` so the suite needs no pytest-asyncio plugin.

Covers:
- B2: a "no healthy session" 404 (and a 409) surfaces the actionable
  "run `tabby session ensure`" error, not an opaque message; an *upstream* 404
  (wrapped in a 200 body, or a bare 404 without the session marker) does not.
- B5: with NOUI_EXECUTE_WARMUP enabled, a no-session response triggers exactly
  one POST /credentials/request warm-up and one execute retry; disabled by
  default; never loops.
"""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.activate.execute_adapter import generate_execute_adapter

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, *, text: str = "", json_body: Any = None) -> None:
        self.status_code = status_code
        self.text = text
        self._json = json_body

    def json(self) -> Any:
        return self._json


class _FakeAsyncClient:
    """Records every POST and replays a scripted sequence of responses.

    ``responses`` maps a URL suffix to a list of responses popped in order.
    """

    calls: list[str] = []
    responses: dict[str, list[_FakeResponse]] = {}

    def __init__(self, *_a: Any, **_k: Any) -> None: ...

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, url: str, **_kwargs: Any) -> _FakeResponse:
        type(self).calls.append(url)
        for suffix, queue in type(self).responses.items():
            if url.endswith(suffix):
                return queue.pop(0)
        raise AssertionError(f"unexpected POST {url}")


def _load_adapter(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Exec the generated execute.py into a module with a stubbed httpx + token."""
    src = generate_execute_adapter()
    module = types.ModuleType("noui_runtime_execute_under_test")

    # The generated module runs `_load_env()` at import time, which references
    # __file__. Point it at a nonexistent path so the dotenv walk is a harmless
    # no-op, and pre-clear NOUI_ENV_FILE so it can't pick up a stray .env.
    module.__dict__["__file__"] = str(_NOUI_ROOT / "noui_runtime" / "execute.py")
    monkeypatch.delenv("NOUI_ENV_FILE", raising=False)

    exec(compile(src, "noui_runtime/execute.py", "exec"), module.__dict__)  # noqa: S102

    # The generated module does `import httpx` at top level — overwrite with a fake.
    fake_httpx = types.SimpleNamespace(
        AsyncClient=_FakeAsyncClient,
        Response=_FakeResponse,
        HTTPError=Exception,
    )
    module.__dict__["httpx"] = fake_httpx

    # Reset the recorder for each load.
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.responses = {}

    # execute_fetch resolves its bearer via _get_tabby_bearer (which wraps the
    # agent_token / platform_jwt exchange + per-mode caching). Stub that single
    # entry point so these behavioural tests stay independent of the auth machinery.
    async def _fake_bearer() -> str:
        return "AGENT_TOKEN"

    monkeypatch.setattr(module, "_get_tabby_bearer", _fake_bearer)
    monkeypatch.setenv("TABBY_API_URL", "http://localhost:8000")
    return module


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _fetch(module: types.ModuleType, **kw: Any) -> Coroutine[Any, Any, Any]:
    return module.execute_fetch("my-profile", "https://api.example.com/x", **kw)


def _execute_count() -> int:
    return sum(u.endswith("/execute/fetch") for u in _FakeAsyncClient.calls)


def _warmup_count() -> int:
    return sum(u.endswith("/credentials/request") for u in _FakeAsyncClient.calls)


# ---------------------------------------------------------------------------
# B2 — 404 / 409 actionability
# ---------------------------------------------------------------------------


class TestNoSessionErrors:
    @pytest.mark.parametrize(
        "status,text",
        [
            (404, '{"message":"No healthy session for app"}'),
            (404, '{"detail":"No active profile for slug"}'),
            (409, '{"message":"session has no pod_name"}'),
        ],
    )
    def test_no_session_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch, status: int, text: str
    ) -> None:
        module = _load_adapter(monkeypatch)
        _FakeAsyncClient.responses = {"/execute/fetch": [_FakeResponse(status, text=text)]}
        with pytest.raises(RuntimeError) as exc:
            _run(_fetch(module))
        assert "session ensure" in str(exc.value)
        assert "my-profile" in str(exc.value)

    def test_bare_404_without_marker_is_not_misclassified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 404 whose body is NOT a Tabby session message stays a generic error."""
        module = _load_adapter(monkeypatch)
        _FakeAsyncClient.responses = {
            "/execute/fetch": [_FakeResponse(404, text='{"error":"route not found"}')]
        }
        with pytest.raises(RuntimeError) as exc:
            _run(_fetch(module))
        assert "session ensure" not in str(exc.value)
        assert "404" in str(exc.value)

    def test_upstream_404_wrapped_in_200_is_not_a_session_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The target returning 404 arrives as a 200 {status:404} body, not a Tabby 404."""
        module = _load_adapter(monkeypatch)
        _FakeAsyncClient.responses = {
            "/execute/fetch": [_FakeResponse(200, json_body={"status": 404, "body": "Not Found"})]
        }
        with pytest.raises(RuntimeError) as exc:
            _run(_fetch(module))
        assert "session ensure" not in str(exc.value)
        assert "-> 404" in str(exc.value)


# ---------------------------------------------------------------------------
# B5 — opt-in warm-up retry
# ---------------------------------------------------------------------------


class TestWarmUpRetry:
    def test_disabled_by_default_no_warmup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _load_adapter(monkeypatch)
        monkeypatch.delenv("NOUI_EXECUTE_WARMUP", raising=False)
        _FakeAsyncClient.responses = {
            "/execute/fetch": [_FakeResponse(404, text="No healthy session")]
        }
        with pytest.raises(RuntimeError):
            _run(_fetch(module))
        assert _warmup_count() == 0
        assert _execute_count() == 1

    def test_enabled_warms_up_then_retries_once_and_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _load_adapter(monkeypatch)
        monkeypatch.setenv("NOUI_EXECUTE_WARMUP", "1")
        _FakeAsyncClient.responses = {
            "/execute/fetch": [
                _FakeResponse(404, text="No healthy session"),  # first attempt: cold
                _FakeResponse(200, json_body={"status": 200, "body": '{"ok": true}'}),  # retry
            ],
            "/credentials/request": [_FakeResponse(200, json_body={"cookies": []})],
        }
        result = _run(_fetch(module))
        assert result == {"ok": True}
        assert _execute_count() == 2
        assert _warmup_count() == 1

    def test_enabled_retries_exactly_once_no_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the retry still has no session, error out — never loop."""
        module = _load_adapter(monkeypatch)
        monkeypatch.setenv("NOUI_EXECUTE_WARMUP", "true")
        _FakeAsyncClient.responses = {
            "/execute/fetch": [
                _FakeResponse(404, text="No healthy session"),
                _FakeResponse(404, text="No healthy session"),
            ],
            "/credentials/request": [_FakeResponse(202, json_body={})],
        }
        with pytest.raises(RuntimeError) as exc:
            _run(_fetch(module))
        assert "session ensure" in str(exc.value)
        assert _execute_count() == 2  # exactly one retry
        assert _warmup_count() == 1

    def test_warmup_failure_does_not_mask_original_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed warm-up (5xx) must not trigger a retry, and the actionable error stands."""
        module = _load_adapter(monkeypatch)
        monkeypatch.setenv("NOUI_EXECUTE_WARMUP", "1")
        _FakeAsyncClient.responses = {
            "/execute/fetch": [_FakeResponse(404, text="No healthy session")],
            "/credentials/request": [_FakeResponse(503, text="rescale failed")],
        }
        with pytest.raises(RuntimeError) as exc:
            _run(_fetch(module))
        assert "session ensure" in str(exc.value)
        assert _execute_count() == 1  # 5xx warm-up → no retry
        assert _warmup_count() == 1
