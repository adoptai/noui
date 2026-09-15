"""verify_replay must not give the caller a reason to truncate its own output.

It used to print the entire report -- every operation, every step -- as indented
JSON while ALSO writing it to replay_report.json. For a two-operation skill that
is already ~8KB of duplicate output, so an agent caller reading it as context
did the rational thing and piped it away:

    verify_replay.py ... 2>&1 | tail -5
    verify_replay.py ... 2>&1 | python3 -c ...

(both recovered verbatim from a production trace, conv 7bec3054). The
per-operation results it then had to reason about were exactly what the pipe
discarded.
"""
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "skills/noui/scripts/verify_replay.py"


def _summarize():
    """Load just the renderer, without the script's runtime imports."""
    src = _SRC.read_text()
    m = re.search(r"def _summarize\(report: dict, report_path: Path\) -> str:.*?(?=\ndef )", src, re.S)
    assert m, "_summarize not found"
    ns: dict = {"Path": Path}
    exec(m.group(0), ns)
    return ns["_summarize"]


# Shaped like build_report()'s real output: goals is the verdict the card
# headlines, and operations carry blocked/needs_approval counts.
_REPORT = {
    "all_goals_reached": False,
    "goals": [{"name": "download_last_statement", "reached": False}],
    "operations": [
        {"name": "read_credit_card", "goal_reached": False, "blocked_count": 0,
         "needs_approval_count": 0,
         "steps": [{"status": "ok"}, {"status": "error"}, {"status": "ok"}]},
        {"name": "download_last_statement", "goal_reached": True, "blocked_count": 0,
         "needs_approval_count": 0,
         "steps": [{"status": "ok"}, {"status": "ok"}]},
    ],
}


def test_summary_carries_the_per_operation_outcome():
    """What the pipe destroyed must survive WITHOUT the pipe: which operations
    reached their goal, which did not, and where the failure was."""
    out = _summarize()(_REPORT, Path("workbench/skills/x/replay_report.json"))
    assert "INCOMPLETE" in out
    assert "0/1 goal(s)" in out
    assert "[FAIL] read_credit_card" in out
    assert "[PASS] download_last_statement" in out
    assert "first error at step 1" in out
    assert "workbench/skills/x/replay_report.json" in out, "detail must remain findable"


def test_summary_is_short_enough_not_to_invite_truncation():
    """The whole point. If this grows back toward the full report, the caller
    starts piping again and the fix is undone."""
    out = _summarize()(_REPORT, Path("workbench/skills/x/replay_report.json"))
    assert len(out.splitlines()) <= 10, out
    assert len(out) < 700, f"{len(out)} chars is drifting back toward a dump"
    assert '"steps"' not in out, "step-level JSON belongs in the file, not stdout"


def test_a_clean_replay_says_so_plainly():
    ok = {"all_goals_reached": True,
          "goals": [{"name": "download_last_statement", "reached": True}],
          "operations": [{"name": "download_last_statement", "goal_reached": True,
                          "blocked_count": 0, "needs_approval_count": 0,
                          "steps": [{"status": "ok"}]}]}
    out = _summarize()(ok, Path("p.json"))
    assert out.startswith("Replay OK")
    assert "1/1 goal(s)" in out


def test_a_partial_run_is_never_presented_as_a_full_one():
    partial = dict(_REPORT, partial=["download_last_statement"])
    out = _summarize()(partial, Path("p.json"))
    assert "PARTIAL" in out and "not a full replay" in out


def test_the_summary_does_not_invite_self_approval():
    """The caller reads this and decides what to tell the member. It must not
    read as a verdict the agent may act on alone."""
    out = _summarize()(_REPORT, Path("p.json"))
    low = out.lower()
    assert "do not approve it yourself" in low
    assert "card" in low


def test_full_json_is_still_reachable_and_still_the_fallback(tmp_path, monkeypatch, capsys):
    """--json restores the dump, and a failed file write must still fall back to
    printing it -- otherwise losing the file would silently lose the report.

    Exercised through the real tail of main() rather than asserted against the
    source text: a source assertion still passes if the print branch is deleted,
    and breaks on a harmless reordering of the condition.
    """
    src = _SRC.read_text()
    assert '"--json"' in src, "the flag must exist"

    tail = re.search(
        r"    out = skill_dir / REPORT_FILE.*?print\(_summarize\(report, out\)\)", src, re.S
    )
    assert tail, "report-writing tail not found"

    ns = {
        "json": __import__("json"),
        "sys": __import__("sys"),
        "REPORT_FILE": "replay_report.json",
        "_summarize": _summarize(),
    }

    def _run(*, writable, want_json):
        ns["skill_dir"] = tmp_path
        ns["report"] = {"goals": [{"name": "g", "reached": True}], "operations": []}
        ns["args"] = type("A", (), {"json": want_json, "only": None})()
        code = tail.group(0)
        if not writable:
            # Simulate the OSError branch the fallback exists for.
            code = code.replace(
                "out.write_text(", "(_ for _ in ()).throw(OSError('boom')) or out.write_text("
            )
        exec("if True:\n" + "\n".join(" " + line for line in code.splitlines()), ns)
        return capsys.readouterr().out

    assert '"goals"' in _run(writable=False, want_json=False), (
        "a failed write must still print the full report"
    )
    assert '"goals"' in _run(writable=True, want_json=True), "--json must print the full report"
    assert "Replay OK" in _run(writable=True, want_json=False), "default is the summary"


def test_skipped_and_recovered_are_not_failures():
    """A clean replay printed "first failure at step 1" because the filter
    counted `skipped` (benign) and the declared-but-unused `recovered`."""
    clean = {
        "all_goals_reached": True,
        "goals": [{"name": "download_statement", "reached": True}],
        "operations": [{
            "name": "download_statement", "goal_reached": True,
            "blocked_count": 0, "needs_approval_count": 0,
            "steps": [{"status": "ok"}, {"status": "skipped"}, {"status": "recovered"}],
        }],
    }
    out = _summarize()(clean, Path("p.json"))
    assert out.startswith("Replay OK")
    assert "error at step" not in out, out


def test_a_report_level_status_is_the_whole_answer():
    """A `not_replayable` report (segment mixture) summarised to
    "0/0 operation(s)" with exit code 0 -- the actionable line existed only in
    the file, so the caller was told nothing and had no reason to look."""
    out = _summarize()(
        {"status": "not_replayable",
         "detail": "Recompile the skill; do not replay this.",
         "operations": [], "goals": []},
        Path("p.json"),
    )
    assert "NOT_REPLAYABLE" in out
    assert "Recompile the skill" in out


def test_the_headline_matches_what_the_card_headlines():
    """build_report's own comment: `goals` differs from `all_goals_reached`,
    which also trips on an intermediate submit that blocked AFTER the download
    arrived. Leading with the latter printed INCOMPLETE over a card saying the
    goal was reached."""
    mixed = {
        "all_goals_reached": False,  # an intermediate op blocked
        "goals": [{"name": "download_statement", "reached": True}],
        "operations": [
            {"name": "submit_filter", "goal_reached": False, "blocked_count": 1,
             "needs_approval_count": 0, "steps": [{"status": "blocked"}]},
            {"name": "download_statement", "goal_reached": True, "blocked_count": 0,
             "needs_approval_count": 0, "steps": [{"status": "ok"}]},
        ],
    }
    out = _summarize()(mixed, Path("p.json"))
    assert out.startswith("Replay OK"), f"must agree with the card: {out}"
    assert "1 blocked" in out, "the blocked step must still be visible"


def test_an_approval_hold_is_not_rendered_as_a_breakage():
    """needs_approval has a remedy (--approve-step). Rendering it identically to
    a broken locator hid the fix, and SKILL.md requires surfacing it."""
    held = {
        "all_goals_reached": False,
        "needs_approval": True,
        "goals": [{"name": "download_statement", "reached": False}],
        "operations": [{
            "name": "download_statement", "goal_reached": False,
            "blocked_count": 0, "needs_approval_count": 1,
            "steps": [{"status": "needs_approval"}],
        }],
    }
    out = _summarize()(held, Path("p.json"))
    assert "awaiting approval" in out
    assert "--approve-step" in out, "the remedy must be named"
    assert "error at step" not in out, "a hold is not an error"
