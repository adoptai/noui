"""Steps discovered at replay live apart from the ones that were recorded.

operations.json is what a human was observed doing, and the digest over it makes
that claim checkable. When a compiled locator died at replay, the agent wrote the
control it found INTO operations.json — destroying the provenance, getting the
whole skill refused, and losing the discovery along with the evidence.

The discovery was not worthless. It was a different KIND of evidence.
"""

from noui_core.compile.amendments import amendment_id, describe, load

AMEND = {
    "operation": "download_statement",
    "step_index": 3,
    "why": "the recorded css path matched nothing",
    "replacement": {"command": "click_element", "params": {"selector": "#new-btn"}},
}


def test_the_id_matches_the_harness_implementation():
    # Pinned in both repos: if they drift, an approval can never be matched to
    # the amendment it approved, and every amended skill is refused forever.
    assert amendment_id(AMEND) == "c9c973553b09f380"


def test_the_id_changes_when_the_replacement_does():
    other = dict(
        AMEND, replacement={"command": "click_element", "params": {"selector": "#something-else"}}
    )
    assert amendment_id(other) != amendment_id(AMEND)


def test_a_malformed_file_reads_as_no_amendments():
    # An amendment nobody can interpret must never become one nobody reviewed.
    assert load("{not json") == []
    assert load(None) == []
    assert load('{"amendments": "nonsense"}') == []


def test_an_entry_without_a_replacement_is_not_an_amendment():
    import json

    assert load(json.dumps({"amendments": [{"operation": "x"}]})) == []


def test_it_describes_an_amendment_in_one_line_a_member_can_decide_on():
    text = describe(AMEND)
    assert "download_statement step 3" in text
    assert "#new-btn" in text
    assert "matched nothing" in text


# --- verify_replay records amendments without touching operations.json -------


def _skill(tmp_path):
    import json

    from noui_core.compile.provenance import steps_digest

    ops = [
        {
            "name": "dl",
            "kind": "download",
            "tool": "call_web_browser",
            "steps": [
                {"command": "click_element", "params": {"selector": "#old"}},
                {"command": "list_downloads"},
            ],
        }
    ]
    (tmp_path / "operations.json").write_text(json.dumps({"operations": ops}))
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "runtime": {"operation_style": "browser"},
                "provenance": {"steps_sha256": steps_digest(ops), "bundle_sha256": "x"},
            }
        )
    )
    return tmp_path


def _run_amend(tmp_path, *amend_json):
    import subprocess
    import sys
    from pathlib import Path

    script = Path("skills/noui/scripts/verify_replay.py").resolve()
    argv = [sys.executable, str(script), str(tmp_path), "--profile-slug", "none"]
    for a in amend_json:
        argv += ["--amend", a]
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path("skills/noui").resolve())},
    )


def test_an_amendment_never_edits_the_compiled_operations(tmp_path):
    """THE property. Editing operations.json destroys the digest that proves the
    rest came from a recording — which is what three builds did, losing the
    discovery along with the evidence when the gate then refused the skill."""
    import json

    from noui_core.compile.provenance import steps_digest

    d = _skill(tmp_path)
    _run_amend(
        d,
        json.dumps(
            {
                "operation": "dl",
                "step_index": 0,
                "replacement": {"command": "click_element", "params": {"selector": "#new"}},
                "why": "#old matched nothing",
            }
        ),
    )

    ops = json.loads((d / "operations.json").read_text())["operations"]
    manifest = json.loads((d / "manifest.json").read_text())
    assert ops[0]["steps"][0]["params"]["selector"] == "#old"
    assert steps_digest(ops) == manifest["provenance"]["steps_sha256"]
    assert len(json.loads((d / "amendments.json").read_text())["amendments"]) == 1


def test_an_amendment_naming_an_unknown_operation_is_refused(tmp_path):
    import json

    d = _skill(tmp_path)
    r = _run_amend(
        d,
        json.dumps(
            {
                "operation": "nope",
                "step_index": 0,
                "replacement": {"command": "click_element", "params": {"selector": "#x"}},
            }
        ),
    )
    assert r.returncode == 1
    assert "No operation named" in r.stderr
    assert not (d / "amendments.json").exists()


def test_an_amendment_out_of_range_is_refused(tmp_path):
    import json

    d = _skill(tmp_path)
    r = _run_amend(
        d,
        json.dumps(
            {
                "operation": "dl",
                "step_index": 9,
                "replacement": {"command": "click_element", "params": {"selector": "#x"}},
            }
        ),
    )
    assert r.returncode == 1
    assert "out of range" in r.stderr


# --- an amendment is only worth what the replay proved about it ----------------
#
# Run 050970d3: one control confirmed by hand on /overview, then step 0 amended
# across six operations. Two ran; four were blocked long before reaching the
# amended step. All six were written as "confirmed working in live session".

from noui_core.compile.amendments import is_verified  # noqa: E402


def test_an_amendment_is_unverified_until_the_replay_runs_it():
    assert is_verified(AMEND) is False
    assert is_verified({**AMEND, "verified": False}) is False
    assert is_verified({**AMEND, "verified": True}) is True


def test_describe_says_when_nothing_exercised_the_step():
    # The member reads this line and nothing else. It must not read the same for
    # a step that ran and a step that never happened.
    assert "NOT EXERCISED" in describe(AMEND)
    assert "NOT EXERCISED" not in describe({**AMEND, "verified": True})
    assert "ran OK" in describe({**AMEND, "verified": True})


def test_the_verified_flag_does_not_change_the_id():
    # Ids are how an approval names what it approved. If replaying an amendment
    # renamed it, the member's earlier decision would silently detach.
    assert amendment_id({**AMEND, "verified": True}) == amendment_id(AMEND)
