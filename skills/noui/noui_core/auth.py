"""NoUI runtime auth resolver — fetches live credentials from Tabby.

This module is used directly by the NoUI backend and by generated MCP servers
at runtime to resolve live auth material for a given Tabby profile.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import httpx

TABBY_API_HOST = os.environ.get("TABBY_API_URL", "http://localhost:8080")


async def get_auth_headers(profile_id: str) -> dict:
    """Fetch live auth material from Tabby for the given profile_id.

    Returns a dict of HTTP headers to merge into outgoing requests.
    Cookie values from the profile are merged into a single ``Cookie`` header.

    Args:
        profile_id: The Tabby credential profile identifier.

    Returns:
        Dict mapping header name to header value.

    Raises:
        httpx.HTTPStatusError: If Tabby returns a non-2xx response.
        httpx.RequestError: If the connection to Tabby fails.
    """
    url = f"{TABBY_API_HOST}/runtime/credentials/{profile_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    return _build_headers_from_data(data)


def get_auth_headers_sync(profile_id: str) -> dict:
    """Synchronous version of get_auth_headers using urllib (no extra deps).

    Useful in synchronous contexts or when httpx is not available.

    Args:
        profile_id: The Tabby credential profile identifier.

    Returns:
        Dict mapping header name to header value.

    Raises:
        urllib.error.HTTPError: If Tabby returns a non-2xx response.
        urllib.error.URLError: If the connection to Tabby fails.
    """
    url = f"{TABBY_API_HOST}/runtime/credentials/{profile_id}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req) as response:
        raw = response.read()
    data = json.loads(raw)
    return _build_headers_from_data(data)


def _build_headers_from_data(data: dict) -> dict:
    """Construct an HTTP headers dict from the Tabby credential payload.

    Expected payload shape:
        {
            "headers": [{"name": "Authorization", "value": "Bearer ..."}],
            "cookies": [{"name": "session", "value": "abc123"}]
        }
    """
    headers: dict[str, str] = {}

    for h in data.get("headers", []):
        name = h.get("name", "")
        value = h.get("value", "")
        if name:
            headers[name] = value

    cookie_parts: list[str] = []
    for cookie in data.get("cookies", []):
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if name:
            cookie_parts.append(f"{name}={value}")

    if cookie_parts:
        existing = headers.get("Cookie", "")
        merged = "; ".join(filter(None, [existing] + cookie_parts))
        headers["Cookie"] = merged

    return headers
