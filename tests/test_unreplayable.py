"""Tests for unreplayable-app detection (noui_core.compile.unreplayable).

Fires on apps that encrypt request bodies in-page (opaque {data,key} envelopes +
a key-fetch endpoint) — the ICICI fingerprint — so the compiler routes them to a
browser-driven skill instead of a doomed HAR-replay one. Must NOT fire on a
normal cookie-auth REST app whose bodies carry real domain fields.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "noui"))

from noui_core.compile.unreplayable import (  # noqa: E402
    detect_unreplayable,
    recommendation_message,
)


def _post(url, body):
    # Include a valid response/timestamp so the entry is usable by BOTH the
    # detector (reads the request) and the HAR-replay compiler (needs a response).
    return {
        "startedDateTime": "2026-08-05T00:00:00.000Z",
        "request": {
            "method": "POST", "url": url,
            "headers": [{"name": "content-type", "value": "application/json"}],
            "postData": {"text": body},
        },
        "response": {"status": 200, "content": {"mimeType": "application/json", "text": "{}"}},
    }


def _get(url):
    return {
        "startedDateTime": "2026-08-05T00:00:00.000Z",
        "request": {"method": "GET", "url": url, "headers": []},
        "response": {"status": 200, "content": {"mimeType": "application/json", "text": "{}"}},
    }


def _har(entries):
    return {"log": {"entries": entries}}


ORIGIN = "https://bank.test"
CIPHER = "A" * 40  # stand-in ciphertext (>=16 chars)


def test_fires_on_encrypted_bodies_plus_key_endpoint():
    entries = [_get(f"{ORIGIN}/getKeys")]
    for i in range(5):
        entries.append(_post(f"{ORIGIN}/dashboardAPI/op{i}",
                             f'{{"data":"{CIPHER}","key":"{CIPHER}"}}'))
    r = detect_unreplayable(_har(entries), app_origin=ORIGIN)
    assert r["unreplayable"] is True
    assert r["key_endpoints"] == ["/getkeys"]
    assert r["encrypted_post_count"] == 5
    msg = recommendation_message(r, app_name="Bank")
    assert "browser-driven" in msg.lower() and "getkeys" in msg.lower()


def test_fires_on_high_envelope_ratio_without_named_key_endpoint():
    # Some apps mint keys inline (no /getKeys path), but nearly every body is an
    # opaque envelope — encryption is clearly the norm.
    entries = [_post(f"{ORIGIN}/api/op{i}", f'{{"data":"{CIPHER}","key":"{CIPHER}"}}')
               for i in range(6)]
    entries.append(_post(f"{ORIGIN}/api/plain", '{"q":"hello"}'))
    r = detect_unreplayable(_har(entries), app_origin=ORIGIN)
    assert r["unreplayable"] is True  # 6/7 envelopes, ratio >= 0.25


def test_does_not_fire_on_normal_rest_api():
    # Real domain fields in the bodies → replayable. Must stay HAR-replay.
    entries = [
        _post(f"{ORIGIN}/api/contacts", '{"name":"Ada","email":"a@x.com"}'),
        _post(f"{ORIGIN}/api/search", '{"query":"invoices","page":2}'),
        _post(f"{ORIGIN}/api/orders", '{"sku":"X1","qty":3}'),
        _get(f"{ORIGIN}/api/me"),
    ]
    r = detect_unreplayable(_har(entries), app_origin=ORIGIN)
    assert r["unreplayable"] is False
    assert r["encrypted_post_count"] == 0
    assert not r["reasons"]


def test_third_party_encrypted_telemetry_does_not_trip_it():
    # A first-party app with plain bodies, but a THIRD-PARTY analytics host posts
    # encrypted blobs. Origin scoping must keep the app classified replayable.
    entries = [
        _post(f"{ORIGIN}/api/contacts", '{"name":"Ada"}'),
        _post("https://analytics.vendor.com/ingest", f'{{"data":"{CIPHER}","key":"{CIPHER}"}}'),
        _post("https://analytics.vendor.com/ingest2", f'{{"data":"{CIPHER}","key":"{CIPHER}"}}'),
        _post("https://analytics.vendor.com/ingest3", f'{{"data":"{CIPHER}","key":"{CIPHER}"}}'),
    ]
    r = detect_unreplayable(_har(entries), app_origin=ORIGIN)
    assert r["unreplayable"] is False


def test_two_incidental_envelopes_below_threshold():
    entries = [_post(f"{ORIGIN}/api/op{i}", f'{{"data":"{CIPHER}","key":"{CIPHER}"}}')
               for i in range(2)]
    entries += [_post(f"{ORIGIN}/api/real{i}", '{"id":%d}' % i) for i in range(8)]
    r = detect_unreplayable(_har(entries), app_origin=ORIGIN)
    assert r["unreplayable"] is False  # only 2 envelopes, under _MIN_ENVELOPE_POSTS


def test_empty_or_missing_har_is_safe():
    assert detect_unreplayable(None)["unreplayable"] is False
    assert detect_unreplayable({})["unreplayable"] is False
    assert detect_unreplayable({"log": {"entries": []}})["unreplayable"] is False


def test_empty_data_envelope_not_counted():
    # {"data":""} is not ciphertext — must not count.
    entries = [_post(f"{ORIGIN}/api/op{i}", '{"data":"","key":""}') for i in range(5)]
    r = detect_unreplayable(_har(entries), app_origin=ORIGIN)
    assert r["encrypted_post_count"] == 0
    assert r["unreplayable"] is False


# --- auto-detection wired through compile_workflow_bundle --------------------

def _bundle(entries, url_events):
    return {"har": {"log": {"entries": entries}}, "click_events": [], "url_events": url_events}


def test_compile_auto_selects_browser_for_unreplayable(tmp_path):
    from noui_core.compile.workflow import compile_workflow_bundle

    entries = [_get(f"{ORIGIN}/getKeys")]
    entries += [_post(f"{ORIGIN}/dashboardAPI/op{i}", f'{{"data":"{CIPHER}","key":"{CIPHER}"}}')
                for i in range(5)]
    urls = [{"to_url": f"{ORIGIN}/login-page"}, {"to_url": f"{ORIGIN}/dashboard"}]
    res = compile_workflow_bundle(
        session_id="deadbeef1234", bundle=_bundle(entries, urls), name="bank",
        target="skill", profile_slug="bank-prof", start_url=f"{ORIGIN}/login-page",
        output_root=str(tmp_path), allow_unbound_profile=True,
        # browser_driven NOT passed — auto-detection must choose it
    )
    assert res["browser_detection"]["unreplayable"] is True
    assert res["skill"]["runtime"]["operation_style"] == "browser"


def test_compile_stays_replay_for_normal_app(tmp_path):
    from noui_core.compile.workflow import compile_workflow_bundle

    entries = [_post(f"{ORIGIN}/api/contacts", '{"name":"Ada","email":"a@x.com"}')]
    urls = [{"to_url": f"{ORIGIN}/login-page"}, {"to_url": f"{ORIGIN}/dashboard"}]
    res = compile_workflow_bundle(
        session_id="deadbeef1234", bundle=_bundle(entries, urls), name="crm",
        target="skill", profile_slug="crm-prof", start_url=f"{ORIGIN}/login-page",
        output_root=str(tmp_path), allow_unbound_profile=True,
    )
    assert res["browser_detection"]["unreplayable"] is False
    assert res["skill"]["runtime"]["operation_style"] != "browser"


def test_no_auto_browser_override_forces_replay(tmp_path):
    from noui_core.compile.workflow import compile_workflow_bundle

    entries = [_get(f"{ORIGIN}/getKeys")]
    entries += [_post(f"{ORIGIN}/dashboardAPI/op{i}", f'{{"data":"{CIPHER}","key":"{CIPHER}"}}')
                for i in range(5)]
    urls = [{"to_url": f"{ORIGIN}/login-page"}, {"to_url": f"{ORIGIN}/dashboard"}]
    res = compile_workflow_bundle(
        session_id="deadbeef1234", bundle=_bundle(entries, urls), name="bank",
        target="skill", profile_slug="bank-prof", start_url=f"{ORIGIN}/login-page",
        output_root=str(tmp_path), allow_unbound_profile=True,
        auto_detect_browser=False,  # explicit override
    )
    # detector never ran; the replay path was used
    assert res.get("browser_detection") is None
    assert res["skill"]["runtime"]["operation_style"] != "browser"
