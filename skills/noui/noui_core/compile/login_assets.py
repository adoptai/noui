"""
Login profile generator: converts a login-recording LoginSession into
Tabby Application + ServiceProfile drafts plus a review report.

Inputs (loaded by the router before calling generate()):
    - session: LoginSession dict
    - click_events: list of ClickEvent dicts (chronological)
    - url_events: list of UrlEvent dicts (chronological)
    - har: HAR dict (log.entries[]) or None

Outputs (returned as a dict):
    {
        "recording": {"session_id": "...", "source": "elicitation-login-recorder"},
        "application_draft": { ... },
        "service_profile_draft": { ... },
        "review_items": [ {"type": ..., "severity": ..., "message": ...} ],
        "validation": {"generator_valid": bool, "issues": []}
    }
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from typing import Any
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Credential-type shape
# ---------------------------------------------------------------------------

# Tabby's credentials consumer iterates credential_types.cookies expecting
# {name, volatility} objects (credentials.service.ts); a plain string array of
# cookie names yields empty name/value for every cookie. Akamai/CDN tokens
# rotate per request, so they are marked VOLATILE; everything else is STABLE.
_VOLATILE_COOKIE_NAMES = frozenset({"bm_sz", "ak_bmsc", "bm_so", "bm_s", "_abck"})


def _cookie_credential_types(cookie_names: list[str]) -> list[dict]:
    """Convert cookie names into Tabby's required ``[{name, volatility}]`` shape."""
    return [
        {
            "name": name,
            "volatility": "VOLATILE" if name in _VOLATILE_COOKIE_NAMES else "STABLE",
        }
        for name in cookie_names
    ]


# ---------------------------------------------------------------------------
# Selector helpers
# ---------------------------------------------------------------------------


def _selector_confidence(ev: dict) -> str:
    """Return 'high', 'medium', or 'low'."""
    data_attrs: dict = {}
    if ev.get("data_attrs_json"):
        try:
            data_attrs = json.loads(ev["data_attrs_json"])
        except Exception:
            pass

    # high: data-testid / data-test
    if data_attrs.get("data-testid") or data_attrs.get("data-test"):
        return "high"
    # high: stable id (no 8+ hex chars that look like random UUIDs)
    elem_id = ev.get("element_id") or ""
    if elem_id and not re.search(r"[0-9a-f]{8,}", elem_id, re.I):
        return "high"
    # medium: name, autocomplete, aria-label, placeholder
    if (
        ev.get("field_name")
        or ev.get("autocomplete")
        or ev.get("aria_label")
        or ev.get("placeholder")
    ):
        return "medium"
    return "low"


def _build_selector(ev: dict) -> str:
    """Build the best CSS selector from click event metadata."""
    data_attrs: dict = {}
    if ev.get("data_attrs_json"):
        try:
            data_attrs = json.loads(ev["data_attrs_json"])
        except Exception:
            pass

    # Priority 1: data-testid / data-test
    if data_attrs.get("data-testid"):
        return f'[data-testid="{data_attrs["data-testid"]}"]'
    if data_attrs.get("data-test"):
        return f'[data-test="{data_attrs["data-test"]}"]'

    # Priority 2: stable id
    elem_id = ev.get("element_id") or ""
    if elem_id and not re.search(r"[0-9a-f]{8,}", elem_id, re.I):
        return f"#{elem_id}"

    # Priority 3: name attribute
    field_name = ev.get("field_name") or ""
    tag = (ev.get("tag_name") or "input").lower()
    if field_name:
        return f'{tag}[name="{field_name}"]'

    # Priority 4: autocomplete
    autocomplete = ev.get("autocomplete") or ""
    if autocomplete:
        return f'{tag}[autocomplete="{autocomplete}"]'

    # Priority 5: semantic combination
    input_type = ev.get("input_type") or ""
    aria_label = ev.get("aria_label") or ""
    placeholder = ev.get("placeholder") or ""
    parts = [tag]
    if input_type and tag == "input":
        parts.append(f'[type="{input_type}"]')
    if aria_label:
        parts.append(f'[aria-label="{aria_label}"]')
    elif placeholder:
        parts.append(f'[placeholder="{placeholder}"]')
    if len(parts) > 1:
        return "".join(parts)

    # Priority 6: fallback to existing selector
    return ev.get("selector") or tag


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


# Query params whose value is minted per session/request. A URL carrying one is
# not replayable: the value died with the recording.
_VOLATILE_QUERY_PARAM = re.compile(
    r"(token|jsessionid|sessionid|sid|nonce|ticket|otp|csrf|xsrf|auth|signature|sig|"
    r"timestamp|_ts|expires)",
    re.I,
)


def _has_volatile_query(url: str) -> bool:
    """True when the URL's query carries session-bound, one-time material.

    ICICI's recorded landing URL was
    ``.../corp/AuthenticationController?...&UX_TOKEN=<one-time>&CTA_FLAG=CCPSTM``.
    Replaying it later asks for a token that expired with the recording session.

    Stripping the query does NOT rescue such a URL: in Finacle/JSP-style apps the
    query IS the routing (FORMSGROUP_ID__, __START_TRAN_FLAG__, ACTION.LOAD), so
    the bare path returns "Page temporarily unavailable". Verified on the live
    portal — both forms fail. There is no replayable variant, so callers must not
    emit one at all rather than emit a broken one.
    """
    try:
        q = urlparse(url).query
    except Exception:
        return False
    if not q:
        return False
    return any(_VOLATILE_QUERY_PARAM.search(part.split("=", 1)[0]) for part in q.split("&"))


def _url_origin(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return url


def _url_domain(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return url


# First path segments that belong to a login/auth flow rather than to the app
# behind it. Used to reject a "post-login" pattern that is really another login
# page. Over-rejecting is cheap (the human confirms with "Mark as Resolved");
# under-rejecting hangs the session forever, so the list leans inclusive.
# Deliberately absent: post-login landing routes that only *look* auth-adjacent,
# e.g. Airbnb's `/` and Expedia's `/onboarding`.
_LOGIN_FLOW_SEGMENTS = frozenset(
    {
        "login",
        "log-in",
        "log_in",
        "logon",
        "signin",
        "sign-in",
        "sign_in",
        "signup",
        "sign-up",
        "register",
        "auth",
        "authn",
        "authenticate",
        "authentication",
        "authorize",
        "oauth",
        "oauth2",
        "openid",
        "sso",
        "saml",
        "idp",
        "password",
        "enterpassword",
        "resetpassword",
        "forgot",
        "verify",
        "verifyotp",
        "verification",
        "otp",
        "mfa",
        "2fa",
        "twofactor",
        "two-factor",
        "challenge",
    }
)


def _first_path_segment(url: str) -> str:
    raw = url if url.startswith(("http://", "https://")) else f"http://{url}"
    path = urlparse(raw).path.strip("/")
    return path.split("/")[0].lower() if path else ""


def _derive_post_login_pattern(login_url: str, landing_url: str) -> str:
    """A glob the LOGGED-IN url matches but the login page does not, or ``""``.

    Returning ``""`` means "no trustworthy pattern": the caller then emits no
    ``wait_for_url``, and login completes when the human clicks *Mark as
    Resolved*. That degradation is cheap. A **wrong** pattern is not — a
    ``wait_for_url`` that can never match leaves the session waiting until it
    times out, and a session that never reports LOGIN_NEEDED never gets a
    sign-in prompt either.

    Seen live on dev: an Airbnb profile compiled
    ``https://www.airbnb.com/login/**`` as its *logged-in* check. It matches
    ``/login/otp`` but not ``/``, ``/hosting/listings`` or ``/s/homes``, so no
    real sign-in could ever satisfy it. The cause is upstream — the capture's
    login slice ended before the post-login navigation, so the "landing" URL was
    still inside the login flow — but the compiler must not turn that into an
    unsatisfiable step.

    Three rejections, in order of how directly they'd break:
      1. the candidate also matches the login page → it would auto-resolve
         instantly, before the human has logged in;
      2. the landing path is itself a login-flow route (see
         ``_LOGIN_FLOW_SEGMENTS``) → it can never match once logged in;
      3. the landing path shares its top-level route with the login page → not a
         distinguishing signal, and covers custom auth routes the list misses.

    Shape note: the glob is ``/<segment>**``, NOT ``/<segment>/**``. Both
    matchers that consume it — Playwright's anchored ``page.waitForURL`` glob and
    Tabby's own unanchored ``urlGlobToRegex`` (worker ``login-dsl-runner.ts``,
    which turns ``**`` into ``.*``) — require the literal ``/`` when the pattern
    carries one, so ``/dashboard/**`` matches ``/dashboard/x`` but NOT the bare
    ``/dashboard`` or ``/dashboard?tab=1``. Landing exactly on the route is the
    common case, so the slash-less form is the one that actually fires.
    """
    raw = (
        landing_url if landing_url.startswith(("http://", "https://")) else f"http://{landing_url}"
    )
    parsed = urlparse(raw)
    seg = _first_path_segment(landing_url)
    candidate = (
        f"{parsed.scheme}://{parsed.netloc}/{seg}**"
        if seg
        else f"{parsed.scheme}://{parsed.netloc}/**"
    )
    if fnmatch(login_url, candidate):
        return ""
    if seg and seg in _LOGIN_FLOW_SEGMENTS:
        return ""
    if seg and seg == _first_path_segment(login_url):
        return ""
    return candidate


def _is_redirect_hop(prev_url: str, next_url: str) -> bool:
    """True if next_url looks like a transient redirect (same domain, /callback, /sso, /auth)."""
    try:
        prev_parsed = urlparse(prev_url)
        next_parsed = urlparse(next_url)
        if prev_parsed.netloc != next_parsed.netloc:
            return False
        path = next_parsed.path.lower()
        return any(
            s in path for s in ["/callback", "/sso", "/auth", "/oauth", "/saml", "/redirect"]
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# HAR analysis helpers
# ---------------------------------------------------------------------------


def _analyze_har(har: dict | None) -> dict[str, Any]:
    """Extract auth signals from HAR entries."""
    result: dict[str, Any] = {
        "has_cookies": False,
        "has_auth_headers": False,
        "has_csrf": False,
        "auth_domains": [],
        "set_cookie_headers": [],
        "auth_header_names": [],
    }
    if not har:
        return result

    entries = har.get("log", {}).get("entries", [])
    domains_seen: set[str] = set()
    cookie_names: list[str] = []
    auth_header_names: list[str] = []

    for entry in entries:
        response = entry.get("response", {})
        req = entry.get("request", {})
        url = req.get("url", "")
        domain = _url_domain(url)
        if domain:
            domains_seen.add(domain)

        # Check response Set-Cookie headers
        for header in response.get("headers", []):
            name = (header.get("name") or "").lower()
            if name == "set-cookie":
                result["has_cookies"] = True
                val = header.get("value", "")
                # Extract cookie name
                cookie_name = val.split("=")[0].strip()
                if cookie_name:
                    cookie_names.append(cookie_name)

        # Check request Authorization headers
        for header in req.get("headers", []):
            hname = (header.get("name") or "").lower()
            if hname in ("authorization", "x-auth-token", "x-api-key"):
                result["has_auth_headers"] = True
                auth_header_names.append(header.get("name", ""))

        # Check for CSRF tokens
        for header in req.get("headers", []):
            hname = (header.get("name") or "").lower()
            if "csrf" in hname or "xsrf" in hname:
                result["has_csrf"] = True
                auth_header_names.append(header.get("name", ""))

    result["auth_domains"] = list(domains_seen)
    result["set_cookie_headers"] = list(set(cookie_names))[:20]
    result["auth_header_names"] = list(set(auth_header_names))[:10]
    return result


# Common two-part public suffixes whose registrable domain is the last 3 labels.
_COMPOUND_TLDS = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "gov.uk",
    "com.br",
    "com.au",
    "com.mx",
    "com.ar",
    "com.tr",
    "com.cn",
    "com.sg",
    "co.jp",
    "co.in",
    "co.za",
    "co.nz",
    "co.kr",
}


def _registrable_suffix(host: str) -> str | None:
    """Convert a hostname to a leading-dot egress suffix covering its subdomains
    (``www.expedia.com`` -> ``.expedia.com``).

    Uses a small compound-TLD table for the common two-part suffixes
    (``.co.uk``); everything else collapses to the last two labels. Heuristic,
    not a full public-suffix list — the goal is broad-but-scoped allowlist
    coverage, and ``extra_egress_allowlist`` stays editable for tightening.
    """
    host = (host or "").strip().lower().split(":")[0].rstrip(".")
    if not host or host == "localhost":
        return None
    if all(ch.isdigit() or ch == "." for ch in host):  # bare IP
        return None
    labels = host.split(".")
    if len(labels) < 2:
        return None
    last_two = ".".join(labels[-2:])
    registrable = (
        ".".join(labels[-3:]) if last_two in _COMPOUND_TLDS and len(labels) >= 3 else last_two
    )
    return f".{registrable}"


def egress_allowlist_from_domains(domains: list[str] | None) -> list[str]:
    """Derive a deduplicated egress allowlist (leading-dot suffix patterns) from
    domains observed in a recording's HAR. Feeds ``extra_egress_allowlist`` so
    the compiled app runs under egress enforce without per-vendor kubectl edits.
    """
    out: list[str] = []
    for host in domains or []:
        suffix = _registrable_suffix(host)
        if suffix and suffix not in out:
            out.append(suffix)
    return out


# ---------------------------------------------------------------------------
# App Template payload (tenant-wide auto-provisioning)
# ---------------------------------------------------------------------------


def build_app_template_payload(
    application_draft: dict[str, Any],
    service_profile_draft: dict[str, Any],
) -> dict[str, Any]:
    """Derive a Tabby App Template payload (``POST /admin/app-templates``).

    A template is the tenant-wide, per-user auto-provisioning blueprint. When a
    federated user (platform JWT, ``owner_user_id`` set) requests a ``profile_id``
    that resolves to no profile, Tabby's ``autoProvisionFromTemplate`` looks up a
    template by ``{tenant_id, profile_name_pattern}`` and clones a private
    App+Profile+Session for that user.

    Two correctness invariants this builder enforces:

    1. ``profile_name_pattern`` MUST equal the runtime profile slug
       (``service_profile_draft.profile_id``, which is what generated ops bake in
       as ``PROFILE_SLUG``). If it differs, ``autoProvisionFromTemplate`` never
       matches and every federated request 404s.
    2. ``autoProvisionFromTemplate`` builds the cloned profile's
       ``credential_types``/``target_domains`` from ``export_policy``, not from a
       separate profile draft (the template has no profile-draft field). So the
       profile draft's ``credential_types`` and ``target_domains`` are folded into
       ``export_policy`` here, or every auto-provisioned user inherits empty
       credential types.

    Args:
        application_draft: The ``application_draft`` dict from :func:`generate`.
        service_profile_draft: The ``service_profile_draft`` dict from :func:`generate`.

    Returns:
        A dict ready to POST to ``/admin/app-templates``.
    """
    profile_slug = service_profile_draft.get("profile_id", "")
    if not profile_slug:
        raise ValueError(
            "service_profile_draft.profile_id is required — it becomes the "
            "template's profile_name_pattern and the runtime PROFILE_SLUG."
        )

    export_policy = dict(application_draft.get("export_policy") or {})
    # Fold profile-draft fields into export_policy so autoProvisionFromTemplate
    # clones working credential_types/target_domains onto each per-user profile.
    credential_types = service_profile_draft.get("credential_types")
    if credential_types is not None:
        export_policy["credential_types"] = credential_types
    target_domains = service_profile_draft.get("target_domains")
    if target_domains is not None:
        export_policy["target_domains"] = target_domains

    return {
        "name": application_draft.get("name") or profile_slug,
        # MUST equal the runtime profile slug (PROFILE_SLUG) — the auto-provision
        # match key. See invariant 1 above.
        "profile_name_pattern": profile_slug,
        "login_config": application_draft.get("login_config") or {},
        "keepalive_config": application_draft.get("keepalive_config") or {},
        "export_policy": export_policy,
        "browser_policy": application_draft.get("browser_policy")
        or {"clipboard": False, "downloads": False, "file_chooser": False},
        # Cloned onto every auto-provisioned app so per-user sessions inherit the
        # vendor's auth/CDN domains in their egress allowlist.
        "extra_egress_allowlist": application_draft.get("extra_egress_allowlist") or [],
        "notification_config": application_draft.get("notification_config") or {},
        # execute_enabled is carried onto every app auto-provisioned from this
        # template (Tabby's autoProvisionFromTemplate clones it), so the per-user
        # connection can run /execute/fetch | /execute/browser — which call_web_api
        # depends on. The App Template DTO now accepts this field (the earlier A4
        # 400 was fixed); omitting it would default to false and silently break
        # execute for every provisioned user.
        "execute_enabled": bool(application_draft.get("execute_enabled", True)),
    }


def _union_scalars(existing: list | None, new: list | None) -> list:
    """Union two scalar lists; new entries first, existing ones appended."""
    out: list = []
    for src in (new or []), (existing or []):
        for x in src:
            if x not in out:
                out.append(x)
    return out


def _union_by_field(existing: list | None, new: list | None, field: str) -> list:
    """Union two lists of dicts keyed by ``field``; new wins on key conflict."""
    out: list = []
    seen: set = set()
    for src in (new or []), (existing or []):
        for item in src:
            if not isinstance(item, dict):
                continue
            key = item.get(field)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def merge_template_export_policy(existing_template: dict, new_payload: dict) -> dict:
    """Merge additive ``export_policy`` fields from an existing template into a
    new template payload, so an upsert PUT does not clobber extractions /
    allowlists / cookie credential_types / target_urls accumulated by prior
    recordings.

    Additive (unioned): ``custom_extractions`` (by ``key``), ``header_allowlist``,
    ``request_header_allowlist``, ``target_urls``, ``artifact_types``, and
    ``credential_types`` (cookies by ``name``, headers as scalars).
    Everything else (``login_config``, ``keepalive_config``, ...) is taken from
    the new payload — those are per-login-flow, not additive.
    """
    merged = dict(new_payload)

    # Top-level extra_egress_allowlist accumulates across recordings (union).
    merged_extra = _union_scalars(
        (existing_template or {}).get("extra_egress_allowlist"),
        new_payload.get("extra_egress_allowlist"),
    )
    if merged_extra:
        merged["extra_egress_allowlist"] = merged_extra

    old_ep = (existing_template or {}).get("export_policy") or {}
    new_ep = dict(merged.get("export_policy") or {})

    for key in ("header_allowlist", "request_header_allowlist", "target_urls", "artifact_types"):
        unioned = _union_scalars(old_ep.get(key), new_ep.get(key))
        if unioned:
            new_ep[key] = unioned

    custom = _union_by_field(
        old_ep.get("custom_extractions"), new_ep.get("custom_extractions"), "key"
    )
    if custom:
        new_ep["custom_extractions"] = custom

    old_ct = old_ep.get("credential_types") or {}
    new_ct = dict(new_ep.get("credential_types") or {})
    cookies = _union_by_field(old_ct.get("cookies"), new_ct.get("cookies"), "name")
    headers = _union_scalars(old_ct.get("headers"), new_ct.get("headers"))
    if cookies:
        new_ct["cookies"] = cookies
    if headers:
        new_ct["headers"] = headers
    if new_ct:
        new_ep["credential_types"] = new_ct

    merged["export_policy"] = new_ep
    return merged


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------


def generate(
    session: dict,
    click_events: list[dict],
    url_events: list[dict],
    har: dict | None = None,
    auth_mode: str | None = None,
    manual_credentials: bool | None = None,
    manual_takeover: bool = False,
    post_login_url_pattern: str = "",
    keepalive_style: str = "goto",
) -> dict[str, Any]:
    """
    Generate an Application draft, ServiceProfile draft, and review items
    from a login recording session.

    Parameters
    ----------
    session:
        LoginSession as a dict (id, app_name, login_url, ...)
    click_events:
        List of ClickEvent dicts, chronological order
    url_events:
        List of UrlEvent dicts, chronological order.
        Each dict may have either:
          - noui format: from_url, to_url  (direct columns)
          - abcd format: metadata_json containing {"url": "...", "from_url": "..."}
    har:
        HAR object (or None)
    auth_mode:
        Runtime token mode hint ('platform_jwt' | 'agent_token'). Does NOT control
        credential storage — that is governed by manual_credentials/manual_takeover
        below, which default to manual (no stored secret).
    manual_credentials:
        True → `credential_ref: "manual:"` (no stored secret; human logs in via
        HITL). False → `credential_ref: "k8s:secret/..."` (stored credentials,
        explicit opt-in only). None (default) → manual.
    manual_takeover:
        True → manual: with a single VNC confirm step. Implies manual_credentials.

    Returns
    -------
    Full bundle dict.
    """
    # Credential model: `manual:` means no stored secret — the worker pod starts
    # without a K8s secret mount and the login is completed by a human via
    # HITL/VNC (request_human_input steps). We DEFAULT to manual and NEVER
    # provision a `k8s:secret` credential implicitly: a stored credential is only
    # emitted when the caller explicitly opts in with manual_credentials=False
    # (i.e. `--credential-mode stored`). This is independent of the runtime token
    # mode (auth_mode) — agent_token sessions are manual too unless opted in.
    if manual_credentials is None:
        manual_creds = True
    else:
        manual_creds = manual_credentials
    # Takeover implies no stored secret — the human logs in via VNC.
    if manual_takeover:
        manual_creds = True
    session_id = session.get("id", "")
    app_name = session.get("app_name") or "recorded-app"
    login_url = session.get("login_url") or ""
    review_items: list[dict] = []
    issues: list[str] = []

    # Normalize app_name to a slug for profile_id
    profile_id = re.sub(r"[^a-z0-9]+", "-", app_name.lower()).strip("-") or "recorded-app"

    # ---- Parse URL transitions ----
    # noui UrlEvent rows have from_url / to_url columns directly.
    # abcd TimelineEvent rows store them in metadata_json.
    url_transitions: list[tuple[str, str]] = []  # (from_url, to_url)
    for ev in url_events:
        # noui format: direct columns
        to_url = ev.get("to_url") or ""
        from_url = ev.get("from_url") or ""
        # abcd fallback: metadata_json
        if not to_url and ev.get("metadata_json"):
            try:
                meta = json.loads(ev["metadata_json"])
                to_url = meta.get("url") or ev.get("summary", "").replace("Navigated to ", "")
                from_url = meta.get("from_url") or from_url
            except Exception:
                pass
        if to_url:
            url_transitions.append((from_url, to_url))

    # First URL: either the session's login_url or the first observed URL
    first_url = login_url
    if not first_url and url_transitions:
        first_url = url_transitions[0][1]
    if not first_url:
        first_url = "https://example.com/login"
        issues.append("No login URL recorded — placeholder used")

    origin = _url_origin(first_url)
    domain = _url_domain(first_url)

    # ---- Map click events to DSL steps ----
    steps: list[dict[str, Any]] = []
    otp_selector: str | None = None
    has_otp = False
    password_selector: str | None = None
    submit_selector: str | None = None
    post_login_url: str | None = None

    # goto first URL
    steps.append({"action": "goto", "url": first_url})

    # Deduplicate consecutive input events for the same field — keep only the
    # last value per (selector, event_type) run so we don't emit partial
    # keystrokes recorded by the standard tracker.
    deduped: list[dict] = []
    for ev in click_events:
        if ev.get("event_type") in ("input", "change") and deduped:
            prev = deduped[-1]
            if (
                prev.get("event_type") in ("input", "change")
                and prev.get("element_id") == ev.get("element_id")
                and prev.get("field_name") == ev.get("field_name")
            ):
                deduped[-1] = ev  # replace with the later (more complete) value
                continue
        deduped.append(ev)
    click_events = deduped

    for ev in click_events:
        # Infer field_role from standard tracker signals when login recorder
        # was not used (field_role will be null from regular capture sessions).
        # Only infer on input/change events — click events on the same elements
        # should stay as clicks, not be converted to fill steps.
        field_role = ev.get("field_role") or ""
        event_type = ev.get("event_type") or "click"
        if not field_role and event_type in ("input", "change"):
            inp_type = (ev.get("input_type") or "").lower()
            elem_id = (ev.get("element_id") or "").lower()
            fname = (ev.get("field_name") or "").lower()
            ac = (ev.get("autocomplete") or "").lower()
            tag = (ev.get("tag_name") or "").lower()
            # Only classify INPUT elements, not buttons or other tags
            if tag in ("input", "textarea", ""):
                _username_signals = {"username", "email", "user", "mail"}
                _password_signals = {"password", "pass", "passwd", "pwd"}
                if (
                    inp_type == "password"
                    or ac in ("current-password", "new-password")
                    or any(s in fname or s in elem_id for s in _password_signals)
                ):
                    field_role = "password"
                elif (
                    inp_type in ("text", "email", "tel")
                    or ac in ("username", "email")
                    or any(s in fname or s in elem_id for s in _username_signals)
                ):
                    field_role = "username"
        selector = _build_selector(ev)
        confidence = _selector_confidence(ev)

        if confidence == "low":
            review_items.append(
                {
                    "type": "selector_confidence",
                    "severity": "warning",
                    "message": f"Low-confidence selector for {field_role or event_type} event: {selector!r}",
                    "selector": selector,
                    "confidence": "low",
                }
            )

        if field_role == "username":
            if manual_creds:
                # Per-user: the end-user supplies their own username/email live.
                steps.append(
                    {
                        "action": "request_human_input",
                        "input_type": "email",
                        "field_selector": selector,
                        "label": "Enter your username or email",
                        "timeout_ms": 120000,
                    }
                )
            else:
                steps.append(
                    {
                        "action": "fill",
                        "selector": selector,
                        "value": "${USERNAME}",
                    }
                )
        elif field_role == "password":
            password_selector = selector
            if manual_creds:
                # Per-user: the end-user supplies their own password live; nothing
                # is stored. sensitive=true suppresses screenshots of this step.
                steps.append(
                    {
                        "action": "request_human_input",
                        "input_type": "password",
                        "field_selector": selector,
                        "label": "Enter your password",
                        "sensitive": True,
                        "timeout_ms": 120000,
                    }
                )
            else:
                steps.append(
                    {
                        "action": "fill",
                        "selector": selector,
                        "value": "${PASSWORD}",
                        "sensitive": True,
                    }
                )
        elif field_role == "otp":
            has_otp = True
            otp_selector = selector
            # Do NOT generate fill ${OTP} — use wait_for with sensitive: true
            steps.append(
                {
                    "action": "wait_for",
                    "selector": selector,
                    "timeout_ms": 120000,
                    "sensitive": True,
                }
            )
        elif field_role == "unknown_sensitive":
            steps.append(
                {
                    "action": "wait_for",
                    "selector": selector,
                    "timeout_ms": 60000,
                    "sensitive": True,
                }
            )
            review_items.append(
                {
                    "type": "unresolved_sensitive_field",
                    "severity": "warning",
                    "message": f"Unknown sensitive field detected — review and classify: {selector!r}",
                    "selector": selector,
                }
            )
        elif event_type == "click":
            tag = (ev.get("tag_name") or "").lower()
            inp_type = (ev.get("input_type") or "").lower()
            text = (ev.get("text_content") or "").lower()
            # Skip clicks on plain input fields (user focusing before typing) —
            # the fill step covers those interactions.
            if tag == "input" and inp_type in ("text", "email", "password", "tel", ""):
                continue
            # Likely a submit or nav click
            step = {"action": "click", "selector": selector}
            if tag in ("button", "input") and (
                inp_type == "submit" or text in ("login", "sign in", "log in", "submit", "continue")
            ):
                submit_selector = selector
            steps.append(step)
        elif event_type in ("input", "change"):
            # Non-role-labeled input (e.g. remember-me checkbox, tenant select)
            if ev.get("is_redacted"):
                # Skip — we don't know what this is
                review_items.append(
                    {
                        "type": "redacted_field",
                        "severity": "info",
                        "message": f"Redacted field with no role detected — review: {selector!r}",
                        "selector": selector,
                    }
                )
            else:
                val = ev.get("value") or ""
                tag_lower = (ev.get("tag_name") or "").lower()
                if tag_lower == "select":
                    steps.append({"action": "select", "selector": selector, "value": val})
                elif val:
                    steps.append({"action": "fill", "selector": selector, "value": val})
        elif event_type == "submit":
            # Skip form submit events when a submit button click was already
            # recorded — the button click navigates away and the form is gone
            # by the time a second click step would run.
            if not submit_selector:
                steps.append({"action": "click", "selector": selector})

    # After OTP wait, if there's a submit needed
    if has_otp:
        # Add click on submit after OTP if we have a submit selector
        if submit_selector:
            steps.append({"action": "click", "selector": submit_selector})
        elif password_selector:
            # Guess: try [type='submit']
            steps.append({"action": "click", "selector": "[type='submit']"})
            review_items.append(
                {
                    "type": "otp_submit_inferred",
                    "severity": "warning",
                    "message": "OTP submit step inferred — verify the correct submit selector",
                }
            )

    # Determine post-login success condition
    # Find the last stable URL after the login sequence
    stable_urls = [
        to_url
        for from_url, to_url in url_transitions
        if not _is_redirect_hop(from_url, to_url) and to_url != first_url
    ]
    if stable_urls:
        # The landing page is the end of the FIRST same-origin run of stable URLs,
        # not the last URL of the recording.
        #
        # stable_urls[-1] took wherever the human finished. On a combined capture
        # that is the end of the *workflow*, and on a multi-host portal it is a
        # different host entirely — ICICI retail login produced
        # "https://infinity.icici.bank.in/corp**" (the CORPORATE/Finacle portal),
        # a URL a retail login can never reach. wait_for_url then timed out on every
        # single session and fell through to request_help, so the human was asked to
        # "finish logging in" after they already had, and nothing ever verified the
        # login.
        #
        # Taking stable_urls[0] outright would be wrong too: logins commonly settle
        # through one more same-origin bounce (classify.py documents Expedia's
        # /onboarding?originUrl=… -> /?challengeReferer=noref). So walk forward while
        # the origin holds and stop at the first origin change — that change is the
        # human navigating on, or an SSO hand-off that has already completed.
        post_login_url = stable_urls[0]
        landing_origin = _url_origin(post_login_url)
        for candidate in stable_urls[1:]:
            if _url_origin(candidate) != landing_origin:
                break
            post_login_url = candidate
        # Add wait_for_url step — Tabby requires a "pattern" key.
        # Ensure the URL has a scheme before parsing (bare host:port strings
        # confuse urlparse, making the host land in scheme).
        _raw = post_login_url
        if not _raw.startswith("http://") and not _raw.startswith("https://"):
            _raw = "http://" + _raw
        parsed = urlparse(_raw)
        url_pattern = f"{parsed.scheme}://{parsed.netloc}/**"
        steps.append(
            {
                "action": "wait_for_url",
                "pattern": url_pattern,
                "timeout_ms": 30000,
            }
        )
    else:
        review_items.append(
            {
                "type": "no_post_login_url",
                "severity": "warning",
                "message": "Could not determine post-login URL — add a wait_for or wait_for_url step manually",
            }
        )

    # ---- Build credential_ref ----
    # Scenario A (platform_jwt): 'manual:' — no stored credentials; the recorded
    # request_human_input steps escalate to the end-user at session creation.
    # Scenario B (agent_token): a K8s Secret holds reusable service credentials.
    secret_name = f"tabby-{profile_id}"
    credential_ref = "manual:" if manual_creds else f"k8s:secret/{secret_name}"

    # ---- Manual-takeover override ----
    # For VNC manual login the worker shouldn't drive the form — it opens the
    # login page and hands control to the human, who logs in and clicks
    # "Mark as Resolved" (a single confirm-type request_human_input). This is the
    # pattern Tabby's bare VNC viewer supports; per-field request_human_input
    # expects Slack/MCP-delivered values instead.
    if manual_takeover:
        # Salesforce-template structure (the supported VNC manual-login flow):
        #   goto → request_human_input(confirm) → wait_for_url(post-login pattern).
        # Reaching the post-login URL auto-resolves the HITL (Tabby marks it
        # resolved) so the user usually need not click "Mark as Resolved" — this
        # is what avoids the repeated-click complaint. on_failure falls back to a
        # confirm if the URL can't be auto-verified.
        takeover_steps: list[dict[str, Any]] = [
            {"action": "goto", "url": first_url},
            {
                "action": "request_human_input",
                "input_type": "confirm",
                "label": (
                    "Open the VNC viewer and log in (complete any MFA/OTP). The flow "
                    "continues automatically once you reach the logged-in page; otherwise "
                    "click 'Mark as Resolved'."
                ),
                "timeout_ms": 1200000,
            },
        ]
        # Resolve the post-login pattern: explicit wins; else derive from the
        # recording's landing URL — but ONLY if it actually distinguishes the
        # logged-in page from the login page (else it would match immediately and
        # falsely auto-resolve). Same-origin root-path apps need an explicit pattern.
        pattern = post_login_url_pattern
        if not pattern and post_login_url:
            # post_login_url, not stable_urls[-1]: the latter is wherever the human
            # finished, which on a multi-host portal is a different host than the
            # login lands on. That produced ICICI's
            # "https://infinity.icici.bank.in/corp**" — the corporate portal — for a
            # retail login, so auto-resolve could never fire and every session fell
            # through to request_help.
            pattern = _derive_post_login_pattern(first_url, post_login_url)
        if pattern:
            takeover_steps.append(
                {
                    "action": "wait_for_url",
                    "pattern": pattern,
                    "timeout_ms": 30000,
                    "retry_count": 0,
                    # skip, not request_help. This step is preceded by a human-attested
                    # `request_human_input` confirm, so a second prompt asks the human to
                    # re-answer a question they already answered — and the pattern it
                    # guards is only ever ONE observed landing route, which portals do not
                    # promise to be stable. ICICI recorded a landing on /dashboard and then
                    # signed real users in to /credit-card, so the glob could never match:
                    # every session timed out for 30s, flipped health to AUTH_FAIL, and
                    # demanded a second "Mark as Resolved" from someone already logged in.
                    # Matching stays a fast-path confirmation; failing to match is not
                    # evidence of a failed login, and the keepalive health check is the
                    # authority on whether the session is really authenticated.
                    "on_failure": {"action": "skip"},
                }
            )
        else:
            review_items.append(
                {
                    "type": "no_autoresolve_pattern",
                    "severity": "info",
                    "message": (
                        "No trustworthy post-login URL pattern (the recorded landing URL "
                        "is still a login-flow page, or shares the login page's route), so "
                        "no auto-resolve wait_for_url was added — the user clicks 'Mark as "
                        "Resolved' to continue, which always works. Pass "
                        "post_login_url_pattern (a glob the logged-in URL matches but the "
                        "login page does not) to enable auto-resolve."
                    ),
                }
            )
        steps = takeover_steps

    # ---- Build login_config ----
    login_config: dict[str, Any] = {
        "login_url": first_url,
        "credential_ref": credential_ref,
        "steps": steps,
    }
    if has_otp and otp_selector:
        login_config["otp_prompt"] = {
            "method": "chat",
            "field_selector": otp_selector,
            "timeout_ms": 120000,
        }

    # ---- Analyze HAR ----
    har_analysis = _analyze_har(har)

    # Non-cookie auth headers (e.g. a client-managed bearer token) are only ever
    # attached on requests to the *app* the login lands on, not the login flow
    # itself — so target_urls/target_domains must cover the post-login origin
    # too, not just where the login started. Without this, Tabby's
    # request-header-capture listener (export_policy.request_header_allowlist,
    # matched against target_urls) never observes a matching request and the
    # declared header never gets a captured value (see auth_plan.py's
    # login_credential_headers doc for the full picture).
    has_dynamic_headers = bool(har_analysis["auth_header_names"])
    post_login_origin = _url_origin(post_login_url) if post_login_url else ""
    post_login_domain = _url_domain(post_login_url) if post_login_url else ""
    # Tabby's request-header-capture matcher (artifact-extractor.ts's
    # buildUrlMatcher) converts each target_urls glob to a FULLY-ANCHORED regex
    # (`^...$`) — a bare origin like "https://x.com" only matches that exact
    # string, never "https://x.com/api/...". A "/**" suffix is required for any
    # real request path to match. Verified live: capture stayed empty until
    # this suffix was added, even with a correct request_header_allowlist.
    target_urls = [f"{u}/**" for u in dict.fromkeys([origin, post_login_origin]) if u]
    target_domains_list = [d for d in dict.fromkeys([domain, post_login_domain]) if d]

    # ---- Infer keepalive ----
    keepalive_actions: list[dict] = []
    keepalive_health_checks: list[dict] = []

    # A health check must be able to FAIL, or the session's health signal is a
    # constant. The previous dom_check on "body" could not: <body> is present on
    # the login page exactly as on the dashboard, so a session that never signed
    # in — or whose cookies expired — still reported HEALTHY/PASS forever. Every
    # downstream consumer then believed the session was authenticated and only
    # discovered otherwise as 401/403 from the target.
    #
    # Prefer a url_check against the landing page: Tabby's runner returns AUTH_FAIL
    # when the request lands on an auth URL, which is what drives re-login. The
    # explicit auth_redirect_pattern matters because the built-in heuristic only
    # matches login|signin|sso|oauth|saml|authgw|identity in the host+path, so a
    # bounce to a bare "/" root goes undetected without one.
    if post_login_url:
        _login_path = urlparse(login_url).path.rstrip("/") if login_url else ""
        _auth_patterns = [re.escape(_login_path)] if _login_path and _login_path != "" else []
        # Login routes, plus the session-expiry landing pages portals bounce to
        # when the cookie dies. Those are the ones that actually bite: a bank
        # serves them with HTTP 200 and a path that contains no auth-looking
        # word, so both expect_status and the login patterns above are satisfied
        # and health reports PASS on a page that says "Your session has expired".
        # Observed on ICICI (/session-expire), which sat HEALTHY for 41 minutes —
        # and because the controller only opens a HITL step on AUTH_FAIL, the
        # human had no "Mark as Resolved" button to recover with.
        _auth_patterns += [
            "/login",
            "/signin",
            "/sign-in",
            "/sso",
            "/auth",
            "/session-expire",
            "/session-expired",
            "/sessionexpired",
            "/session-timeout",
            "/sessiontimeout",
            "/expired",
            "/timeout",
            "/logout",
        ]
        keepalive_health_checks.append(
            {
                "type": "url_check",
                "url": post_login_url,
                "expect_status": 200,
                "auth_redirect_pattern": "|".join(dict.fromkeys(_auth_patterns)),
                "timeout_ms": 15000,
            }
        )
    else:
        # No landing page inferred, so there is nothing meaningful to probe. Keep
        # the old dom_check rather than invent a URL, and flag it: this session's
        # health signal cannot detect a lost login.
        keepalive_health_checks.append(
            {
                "type": "dom_check",
                "selector": "body",
                "exists": True,
            }
        )
        review_items.append(
            {
                "type": "unverifiable_health_check",
                "severity": "warning",
                "message": (
                    "No post-login URL inferred, so health falls back to dom_check on "
                    "body — which always passes. This session will report HEALTHY even "
                    "when signed out; add a url_check against an authenticated page."
                ),
            }
        )

    # Two keepalive styles, chosen by the skill kind (see keepalive_style):
    #
    #   "activity" (browser-driven default) — a small trusted mouse-move + scroll
    #     every 60s. Portals detect idle via DOM interaction events (mousemove/
    #     scroll reset a client-side countdown that redirects to /session-expire),
    #     NOT HTTP — a page left idle died in <=90s, but the activity nudge held
    #     ICICI HEALTHY for 11+ minutes. 60s is the tightest interval Tabby
    #     accepts (validateKeepaliveConfig rejects interval_seconds < 60); it fires
    #     comfortably before the ~90s idle death. Safe on any page (no clicks/keys/
    #     navigation; Playwright input is trusted), so it never disrupts the user
    #     or trips a refresh-sensitive expiry. A browser skill reads the DOM, so it
    #     needs no header-capture navigation.
    #
    #   "goto" (HAR-replay default) — revisit the post-login page so there's
    #     guaranteed real traffic for the request-header-capture listener; without
    #     it a captured header can go stale (or never populate) between calls. This
    #     is what a call_web_api skill's dynamic bearers rely on.
    if keepalive_style == "activity":
        keepalive_actions.append({"action": "activity"})
        # 60s is Tabby's floor (dsl.validator rejects interval_seconds < 60) and
        # still fires well under the ~90s idle death. A tighter value fails app
        # validation → every call_web_browser 400s and the session never provisions.
        keepalive_interval = 60
    else:  # "goto"
        if has_dynamic_headers and post_login_url:
            if _has_volatile_query(post_login_url):
                # A URL whose token died with the recording is worse than nothing:
                # every interval it threw the live browser onto an expired-token
                # error page, discarding whatever the user had signed into.
                review_items.append(
                    {
                        "type": "keepalive_goto_skipped",
                        "severity": "warning",
                        "message": (
                            "The recorded landing URL carries one-time query material "
                            f"({post_login_url.split('?')[0]}?...), so no keepalive goto "
                            "was emitted — replaying it would navigate the live session "
                            "onto an expired-token error page. Header capture relies on "
                            "organic traffic; set a stable authenticated URL by hand if "
                            "the captured headers go stale, or compile browser-driven "
                            "(keepalive_style='activity')."
                        ),
                    }
                )
            else:
                keepalive_actions.append({"action": "goto", "url": post_login_url})
        keepalive_interval = 120

    keepalive_config: dict[str, Any] = {
        "interval_seconds": keepalive_interval,
        "actions": keepalive_actions,
        "health_checks": keepalive_health_checks,
        "policy": "all",
    }

    # ---- Infer export policy ----
    artifact_types: list[str] = ["cookies", "headers"]
    if har_analysis["has_csrf"]:
        artifact_types.append("csrf_token")

    export_policy: dict[str, Any] = {
        "artifact_types": artifact_types,
        "encryption": {"algo": "AES-256-GCM", "key_version": "v1"},
        "ttl_seconds": 3600,
        "target_urls": target_urls,
    }
    if has_dynamic_headers:
        # THE allowlist that turns header capture on. Without it the worker's
        # registerRequestHeaderCapture() early-returns and never attaches a
        # listener, so nothing is ever captured — no error, just an empty
        # bundle. Declaring credential_types.headers (below) only says which
        # captured headers to *surface*; it does not cause capture. Omitting
        # this while declaring those is silent breakage: /execute/fetch returns
        # 200 with no auth header attached and the target 401s/403s, which
        # reads as a login problem rather than a config one.
        #
        # Names are the literal ones observed in the HAR (see _analyze_har), so
        # the on-the-wire spelling is preserved — Tabby matches case-insensitively
        # but surfaces the configured casing back to the consumer.
        export_policy["request_header_allowlist"] = list(har_analysis["auth_header_names"])

        # Client-managed bearer/CSRF tokens are typically short-lived
        # (silent-refresh OAuth patterns); re-capture often enough to keep the
        # value usable. 180s sits in Tabby's own documented 120-300s guidance
        # for JWT-minting SPAs (tabby/CLAUDE.md gotcha #17) — the 3600s
        # worker-side default is far too slow for this class of header.
        export_policy["refresh_interval_seconds"] = 180

    # ---- Infer credential_types ----
    credential_types: dict[str, list] = {"cookies": [], "headers": []}
    if har_analysis["set_cookie_headers"]:
        credential_types["cookies"] = _cookie_credential_types(har_analysis["set_cookie_headers"])
    if har_analysis["auth_header_names"]:
        # Headers are tolerated as plain names by Tabby's consumer (only cookies
        # require the object shape), so they are left as a name list.
        credential_types["headers"] = har_analysis["auth_header_names"]

    # ---- Build Application draft ----
    application_draft: dict[str, Any] = {
        "name": app_name,
        "target_urls": target_urls,
        # Auth/CDN domains observed in the HAR, as egress suffix patterns, so the
        # compiled app's egress allowlist covers the full login flow under enforce.
        "extra_egress_allowlist": egress_allowlist_from_domains(har_analysis["auth_domains"]),
        "login_config": login_config,
        "keepalive_config": keepalive_config,
        "export_policy": export_policy,
        "notification_config": {"channels": ["slack:#local-dev"]},
        "desired_session_count": 0,
        # execute_enabled must be true or the K8s worker Service + pod
        # EXECUTE_ENABLED are never created and /execute/fetch 502s (defaults
        # false). Locally this is masked by the .env.local EXECUTE_ENABLED
        # override + LOCAL_WORKER_URL; in real K8s it is load-bearing. See A4.
        "execute_enabled": True,
    }

    # ---- Build ServiceProfile draft ----
    import time as _time

    t = _time.localtime()
    version = f"{t.tm_year % 100}.{t.tm_mon}.{t.tm_mday}"

    service_profile_draft: dict[str, Any] = {
        "profile_id": profile_id,
        "version": version,
        "login_config": login_config,  # identical to app at creation time
        "credential_types": credential_types,
        "target_domains": target_domains_list,
        "extra_config": {},
    }

    # ---- Validation ----
    # A credential field is satisfied by either a `fill ${USERNAME}/${PASSWORD}`
    # step (stored-secret mode) OR a `request_human_input` step of the matching
    # input_type (manual: mode — the human supplies it via HITL/VNC).
    generator_valid = True

    def _has_human_input(types: tuple[str, ...]) -> bool:
        return any(
            s.get("action") == "request_human_input" and s.get("input_type") in types for s in steps
        )

    has_username_step = any(s.get("value") == "${USERNAME}" for s in steps) or _has_human_input(
        ("email", "username")
    )
    has_password_step = any(s.get("value") == "${PASSWORD}" for s in steps) or _has_human_input(
        ("password",)
    )
    # In takeover mode the human logs in manually via VNC, so the recording need
    # not contain username/password fields — skip those checks.
    if not has_username_step and not has_otp and not manual_takeover:
        issues.append("No username field detected in recording")
    if not has_password_step and not has_otp and not manual_takeover:
        issues.append("No password field detected in recording")
        generator_valid = False

    if issues:
        for issue in issues:
            review_items.append(
                {
                    "type": "generator_issue",
                    "severity": "error",
                    "message": issue,
                }
            )

    return {
        "recording": {
            "session_id": session_id,
            "source": "elicitation-login-recorder",
        },
        "application_draft": application_draft,
        "service_profile_draft": service_profile_draft,
        "review_items": review_items,
        "validation": {
            "generator_valid": generator_valid,
            "issues": issues,
        },
    }
