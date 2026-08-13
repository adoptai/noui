"""The compiler's output for a real recording, pinned.

Every unit test in this suite checks a function in isolation. None of them looks
at the artifact a replay actually runs, which is why a compiler change could pass
1028 tests and still silently gut a skill:

  9f4fba3 restored a dropped "Past" click -- and displaced the Annual radio's
  set_checked into a different operation, where it stopped being emitted at all.
  The suite stayed green. It was found by a human watching a replay click the
  wrong tab, hours later.

So this file pins the whole output for one fixed bundle. Any change to the
compiler that alters the operations fails here with a diff. That is the point:
accepting a change becomes a deliberate, visible act -- regenerate the golden,
and the diff sits in the review where someone can see what behaviour moved --
rather than something that happens quietly while the assertions still pass.

Regenerate deliberately, never reflexively:

    python tests/test_compile_is_stable.py --update

and read the diff before committing it.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from noui_core.compile.browser_skill import generate_browser_skill

FIXTURES = Path(__file__).parent / "fixtures"
BUNDLE = FIXTURES / "icici_statement_bundle.json"
GOLDEN = FIXTURES / "icici_statement_operations.json"
LOGIN_URL = "https://retailnetbanking.icici.bank.in/login-page"


def _compile(bundle: dict) -> list[dict]:
    """The operations a replay would actually run, reduced to what matters.

    Drives generate_browser_skill -- the one entry point compile_workflow uses --
    and reads the operations.json it writes. Calling the internals instead
    (derive_browser_pages + derive_terminal_operations) looks equivalent and is
    not: the assembly on top is where set_checked and select_option land, so a
    harness built on the fragments reported them missing from a skill that
    plainly contained them.

    Selectors and values are kept -- they are the thing that breaks -- while
    volatile provenance (seq stamps, jsessionid-bearing urls) is dropped, so the
    golden does not churn on every re-record.
    """
    with tempfile.TemporaryDirectory() as out:
        generate_browser_skill(
            app_slug="icici-fixture",
            app_name="ICICI Fixture",
            workflow_name="statement",
            profile_slug="icici-fixture",
            url_events=bundle.get("url_events") or [],
            click_events=bundle.get("click_events") or [],
            login_url=LOGIN_URL,
            output_dir=out,
            bundle=bundle,
        )
        written = json.loads((Path(out) / "operations.json").read_text())
    ops = written if isinstance(written, list) else written.get("operations", [])

    shaped: list[dict] = []
    for op in ops:
        steps = []
        for step in op.get("segment_steps") or op.get("steps") or []:
            params = step.get("params") or {}
            steps.append(
                {
                    "command": step.get("command"),
                    # The three that decide whether a step does the right thing.
                    "target": params.get("selector") or params.get("text") or params.get("role"),
                    "value": params.get("value") or params.get("name"),
                    "hover_first": params.get("hover_first"),
                }
            )
        shaped.append({"name": op.get("name"), "kind": op.get("kind"), "steps": steps})
    return shaped


def _load_bundle() -> dict:
    return json.loads(BUNDLE.read_text())


def test_the_compiled_operations_have_not_changed():
    actual = _compile(_load_bundle())
    expected = json.loads(GOLDEN.read_text())
    if actual != expected:  # pragma: no cover - only on a real regression
        raise AssertionError(
            "The compiler now produces different operations for the same recording.\n"
            "If that is intended, run `python tests/test_compile_is_stable.py --update`\n"
            "and put the diff in the review. If it is not, something regressed.\n\n"
            f"expected: {json.dumps(expected, indent=1)}\n\n"
            f"actual:   {json.dumps(actual, indent=1)}"
        )


def test_the_download_operation_still_selects_the_period_and_downloads():
    """The specific behaviour that broke, named so a regression says what it cost.

    A golden diff tells you something moved. This says which capability went
    missing -- the Annual radio and the period, without which the skill downloads
    a monthly statement and every check still passes, because a monthly statement
    is still a statement.
    """
    ops = _compile(_load_bundle())
    download = [o for o in ops if o["kind"] == "download"]
    assert download, "no download operation compiled at all"
    commands = [s["command"] for o in download for s in o["steps"]]
    assert "set_checked" in commands, "the statement-period radio is not set"
    assert "select_option" in commands, "the period dropdown is not chosen"
    assert "list_downloads" in commands, "nothing confirms a file arrived"


if __name__ == "__main__":  # pragma: no cover - maintenance entry point
    if "--update" in sys.argv:
        GOLDEN.write_text(json.dumps(_compile(_load_bundle()), indent=1) + "\n")
        print(f"wrote {GOLDEN}")
    else:
        print(__doc__)
