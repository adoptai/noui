"""Convert a HAR (HTTP Archive) log into NoUI MCP tool definitions.

Each HAR entry that looks like an API call becomes one tool_def dict. The
dicts are the primary input to both the code generator and the Markdown doc
generator.

Tool-def schema (all fields):
    name              str       Python-identifier tool name
    description       str       Human-readable summary
    method            str       HTTP method (GET, POST, …)
    url               str       Full URL from the recording (example values)
    path              str       Path template with {param} placeholders
    base_url          str       Scheme + host
    request_headers   list[dict]  [{name, value}, …] — no auth values
    request_body      dict|None   Parsed JSON body; None for GET/form
    request_content_type  str     MIME type of request body
    response_status   int       HTTP status code seen during recording
    params            list[dict]  [{name, description, type, required, source}, …]
    auth_headers      list[str]   Header names that carry auth (no values)
    auth_cookies      list[str]   Cookie names that carry auth (no values)
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HarValidationError(ValueError):
    """Raised when a HAR cannot be turned into any usable tool definitions.

    This is a *user-correctable* error: the capture itself was empty, malformed,
    or contained only static assets / non-API traffic. Callers (the MCP and
    Skill compilers, and the workflow export endpoint) should surface the
    message back to the user rather than emitting an empty server.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def har_to_tool_defs(
    har: dict,
    *,
    workflow_name: str,
    tabby_profile_id: str,
    auth_cookie_names: list[str] | None = None,
) -> list[dict]:
    """Convert a parsed HAR dict into a list of tool_def dicts.

    Args:
        har: Parsed HAR JSON (top-level dict with "log" key).
        workflow_name: Human-readable workflow name used in auto-descriptions.
        tabby_profile_id: Non-empty when the workflow is authenticated.
        auth_cookie_names: Additional cookie names to treat as auth tokens.

    Raises:
        HarValidationError: If the HAR is malformed, has no entries, or yields
            no API-like requests after filtering. The message is safe to show
            to end users.
    """
    if not isinstance(har, dict):
        raise HarValidationError(
            f"HAR must be a JSON object with a top-level 'log' key, got {type(har).__name__}."
        )

    log = har.get("log")
    if not isinstance(log, dict) or "entries" not in log:
        raise HarValidationError(
            "HAR is missing the 'log.entries' array. The capture file does not "
            "look like a valid HAR 1.2 document — re-record the workflow and "
            "ensure the browser exported network traffic."
        )

    entries = log.get("entries")
    if not isinstance(entries, list) or not entries:
        raise HarValidationError(
            "HAR contains no entries. The browser did not record any network "
            "traffic for this workflow — re-record while interacting with the "
            "site so requests are captured."
        )

    api_entries = [e for e in entries if isinstance(e, dict) and _is_api_call(e)]

    if not api_entries:
        raise HarValidationError(
            f"HAR has {len(entries)} entries but none look like API calls "
            "after filtering static assets, redirects, and analytics. "
            "Re-record the workflow and trigger the action whose API you want "
            "to expose (e.g. submit a form, load data, click a button)."
        )

    # Deduplicate: keep the first representative per (method, base_url, path_pattern).
    seen: dict[tuple[str, str, str], dict] = {}
    for entry in api_entries:
        req = entry.get("request", {})
        method = req.get("method", "GET").upper()
        raw_url = req.get("url", "")
        parsed = urlparse(raw_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        path_template, _ = _path_template(parsed.path)
        key = (method, base_url, path_template)
        if key not in seen:
            seen[key] = entry

    tool_defs: list[dict] = []
    used_names: set[str] = set()

    for entry in seen.values():
        td = _entry_to_tool_def(
            entry,
            workflow_name=workflow_name,
            tabby_profile_id=tabby_profile_id,
            auth_cookie_names=auth_cookie_names or [],
            used_names=used_names,
        )
        used_names.add(td["name"])
        tool_defs.append(td)

    return tool_defs


# ---------------------------------------------------------------------------
# HAR filtering
# ---------------------------------------------------------------------------

_SKIP_EXTENSIONS = re.compile(
    r"\.(png|jpg|jpeg|gif|webp|svg|ico|woff|woff2|ttf|eot|css|js|map|txt|pdf|zip)(\?.*)?$",
    re.IGNORECASE,
)

_SKIP_URL_PATTERNS = re.compile(
    r"/(analytics|track|beacon|pixel|telemetry|log|collect|gtm|ga4|"
    r"_next/static|__webpack|sockjs|hot-update|favicon|robots\.txt|"
    r"sw\.js|service.worker)",
    re.IGNORECASE,
)

_API_CONTENT_TYPES = {
    "application/json",
    "application/x-www-form-urlencoded",
    "application/octet-stream",
    "text/plain",  # some APIs use text/plain for JSON
    "multipart/form-data",
}

_SKIP_STATUS = {0, 204, 301, 302, 303, 307, 308}

# Response content types that indicate a downloadable document/file (not a
# static asset — those are caught by _SKIP_EXTENSIONS). Kept as operations
# because the harness can stream binary results into the sandbox (gap G2).
_DOWNLOAD_CONTENT_TYPES = (
    "application/pdf",
    "application/zip",
    "application/octet-stream",
    "application/msword",
    "application/vnd.openxmlformats",
    "application/vnd.ms-",
    "text/csv",
)


def _is_api_call(entry: dict) -> bool:
    req = entry.get("request", {})
    resp = entry.get("response", {})

    method = req.get("method", "").upper()
    if method in ("OPTIONS", "HEAD"):
        return False

    url = req.get("url", "")
    if not url or url.startswith("data:") or url.startswith("blob:"):
        return False

    parsed = urlparse(url)
    path = parsed.path.lower()

    if _SKIP_EXTENSIONS.search(path):
        return False
    if _SKIP_URL_PATTERNS.search(path):
        return False

    status = resp.get("status", 0)
    if status in _SKIP_STATUS:
        return False

    # Accept if the response content looks like API data
    resp_mime = resp.get("content", {}).get("mimeType", "").lower()
    if any(t in resp_mime for t in ("json", "xml", "form-urlencoded", "text/plain")):
        return True

    # Accept document/file downloads. These are real API operations (e.g. a
    # statement PDF at /documents/{id}); static assets are already filtered by
    # _SKIP_EXTENSIONS above. The harness streams binary results into the
    # sandbox (gap G2), so such endpoints are usable, not just noise.
    if any(t in resp_mime for t in _DOWNLOAD_CONTENT_TYPES):
        return True

    # Accept if the request body looks like API data
    req_mime = (req.get("postData", {}) or {}).get("mimeType", "").lower()
    if any(t in req_mime for t in ("json", "form-urlencoded", "octet-stream")):
        return True

    # Accept XHR/Fetch initiator types
    initiator_type = entry.get("_initiatorType", "").lower()
    if initiator_type in ("fetch", "xmlhttprequest", "xhr"):
        return True

    resource_type = entry.get("_resourceType", "").lower()
    if resource_type in ("fetch", "xhr"):
        return True

    # Accept non-GET requests even if we can't identify the mime type
    return method != "GET"


# ---------------------------------------------------------------------------
# Path template extraction
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_HEX_ID_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^[0-9]+$")


def _path_template(path: str) -> tuple[str, list[str]]:
    """Replace dynamic segments with {name} placeholders.

    Returns (template, list[extracted_param_names]).
    """
    segments = path.split("/")
    param_names: list[str] = []
    out: list[str] = []
    prev_segment = ""

    for seg in segments:
        if not seg:
            out.append(seg)
            prev_segment = seg
            continue

        if _UUID_RE.fullmatch(seg) or _HEX_ID_RE.fullmatch(seg):
            # Name based on the preceding noun segment
            pname = _id_param_name(prev_segment)
            if pname in param_names:
                pname = f"{pname}_{len(param_names) + 1}"
            param_names.append(pname)
            out.append(f"{{{pname}}}")
        elif _NUMERIC_RE.fullmatch(seg):
            pname = _id_param_name(prev_segment)
            if pname in param_names:
                pname = f"{pname}_{len(param_names) + 1}"
            param_names.append(pname)
            out.append(f"{{{pname}}}")
        else:
            out.append(seg)

        prev_segment = seg

    return "/".join(out), param_names


def _id_param_name(preceding_segment: str) -> str:
    """Derive a param name from the path segment that comes before the ID."""
    if not preceding_segment:
        return "id"
    # Singularise naive plurals: clients → client_id, posts → post_id
    noun = re.sub(r"[^a-z0-9]", "_", preceding_segment.lower()).rstrip("_")
    if noun.endswith("s"):
        noun = noun[:-1]
    return f"{noun}_id" if noun else "id"


# ---------------------------------------------------------------------------
# Tool name derivation
# ---------------------------------------------------------------------------

_METHOD_PREFIX = {
    "GET": "get",
    "POST": "create",
    "PUT": "update",
    "PATCH": "patch",
    "DELETE": "delete",
}


def _tool_name(method: str, path_template: str, used_names: set[str]) -> str:
    prefix = _METHOD_PREFIX.get(method.upper(), method.lower())
    # Take last 3 meaningful path segments, strip leading slashes/dots
    parts = [
        re.sub(r"[^a-z0-9]+", "_", seg.strip("{}").lower()).strip("_")
        for seg in path_template.split("/")
        if seg and "{" not in seg
    ][-3:]
    base = "_".join(p for p in parts if p) or "endpoint"
    name = f"{prefix}_{base}"
    # Ensure uniqueness
    candidate = name
    n = 2
    while candidate in used_names:
        candidate = f"{name}_{n}"
        n += 1
    return candidate


# ---------------------------------------------------------------------------
# Entry → tool_def
# ---------------------------------------------------------------------------

_AUTH_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-access-token",
        "x-csrf-token",
        "x-xsrf-token",
        "proxy-authorization",
    }
)

# Cookie names that are typically session/auth tokens
_AUTH_COOKIE_PATTERNS = re.compile(
    r"(sid|session|auth|token|jwt|csrf|ssid|secure|login|identity|id_token)",
    re.IGNORECASE,
)


def _entry_to_tool_def(
    entry: dict,
    *,
    workflow_name: str,
    tabby_profile_id: str,
    auth_cookie_names: list[str],
    used_names: set[str],
) -> dict:
    req = entry.get("request", {})
    resp = entry.get("response", {})

    method = req.get("method", "GET").upper()
    raw_url = req.get("url", "")
    parsed = urlparse(raw_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    path_template, path_param_names = _path_template(parsed.path)

    name = _tool_name(method, path_template, used_names)

    # ── Request headers ───────────────────────────────────────────────────────
    raw_headers: list[dict] = req.get("headers", [])
    request_headers: list[dict] = []
    auth_headers: list[str] = []
    cookies_from_header: list[str] = []

    for h in raw_headers:
        hname = h.get("name", "")
        hname_lower = hname.lower()
        if hname_lower in _AUTH_HEADER_NAMES:
            if hname_lower == "authorization":
                auth_headers.append(hname)
            elif hname_lower == "cookie":
                # Parse cookie header to extract names
                cookie_str = h.get("value", "")
                for part in cookie_str.split(";"):
                    cname = part.strip().split("=")[0].strip()
                    if cname and (
                        _AUTH_COOKIE_PATTERNS.search(cname) or cname in auth_cookie_names
                    ):
                        cookies_from_header.append(cname)
            # Skip auth headers from the documented headers list
        else:
            request_headers.append({"name": hname, "value": h.get("value", "")})

    # ── Request body ──────────────────────────────────────────────────────────
    post_data: dict = req.get("postData") or {}
    content_type = post_data.get("mimeType", "").split(";")[0].strip()
    request_body: dict | None = None
    body_params: list[dict] = []

    if post_data:
        raw_text = post_data.get("text", "")
        if "json" in content_type:
            try:
                request_body = json.loads(raw_text)
                body_params = _body_to_params(request_body)
            except (json.JSONDecodeError, TypeError):
                request_body = None
        elif "form-urlencoded" in content_type:
            # HAR may provide params[] or raw text
            form_params = post_data.get("params", [])
            if form_params:
                body_params = [
                    {
                        "name": p.get("name", ""),
                        "description": f"Form field: {p.get('name', '')}",
                        "type": "string",
                        "required": True,
                        "source": "body",
                    }
                    for p in form_params
                    if p.get("name")
                ]
            elif raw_text:
                body_params = _form_text_to_params(raw_text)

    # ── Query string params ───────────────────────────────────────────────────
    qs_raw: list[dict] = req.get("queryString", [])
    query_params: list[dict] = [
        {
            "name": q.get("name", ""),
            "description": f"Query parameter: {q.get('name', '')}",
            "type": "string",
            "required": False,
            "source": "query",
        }
        for q in qs_raw
        if q.get("name") and q.get("name", "").lower() not in _AUTH_HEADER_NAMES
    ]

    # ── Path params ───────────────────────────────────────────────────────────
    path_params: list[dict] = [
        {
            "name": pname,
            "description": f"Path parameter: {pname}",
            "type": "string",
            "required": True,
            "source": "path",
        }
        for pname in path_param_names
    ]

    all_params = path_params + body_params + query_params

    # ── Auth cookies ──────────────────────────────────────────────────────────
    # Merge detected cookies with any explicit list from caller
    auth_cookies: list[str] = list(dict.fromkeys(cookies_from_header + auth_cookie_names))

    # ── Description ──────────────────────────────────────────────────────────
    verb = method.capitalize()
    path_label = " ".join(
        seg.replace("-", " ").replace("_", " ").title()
        for seg in path_template.split("/")
        if seg and not seg.startswith("{")
    )
    description = (
        f"{verb} {path_label} ({workflow_name})" if path_label else f"{verb} ({workflow_name})"
    )

    return {
        "name": name,
        "description": description,
        "method": method,
        "url": raw_url,
        "path": path_template,
        "base_url": base_url,
        "request_headers": request_headers,
        "request_body": request_body,
        "request_content_type": content_type,
        "response_status": resp.get("status", 200),
        "params": all_params,
        "auth_headers": auth_headers,
        "auth_cookies": auth_cookies,
    }


# ---------------------------------------------------------------------------
# Parameter extraction helpers
# ---------------------------------------------------------------------------


def _body_to_params(body: dict | None) -> list[dict]:
    if not body or not isinstance(body, dict):
        return []
    params: list[dict] = []
    for key, val in body.items():
        if isinstance(val, bool):
            ptype = "bool"
        elif isinstance(val, int):
            ptype = "int"
        elif isinstance(val, float):
            ptype = "float"
        else:
            ptype = "string"
        params.append(
            {
                "name": key,
                "description": f"Request body field: {key}",
                "type": ptype,
                "required": True,
                "source": "body",
            }
        )
    return params


def _form_text_to_params(text: str) -> list[dict]:
    try:
        parsed = parse_qs(text, keep_blank_values=True)
    except Exception:
        return []
    return [
        {
            "name": key,
            "description": f"Form field: {key}",
            "type": "string",
            "required": True,
            "source": "body",
        }
        for key in parsed
    ]
