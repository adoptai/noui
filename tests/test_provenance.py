"""Provenance binds a compiled browser skill to the recording behind it.

The digest vector is pinned in BOTH repos (adoptai-workflows:
tests/agent_harness/test_web_browser_dispatch.py). The installer refuses a skill
whose recording does not hash to the stamp, so if the two implementations ever
drift apart every real skill is refused -- and without this pin, nothing would
say why.
"""

from noui_core.compile.provenance import build, bundle_digest, observed_selectors

RECORDING = {
    "session_id": "sess-1",
    "schema_version": 5,
    "click_events": [{"seq": 1, "locator": {"value": "#dl", "kind": "css", "is_css": True}}],
    "url_events": [{"seq": 0, "to_url": "https://bank.test/statements"}],
}

SHARED_BUNDLE_DIGEST = "7ada3d2f1371dc12fa5ecbb8a8484b627b414523c90e0b02459497b89ff4509a"


def test_the_digest_matches_the_harness_implementation():
    assert bundle_digest(RECORDING) == SHARED_BUNDLE_DIGEST


def test_the_digest_changes_when_the_recording_does():
    tampered = dict(RECORDING, click_events=[{"seq": 1, "locator": {"value": "#other"}}])
    assert bundle_digest(tampered) != SHARED_BUNDLE_DIGEST


def test_observed_selectors_collects_every_form_a_step_might_use():
    rec = {
        "click_events": [
            {"candidates": [{"kind": "role_name", "value": "button|Download"}]},
            {"locator": {"value": "#dl"}},
            {"text": "Past Statements"},
        ]
    }
    seen = observed_selectors(rec)
    # role_name candidates are "role|name"; a click_by_text step keeps only the name.
    assert {"button|Download", "Download", "#dl", "Past Statements"} <= seen


def test_build_stamps_what_the_installer_verifies():
    prov = build(RECORDING, bundle_file="recording_bundle.json")
    assert prov["bundle_sha256"] == SHARED_BUNDLE_DIGEST
    assert prov["bundle_file"] == "recording_bundle.json"
    assert prov["recording_session_id"] == "sess-1"
    assert prov["source"] == "recording"
