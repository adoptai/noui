"""Pre-capture check: does a similar App Template already exist for this login?

Runs before a **login** recording starts (see ``capture_record.py``) to avoid
capturing a duplicate login for a site that's already registered. A match
requires the candidate URL's origin to overlap one of the template's known
origins (``login_config.login_url`` or ``export_policy.target_urls`` — see
``login_assets.py::generate``) AND the given name to be similar to the
template's ``name``. Both signals are required: same origin alone is common
(multiple logins can share a host), and name similarity alone proves nothing
about the site.
"""

from __future__ import annotations

import difflib
from urllib.parse import urlparse

from noui_core import tabby_client

NAME_SIMILARITY_THRESHOLD = 0.6


def _origin(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}".lower()
    except Exception:
        return url.lower()


def _template_origins(template: dict) -> set[str]:
    origins = set()
    login_url = (template.get("login_config") or {}).get("login_url") or ""
    if login_url:
        origins.add(_origin(login_url))
    for pattern in (template.get("export_policy") or {}).get("target_urls") or []:
        # target_urls entries are "<origin>/**" (see login_assets.generate) — strip
        # the glob suffix back down to an origin before comparing.
        origins.add(_origin(pattern.rstrip("*/")))
    return {o for o in origins if o}


def find_similar_templates(
    name: str,
    url: str,
    token: str,
    *,
    name_threshold: float = NAME_SIMILARITY_THRESHOLD,
) -> list[dict]:
    """Return existing App Templates that look like duplicates of (name, url).

    Each returned template dict has an added ``_name_score`` (difflib ratio,
    case-insensitive), sorted highest-first. Best-effort: returns ``[]``
    (never raises) if Tabby is unreachable or the lookup fails — this is a
    pre-flight nicety, not a required gate on recording.
    """
    if not name or not url:
        return []
    candidate_origin = _origin(url)
    if not candidate_origin:
        return []
    try:
        templates = tabby_client.list_app_templates(token)
    except RuntimeError:
        return []

    name_norm = name.strip().lower()
    matches = []
    for t in templates:
        if candidate_origin not in _template_origins(t):
            continue
        t_name = (t.get("name") or "").strip().lower()
        if not t_name:
            continue
        score = difflib.SequenceMatcher(None, name_norm, t_name).ratio()
        if score >= name_threshold or name_norm in t_name or t_name in name_norm:
            matches.append({**t, "_name_score": score})
    matches.sort(key=lambda t: t["_name_score"], reverse=True)
    return matches
