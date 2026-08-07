"""Detect apps whose requests cannot be replayed, so the compiler can route them
to a browser-driven skill instead of a doomed HAR-replay one.

Some single-page apps encrypt every request body in the page's JavaScript with a
per-session key fetched at load time, and/or stamp each request with rotating
per-request headers. A recorded request is then unreusable: the body is an opaque
`{data, key}` blob only the live page can produce, and the headers are dead the
moment the recording ends. ICICI is the canonical case — a HAR-replay skill
compiles 40 operations that all 403, and even a perfect capture of the target
endpoint is dead on arrival.

The fingerprint is visible in the recording itself (this is the same evidence a
human reads to diagnose it):
  - a key-fetch endpoint (``/getKeys`` and friends) handing out session crypto, and
  - request bodies that are opaque encryption envelopes (``{data, key}``).

When it fires, the app should be compiled browser-driven (drive the page, read the
rendered DOM) rather than by replay.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

# Path fragments of endpoints that hand out per-session crypto material.
_KEY_PATH_MARKERS = ("getkeys", "getpublickey", "publickey", "/pubkey", "/session/keys")

# A request body is an "opaque encryption envelope" when it is a JSON object whose
# keys are ALL drawn from this set — i.e. it carries nothing but ciphertext and a
# wrapped key. A normal API body has domain fields (amount, id, query...) too, so
# it won't be a subset of this set.
_ENVELOPE_KEYS = frozenset(
    {"data", "key", "encrypteddata", "encrypted_data", "payload", "enc", "iv", "tag", "nonce"}
)

# Minimum opaque-body POSTs before the encrypted-body signal counts. One or two
# could be incidental; a workflow that genuinely can't be replayed has many.
_MIN_ENVELOPE_POSTS = 3


def _origin(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _is_envelope_shape(keys: list, long_values: bool) -> bool:
    """Envelope test from a body's SHAPE rather than its bytes.

    Tabby reduces `workflow` HARs to metadata — no bodies, no headers, no query
    strings — because a browser skill never replays a request and a bank
    portal's payloads (balances, PANs, live tokens) have no business sitting in
    a recording bundle. It keeps the top-level field NAMES and a flag for whether
    any value was a long string, which is precisely what the text-based test
    below reduces to: names ⊆ envelope set, and at least one ciphertext-looking
    value.

    Without this, reducing the HAR would silently disable browser-vs-replay
    auto-detection: every workflow recording would look replayable, and apps like
    ICICI would compile back into the 40-operations-that-all-403 skill.
    """
    if not keys or not long_values:
        return False
    names = {str(k).lower() for k in keys}
    return bool(names) and names <= _ENVELOPE_KEYS


def _is_encryption_envelope(text: str) -> bool:
    """True when ``text`` is a JSON object carrying only ciphertext/wrapped-key
    fields — the shape a page produces when it encrypts the real payload in JS."""
    if not text:
        return False
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return False
    if not isinstance(body, dict) or not body:
        return False
    keys = {str(k).lower() for k in body}
    if not keys <= _ENVELOPE_KEYS:
        return False
    # Guard against a trivially-empty {"data": ""}: require the values to actually
    # look like ciphertext (long, mostly non-space).
    return any(isinstance(v, str) and len(v) >= 16 for v in body.values())


def detect_unreplayable(har: dict | None, *, app_origin: str = "") -> dict:
    """Inspect a recording's HAR for the unreplayable fingerprint.

    ``app_origin`` (scheme://host of the app's own pages) scopes the encrypted-body
    count to first-party requests, so a third party's encrypted telemetry can't
    trip the detector. Pass "" to consider all origins.

    Returns a report dict; ``report["unreplayable"]`` is the decision.
    """
    entries = ((har or {}).get("log") or {}).get("entries") or []
    key_endpoints: list[str] = []
    envelope_posts = 0
    app_posts = 0

    for e in entries:
        req = e.get("request") or {}
        url = req.get("url") or ""
        path = urlparse(url).path.lower()
        if any(m in path for m in _KEY_PATH_MARKERS):
            key_endpoints.append(path)
        if (req.get("method") or "").upper() != "POST":
            continue
        if app_origin and _origin(url) != app_origin:
            continue
        app_posts += 1
        post = req.get("postData") or {}
        text = post.get("text") or ""
        if text:
            is_envelope = _is_encryption_envelope(text)
        else:
            # A metadata-reduced HAR (Tabby workflow bundles) carries the body's
            # shape instead of its bytes. Same test, same verdict.
            is_envelope = _is_envelope_shape(
                post.get("keys") or [], bool(post.get("long_values"))
            )
        if is_envelope:
            envelope_posts += 1

    ratio = (envelope_posts / app_posts) if app_posts else 0.0
    key_present = bool(key_endpoints)

    # Fire when the app clearly encrypts its bodies in-page. Two ways to be sure:
    #   - enough opaque envelopes AND a key-fetch endpoint (the full ICICI shape); or
    #   - enough opaque envelopes that are a large fraction of first-party POSTs
    #     (encryption is the norm here, not an outlier), even absent a named key
    #     endpoint (some apps mint keys inline).
    unreplayable = envelope_posts >= _MIN_ENVELOPE_POSTS and (key_present or ratio >= 0.25)

    reasons: list[str] = []
    if unreplayable:
        if key_present:
            reasons.append(
                f"page fetches session crypto from a key endpoint "
                f"({', '.join(sorted(set(key_endpoints))[:3])})"
            )
        reasons.append(
            f"{envelope_posts} of {app_posts} first-party POST bodies are opaque "
            f"encryption envelopes ({{data,key}}), which cannot be replayed"
        )

    return {
        "unreplayable": unreplayable,
        "reasons": reasons,
        "key_endpoints": sorted(set(key_endpoints)),
        "encrypted_post_count": envelope_posts,
        "app_post_count": app_posts,
        "encrypted_ratio": round(ratio, 3),
    }


def recommendation_message(report: dict, *, app_name: str = "this app") -> str:
    """A one-paragraph, human-facing explanation of why browser mode was chosen —
    surfaced to the operator/user so the choice isn't silent."""
    why = "; ".join(report.get("reasons") or ["requests appear to be encrypted in-page"])
    return (
        f"{app_name} encrypts its requests in the browser, so a recorded request "
        f"cannot be replayed ({why}). Compiling as a BROWSER-DRIVEN skill: it drives "
        f"the page and reads the rendered data via call_web_browser, instead of "
        f"replaying API calls that would all fail with 403. Pass --no-auto-browser "
        f"to force the legacy replay compile."
    )
