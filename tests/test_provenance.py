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


# --- steps digest: pinned in BOTH repos, same reason as the bundle digest -----

SHARED_STEPS = [
    {
        "name": "download",
        "kind": "download",
        "steps": [{"command": "click_element", "params": {"selector": "#dl"}}],
        "parameters": [],
    }
]
SHARED_STEPS_DIGEST = "ede061aad69f99f7a5c075bd20370936f10fee595fdf2ccc6e075524255986dc"


def test_the_steps_digest_matches_the_harness_implementation():
    from noui_core.compile.provenance import steps_digest

    assert steps_digest(SHARED_STEPS) == SHARED_STEPS_DIGEST


def test_renaming_an_operation_does_not_change_the_steps_digest():
    """A gate that fires on a wording fix is one people learn to route around."""
    from noui_core.compile.provenance import steps_digest

    renamed = [dict(SHARED_STEPS[0], name="download_annual", description="Nicer words")]
    assert steps_digest(renamed) == SHARED_STEPS_DIGEST


def test_reassembling_steps_changes_the_digest():
    """The hole this closes: same locator, different workflow.

    Told its selectors were unobserved, a build harvested the locators that WOULD
    pass and rewrote the steps around them. Every locator then appeared in the
    recording, so the set check went green while the workflow was no longer the
    compiled one.
    """
    from noui_core.compile.provenance import steps_digest

    reassembled = [
        dict(
            SHARED_STEPS[0],
            steps=[
                {"command": "click_element", "params": {"selector": "#dl"}},
                {"command": "click_element", "params": {"selector": "#dl"}},
            ],
        )
    ]
    assert steps_digest(reassembled) != SHARED_STEPS_DIGEST
