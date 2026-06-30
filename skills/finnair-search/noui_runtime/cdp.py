"""NoUI runtime CDP adapter — execute HTTP calls inside Tabby's browser session.

Shared across MCP-server and Skill output formats. Opens a WebSocket to Tabby's
direct CDP endpoint (localhost:9222), locates a page target matching the caller's
domain, and evaluates `fetch(url, {credentials: 'include'})` via Runtime.evaluate.

Why this module exists:
  - Cookies and TLS fingerprint come from the real authenticated browser.
  - No credential extraction on the Python side.
  - Bypasses Akamai / Cloudflare false positives that fire on httpx requests.

Port 9222 is *mandatory* — port 9223 is Tabby's relay which whitelists only
Page.screencast* and Input.dispatch*Event, so Runtime.evaluate is blocked there.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import websockets

CDP_PORT = 9222
CDP_LIST_URL = f"http://localhost:{CDP_PORT}/json"


async def find_page(domain: str) -> str | None:
    """Return the webSocketDebuggerUrl for the first page target whose URL matches domain.

    Args:
        domain: Substring matched against each target's `url` (e.g. "expedia.com").

    Returns:
        The WebSocket URL, or None if no matching page is currently open.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(CDP_LIST_URL, timeout=5)
        targets = resp.json()
    for t in targets:
        if t.get("type") == "page" and domain in t.get("url", ""):
            return t["webSocketDebuggerUrl"]
    return None


async def cdp_eval(ws_url: str, expression: str) -> Any:
    """Evaluate a JavaScript expression in the browser and return the parsed result.

    The caller is responsible for wrapping async JS in a Promise. Values are
    returned by-value: string results are `json.loads`-decoded; other primitives
    (number, bool) are returned as-is; objects come back as dicts.

    Raises:
        RuntimeError: if the evaluation surfaced `exceptionDetails`.
    """
    async with websockets.connect(ws_url) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
        )
        raw = await ws.recv()
    payload = json.loads(raw)
    if "error" in payload:
        msg = json.dumps(payload["error"])[:500]
        raise RuntimeError(f"CDP eval error: {msg}")
    result = payload.get("result", {})
    if "exceptionDetails" in result:
        msg = json.dumps(result["exceptionDetails"])[:500]
        raise RuntimeError(f"CDP eval threw: {msg}")
    value_wrap = result.get("result", {})
    if value_wrap.get("type") == "string":
        return json.loads(value_wrap["value"])
    return value_wrap.get("value")


async def cdp_fetch(
    ws_url: str,
    url: str,
    *,
    method: str = "GET",
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Execute fetch() inside the browser and return the parsed response body.

    Always sends `credentials: 'include'` so session cookies from the
    authenticated Tabby browser are attached. On non-2xx the server's status and
    (truncated) body are included in the RuntimeError message.

    Args:
        ws_url: WebSocket debugger URL (from `find_page`).
        url: Absolute URL to fetch.
        method: HTTP method; defaults to GET.
        body: Optional JSON-serializable body; sent as a stringified JSON payload.
        headers: Optional extra request headers.

    Returns:
        Parsed JSON body on 2xx when possible; otherwise `{"status": n, "text": <raw>}`.
    """
    init: dict[str, Any] = {
        "method": method.upper(),
        "credentials": "include",
    }
    if headers:
        init["headers"] = headers
    if body is not None:
        init["body"] = json.dumps(body)

    js = (
        f"fetch({json.dumps(url)}, {json.dumps(init)}).then("
        "r => r.text().then(t => JSON.stringify({status: r.status, body: t}))"
        ")"
    )
    data = await cdp_eval(ws_url, js)
    status = data.get("status")
    raw_body = data.get("body", "")
    if not (isinstance(status, int) and 200 <= status < 300):
        snippet = (raw_body or "")[:500]
        raise RuntimeError(f"{method.upper()} {url} -> {status}: {snippet}")
    try:
        return json.loads(raw_body) if raw_body else {}
    except (ValueError, TypeError):
        return {"status": status, "text": raw_body}
