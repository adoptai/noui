"""Pillar 1 — Capture (Autopilot, agent-driven).

The agent drives a profile's authenticated Tabby browser session via
`POST /execute/browser` (har_start → navigate/click/type → har_stop). Because
NoUI issues the commands, it synthesizes the recording bundle from the inline
HAR (returned by har_stop) plus the click/url events it drove — no Tabby-side
change and no server-side drain required. The bundle is the same shape the VNC
path produces, so it feeds the same compiler.

Usage (the agent is the driver):

    ap = AutopilotSession(profile_slug)
    ap.start_capture()
    ap.navigate("https://example.com/search?q=foo")
    ap.click("#result-0")
    bundle = ap.finish()            # har_stop + synthesize
    # → compile.workflow.compile_workflow_bundle(session_id=..., bundle=bundle, ...)
"""

from __future__ import annotations

from typing import Any

from noui_core import tabby_client
from noui_core.capture import recording


def _extract_har(data: dict) -> dict:
    """Normalize the har_stop payload into a HAR 1.2 object ({"log": {...}})."""
    if not isinstance(data, dict):
        return {}
    if "log" in data:
        return data
    if isinstance(data.get("har"), dict):
        return data["har"]
    return data


class AutopilotSession:
    """Drive a profile's Tabby browser session and synthesize a capture bundle."""

    def __init__(
        self,
        profile_slug: str,
        token: str = "",
        *,
        default_timeout_ms: int = 30000,
    ) -> None:
        self.profile_slug = profile_slug
        self.token = token or recording.resolve_agent_token()
        self.default_timeout_ms = default_timeout_ms
        self._clicks: list[dict[str, Any]] = []
        self._urls: list[dict[str, Any]] = []
        self._current_url = ""
        self.capturing = False

    # ── low-level ────────────────────────────────────────────────────────────
    def _exec(
        self, command: str, params: dict | None = None, timeout_ms: int | None = None
    ) -> dict:
        resp = tabby_client.execute_browser(
            self.profile_slug,
            command,
            params,
            token=self.token,
            timeout_ms=timeout_ms or self.default_timeout_ms,
        )
        return resp.get("data") or {}

    # ── capture lifecycle ─────────────────────────────────────────────────────
    def start_capture(self) -> None:
        self._exec("har_start")
        self.capturing = True

    def stop_capture(self) -> dict:
        """Stop HAR capture and return the HAR 1.2 object."""
        data = self._exec("har_stop")
        self.capturing = False
        return _extract_har(data)

    # ── driving commands (events recorded as issued) ──────────────────────────
    def navigate(self, url: str, timeout_ms: int | None = None) -> dict:
        data = self._exec("navigate", {"url": url}, timeout_ms)
        self._record_url(data.get("url") or url)
        return data

    def click(self, selector: str, timeout_ms: int | None = None) -> dict:
        self._record_click(selector=selector)
        return self._exec("click_element", {"selector": selector}, timeout_ms)

    def click_text(self, text: str, exact: bool = True, timeout_ms: int | None = None) -> dict:
        self._record_click(text=text)
        return self._exec("click_by_text", {"text": text, "exact": exact}, timeout_ms)

    def type(self, selector: str, text: str, timeout_ms: int | None = None) -> dict:
        return self._exec("type_text", {"selector": selector, "text": text}, timeout_ms)

    def press(self, key: str) -> dict:
        return self._exec("press_key", {"key": key})

    def wait_for(self, selector: str, timeout_ms: int | None = None) -> dict:
        return self._exec("wait_for_selector", {"selector": selector}, timeout_ms)

    def scroll(self, dx: int = 0, dy: int = 300) -> dict:
        return self._exec("scroll_page", {"dx": dx, "dy": dy})

    def page_summary(self) -> dict:
        return self._exec("get_page_summary")

    def screenshot(self) -> dict:
        return self._exec("screenshot")

    # ── bundle synthesis ──────────────────────────────────────────────────────
    def _record_url(self, to_url: str) -> None:
        if to_url and to_url != self._current_url:
            self._urls.append({"from_url": self._current_url, "to_url": to_url})
            self._current_url = to_url

    def _record_click(self, **info: Any) -> None:
        self._clicks.append({"event_type": "click", **info})

    def build_bundle(self, har: dict) -> dict:
        """Assemble a workflow recording bundle from a captured HAR + driven events."""
        return {
            "recording_mode": "workflow",
            "har": har,
            "click_events": list(self._clicks),
            "url_events": list(self._urls),
        }

    def finish(self) -> dict:
        """Stop capture and return the synthesized workflow bundle."""
        har = self.stop_capture()
        return self.build_bundle(har)


# Command names accepted by run_steps() → AutopilotSession methods.
_STEP_DISPATCH = {
    "navigate": lambda ap, s: ap.navigate(s["url"], s.get("timeout_ms")),
    "click": lambda ap, s: ap.click(s["selector"], s.get("timeout_ms")),
    "click_text": lambda ap, s: ap.click_text(s["text"], s.get("exact", True), s.get("timeout_ms")),
    "type": lambda ap, s: ap.type(s["selector"], s["text"], s.get("timeout_ms")),
    "press": lambda ap, s: ap.press(s["key"]),
    "wait_for": lambda ap, s: ap.wait_for(s["selector"], s.get("timeout_ms")),
    "scroll": lambda ap, s: ap.scroll(s.get("dx", 0), s.get("dy", 300)),
}


def run_steps(profile_slug: str, steps: list[dict], *, token: str = "") -> dict:
    """Scripted Autopilot: run a list of `{action, ...}` steps and return the bundle.

    Each step is `{"action": "navigate"|"click"|"click_text"|"type"|"press"|"wait_for"|"scroll", ...}`.
    """
    ap = AutopilotSession(profile_slug, token)
    ap.start_capture()
    for i, step in enumerate(steps):
        action = str(step.get("action") or "")
        fn = _STEP_DISPATCH.get(action)
        if fn is None:
            raise ValueError(
                f"step {i}: unknown action {action!r}; valid: {sorted(_STEP_DISPATCH)}"
            )
        fn(ap, step)
    return ap.finish()
