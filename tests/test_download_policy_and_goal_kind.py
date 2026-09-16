"""Three fixes that together stop a working download reading as a broken step.

Real build, org 87451b06: the recorded click DID produce a file (the recorder
attributed one within its 120s window, so the compiled step carries
`expect.download: true`). At replay `list_downloads` returned `[]` every time,
the step was marked blocked with "no file arrived", and the member was asked to
waive the one operation they came for.

Two independent causes, plus a false alarm that pointed the agent at the wrong
explanation:
  * the App Template had browser_policy.downloads off, so the worker cancelled
    every download silently (F4/F5);
  * replay gave the file one 2.5s retry where recording allowed 120s (F5);
  * the import read manifest entries that carried no `kind`, so it announced
    "captured no download" about a recording that had one (F3).
"""

from noui_core.compile.browser_skill import manifest_op_entries
from noui_core.compile.goals import goal_coverage_warning, infer_primary_goal
from noui_core.verify.replay import expectation_unmet

PAGES = [{"name": "read_credit_card", "url": "https://bank.test/credit-card"}]
TERMINALS = [{"name": "download", "kind": "download", "url": "https://bank.test/credit-card"}]


# ---- F3: the import's goal check sees the download ------------------------


def test_manifest_entries_carry_kind():
    entries = manifest_op_entries(PAGES, TERMINALS)
    assert [e["kind"] for e in entries] == ["read", "download"]


def test_a_recording_with_a_download_is_not_reported_as_goalless():
    """THE REGRESSION: manifest entries had no `kind`, so every entry read as
    "read" and a recording that downloaded a file printed
    "This recording demonstrated no goal: it captured no download"."""
    entries = manifest_op_entries(PAGES, TERMINALS)
    assert goal_coverage_warning(entries) is None
    goal = infer_primary_goal(entries)
    assert goal["name"] == "download" and goal["kind"] == "download"


def test_a_genuinely_read_only_recording_still_warns():
    assert goal_coverage_warning(manifest_op_entries(PAGES, [])) is not None


# ---- F4/F5: a policy-cancelled download is not a broken step --------------


def _exec(seq):
    """Executor answering list_downloads from a queue of payloads."""
    it = iter(seq)
    last = {"downloads": []}

    def run(command, params=None):
        nonlocal last
        if command == "list_downloads":
            try:
                last = next(it)
            except StopIteration:
                pass
            return {"success": True, "data": last}
        return {"success": True, "data": {}}

    return run


def test_downloads_disabled_by_policy_says_so_instead_of_no_file_arrived():
    unmet = expectation_unmet(
        {"download": True},
        _exec([{"downloads": [], "disabled_by_policy": True}]),
        known_downloads=set(),
    )
    assert "DISABLED" in unmet
    assert "browser_policy.downloads" in unmet
    assert "template setting, not a broken step" in unmet
    assert "no file arrived" not in unmet


def test_a_slow_download_is_waited_for_rather_than_failed(monkeypatch):
    """Recording attributes a download up to 120s after the click; replay used
    to give it one 2.5s retry. A bank showing "generating statement..." passed
    recording and failed every replay."""
    import noui_core.verify.replay as rp

    monkeypatch.setattr(rp.time, "sleep", lambda _s: None)
    run = _exec(
        [
            {"downloads": []},
            {"downloads": []},
            {"downloads": [{"id": "d1", "state": "completed"}]},
        ]
    )
    assert expectation_unmet({"download": True}, run, known_downloads=set()) == ""


def test_the_wait_is_bounded_and_still_fails_when_nothing_arrives(monkeypatch):
    import noui_core.verify.replay as rp

    monkeypatch.setattr(rp.time, "sleep", lambda _s: None)
    ticks = iter([0.0] + [rp._DOWNLOAD_WAIT_S + 1] * 50)
    monkeypatch.setattr(rp.time, "monotonic", lambda: next(ticks))
    unmet = expectation_unmet({"download": True}, _exec([{"downloads": []}]), known_downloads=set())
    assert "no file arrived" in unmet


def test_an_already_known_file_does_not_satisfy_a_later_step(monkeypatch):
    import noui_core.verify.replay as rp

    monkeypatch.setattr(rp.time, "sleep", lambda _s: None)
    ticks = iter([0.0] + [rp._DOWNLOAD_WAIT_S + 1] * 50)
    monkeypatch.setattr(rp.time, "monotonic", lambda: next(ticks))
    unmet = expectation_unmet(
        {"download": True},
        _exec([{"downloads": [{"id": "old", "state": "completed"}]}]),
        known_downloads={"old"},
    )
    assert "no NEW completed" in unmet
