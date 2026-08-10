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
