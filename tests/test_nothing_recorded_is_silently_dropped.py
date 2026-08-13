"""Every control the human touched reaches the skill, or is named as excluded.

This is the invariant the golden cannot express. A golden pins WHAT the compiler
produces; this pins that it produces something for everything it was given.

Both of the worst bugs in this pipeline were silent discards, and neither showed
up as a failure anywhere:

  * The nav cap kept the first click and the last two, so a recorded "Past" tab
    switch vanished. The replay looked for a link that only exists under that
    tab and reported "nothing on the page matches this control" -- true, and
    completely misleading.
  * The Annual radio fell between two filters -- after its own page's terminal,
    on a different page from the terminal it configures -- and belonged to no
    operation. The download ran on whatever was already selected and every check
    passed, because a monthly statement is still a statement.

In both cases the bundle held the event, the compiled skill did not, and nothing
in between said a step had been dropped. A human found each by watching a replay.

An exclusion is fine -- plenty of recorded clicks are noise -- but it has to be
DECLARED here, so dropping something is a decision someone made rather than a
side effect of a slice.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from noui_core.compile.browser_skill import generate_browser_skill

FIXTURES = Path(__file__).parent / "fixtures"
BUNDLE = FIXTURES / "icici_statement_bundle.json"
LOGIN_URL = "https://retailnetbanking.icici.bank.in/login-page"

#: Recorded interactions that legitimately produce no step, by what they are --
#: never by seq, which shifts on every re-record.
DECLARED_EXCLUSIONS = {
    # Chat/support widget on ICICI's statement page. Real events, no bearing on
    # the journey.
    "#FieldDropdown",
    "#InitiateChat",
    "#RefreshChat",
    "#SendChat",
    "#DisconnectChat",
    # The QR/credentials tab on the LOGIN page. A workflow skill starts from an
    # authenticated session, so nothing before sign-in belongs in its steps.
    "button.tab-btn",
}


def _is_interactive(ev: dict) -> bool:
    """An event that CHANGES something, so losing it changes the outcome.

    Hovers are excluded: a hover reveals, and the fold merges it into the click
    it opened, so it legitimately may not appear as a step of its own.
    """
    kind = (ev.get("event_type") or "click").lower()
    # A submit IS the terminal -- it becomes the operation itself, not a step
    # inside one -- so it never appears in the step list by design.
    if kind == "submit":
        return False
    if kind == "change":
        return True
    if kind != "click":
        return False
    # A click on a page the journey never reaches is not this test's business.
    return bool(ev.get("selector") or ev.get("text_content") or ev.get("candidates"))


def _norm(text: str) -> str:
    """Compare selectors without CSS escaping.

    The compiler escapes a dot in an id -- `#CustomViewEStatementsFG\\.PERIOD` --
    while the recorder stores it raw, so a substring match reports a control as
    dropped when it is right there. A test that cries wolf is worse than no test,
    so normalise both sides before comparing.
    """
    return text.replace("\\", "")


def _step_haystack(ops: list[dict]) -> str:
    """Every selector, text and value the compiled operations mention."""
    parts: list[str] = []
    for op in ops:
        for step in op.get("segment_steps") or op.get("steps") or []:
            parts.append(json.dumps(step.get("params") or {}))
    return _norm("\n".join(parts))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN DEFECT, not a flaky test: the compiler drops the GO button "
        "(#DUMMY1) that submits ICICI's statement form. Earlier compiles of the "
        "same journey emitted `click_element #DUMMY1`; the current one does not, "
        "so a step the human performed reaches no operation. Excluding it would "
        "be editing the test to fit the code -- the thing this file exists to "
        "prevent. When the compiler stops dropping it this test XPASSes and "
        "strict=True fails the build, which is the signal to remove this marker."
    ),
)
def test_every_state_changing_interaction_reaches_some_operation():
    bundle = json.loads(BUNDLE.read_text())
    clicks = bundle.get("click_events") or []
    # The real entry point, for the reason spelled out in test_compile_is_stable:
    # the fragments below it omit the assembly where several step kinds land.
    with tempfile.TemporaryDirectory() as out:
        generate_browser_skill(
            app_slug="icici-fixture",
            app_name="ICICI Fixture",
            workflow_name="statement",
            profile_slug="icici-fixture",
            url_events=bundle.get("url_events") or [],
            click_events=clicks,
            login_url=LOGIN_URL,
            output_dir=out,
            bundle=bundle,
        )
        written = json.loads((Path(out) / "operations.json").read_text())
    ops = written if isinstance(written, list) else written.get("operations", [])
    haystack = _step_haystack(ops)

    missing: list[str] = []
    for ev in clicks:
        if not _is_interactive(ev):
            continue
        selector = str(ev.get("selector") or "")
        text = str(ev.get("text_content") or "").strip()
        if any(x and x in selector for x in DECLARED_EXCLUSIONS):
            continue
        # Represented if ANY of its recorded handles appears in some step: the
        # compiler is free to choose a different locator, just not to drop the
        # control.
        handles = [h for h in (selector, text) if h]
        for cand in ev.get("candidates") or []:
            if not isinstance(cand, dict) or not cand.get("value"):
                continue
            value = str(cand["value"])
            handles.append(value)
            # A radio is often addressed as set_checked{role, name}, so the
            # recorded `radio|Annual` never appears verbatim -- the compiled step
            # carries only the name half.
            if str(cand.get("kind")) == "role_name" and "|" in value:
                handles.append(value.split("|", 1)[1])
        if not any(h and _norm(h) in haystack for h in handles):
            missing.append(f"seq {ev.get('seq')} {ev.get('event_type')} {selector or text!r}")

    assert not missing, (
        "Recorded interactions that reach no compiled step:\n  "
        + "\n  ".join(missing)
        + "\n\nEither the compiler is dropping work the human did, or these are "
        "noise and belong in DECLARED_EXCLUSIONS -- with a reason."
    )
