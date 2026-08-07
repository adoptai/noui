"""The login DSL must follow INTERACTION order, not recording order.

Tabby's injected recorder debounces `input` by 500ms. A human who fills a field
and clicks submit inside that window flushes the input AFTER the click, so the
bundle's list order — and its `timestamp`, which records that flush — put "click
Sign in" before the fill it submitted. A login DSL compiled in that order clicks
submit on an empty form. `seq`, assigned at interaction time, is the order the
compiler must use; `timestamp` keeps its flush meaning for existing consumers.
"""

from __future__ import annotations

import sys
from pathlib import Path

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from noui_core.compile.login_assets import generate

_SESSION = {"id": "sess-1", "app_name": "acme", "login_url": "https://acme.example.com/login"}
_URLS = [{"from_url": "", "to_url": "https://acme.example.com/login", "seq": 1}]


def _fill(seq: int, name: str, role: str, ts: str) -> dict:
    return {
        "event_type": "input",
        "tag_name": "INPUT",
        "element_id": name,
        "field_name": name,
        "field_role": role,
        "input_type": "password" if role == "password" else "text",
        "selector": f"#{name}",
        "url": "https://acme.example.com/login",
        "value": "[REDACTED]",
        "is_redacted": True,
        "seq": seq,
        "timestamp": ts,
    }


def _submit_click(seq: int, ts: str) -> dict:
    return {
        "event_type": "click",
        "tag_name": "BUTTON",
        "element_id": "signin",
        "text_content": "Sign in",
        "selector": "#signin",
        "url": "https://acme.example.com/login",
        "seq": seq,
        "timestamp": ts,
    }


def _actions(clicks: list[dict]) -> list[str]:
    result = generate(_SESSION, clicks, _URLS, manual_credentials=False)
    return [s["action"] for s in result["application_draft"]["login_config"]["steps"]]


def _fill_selectors(clicks: list[dict]) -> list[str]:
    result = generate(_SESSION, clicks, _URLS, manual_credentials=False)
    return [
        s.get("selector")
        for s in result["application_draft"]["login_config"]["steps"]
        if s["action"] in ("fill", "click")
    ]


class TestDebouncedFillOrdering:
    # Arrival order: the click beat the 500ms input flush, and the flush stamped
    # the fill 400ms AFTER the click it preceded.
    _AS_RECORDED = [
        _fill(1, "username", "username", "2026-01-01T00:00:00.100Z"),
        _submit_click(3, "2026-01-01T00:00:01.000Z"),
        _fill(2, "password", "password", "2026-01-01T00:00:01.400Z"),
    ]

    def test_fills_precede_the_submit_click(self) -> None:
        assert _actions(self._AS_RECORDED) == ["goto", "fill", "fill", "click"]

    def test_password_fill_is_not_stranded_after_the_click(self) -> None:
        assert _fill_selectors(self._AS_RECORDED) == ["#username", "#password", "#signin"]

    def test_timestamp_order_would_have_compiled_the_click_first(self) -> None:
        # Guards the premise: sorting this bundle by timestamp — or trusting its
        # list order — puts the submit ahead of the password it submitted.
        by_ts = sorted(self._AS_RECORDED, key=lambda e: e["timestamp"])
        assert [e["event_type"] for e in by_ts] == ["input", "click", "input"]


class TestUnnumberedBundles:
    """Bundles recorded before `seq` existed keep the previous behaviour."""

    def test_recording_order_is_preserved(self) -> None:
        clicks = [
            {**_fill(0, "username", "username", "2026-01-01T00:00:00.100Z"), "seq": None},
            {**_fill(0, "password", "password", "2026-01-01T00:00:00.500Z"), "seq": None},
            {**_submit_click(0, "2026-01-01T00:00:01.000Z"), "seq": None},
        ]
        for c in clicks:
            del c["seq"]
        assert _actions(clicks) == ["goto", "fill", "fill", "click"]
