"""An import that keeps an existing App Template must say so, in detail.

From a real build (org 87451b06, 2026-09-15): the member asked for a FRESH
profile. `capture_record --force` honoured that at record time, but the agent
reused the slug, so `capture_import` hit Tabby's 409 and — by a policy written
for "the SECOND import of the SAME recording" — kept a 29-day-old template and
compiled only the workflow half, saying so in one line of stderr the agent read
past.

That template's browser_policy lacked `block_navigate` and `downloads`, which a
fresh compile of this recording sets. Both halves of the task then failed far
from here: every `navigate` reloaded the bank and signed the member out, and
every download was discarded by the browser.

The import REPORTS all of this and changes nothing. An App Template is
tenant-wide and its edits propagate to apps already provisioned from it, so
changing one is a decision about other people's live profiles — the member's,
made in the Auth Manager, not an import's side effect.
"""

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "skills/noui/scripts"


def _mod():
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("ci", _SCRIPTS / "capture_import.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


COMPILED = {"application_draft": {"browser_policy": {"downloads": True, "block_navigate": True}}}


def _with_existing(m, monkeypatch, policy):
    monkeypatch.setattr(m, "_existing_policy", lambda _slug: policy)


def test_it_names_the_reuse_and_that_this_login_was_not_registered(monkeypatch):
    m = _mod()
    _with_existing(m, monkeypatch, {})
    out = m._on_template_exists("icici-credit-card-statement", COMPILED)
    assert "was NOT registered" in out
    assert "not a fresh one" in out


def test_it_reports_exactly_which_flags_the_existing_template_lacks(monkeypatch):
    m = _mod()
    _with_existing(m, monkeypatch, {"downloads": True})  # block_navigate absent
    out = m._on_template_exists("app", COMPILED)
    assert "MISSING" in out
    assert "browser_policy.block_navigate=true" in out
    assert "browser_policy.downloads" not in out.split("MISSING")[1].split(".", 1)[0]
    # And what it costs, so the member can judge urgency.
    assert "sign the member out" in out and "discarded" in out


def test_it_writes_nothing_and_points_at_the_supported_path(monkeypatch):
    """An App Template is tenant-wide and propagates to provisioned apps, so this
    is the member's decision and belongs in the UI, not in a script."""
    m = _mod()
    _with_existing(m, monkeypatch, {})
    out = m._on_template_exists("app", COMPILED)
    assert "NOT changed automatically" in out
    assert "Auth Manager" in out
    assert "the member's" in out
    assert "Do not attempt it with a script" in out
    # No writer is even reachable from this module any more.
    assert not hasattr(m, "update_app_template")
    assert "update_app_template" not in Path(_SCRIPTS / "capture_import.py").read_text()


def test_it_stays_quiet_when_the_template_already_has_the_flags(monkeypatch):
    m = _mod()
    _with_existing(m, monkeypatch, {"downloads": True, "block_navigate": True})
    out = m._on_template_exists("app", COMPILED)
    assert "already has" in out
    assert "MISSING" not in out


def test_an_unreadable_template_is_reported_not_guessed_at(monkeypatch):
    m = _mod()
    _with_existing(m, monkeypatch, None)
    out = m._on_template_exists("app", COMPILED)
    assert "Could not read the existing template" in out
    assert "MISSING" not in out, "never assert a flag is missing on a failed read"


def test_a_recording_that_needs_no_flags_adds_no_noise(monkeypatch):
    m = _mod()
    _with_existing(m, monkeypatch, {})
    out = m._on_template_exists("app", {"application_draft": {"browser_policy": {}}})
    assert "was NOT registered" in out
    assert "Auth Manager" not in out


def test_the_diagnosis_read_never_fails_the_import(monkeypatch):
    """A real workflow asset was produced; a diagnostic lookup must not sink it."""
    m = _mod()
    monkeypatch.setattr(
        m.register, "resolve_admin_token", lambda: (_ for _ in ()).throw(RuntimeError("no token"))
    )
    assert m._existing_policy("app") is None
    assert m._on_template_exists("app", COMPILED)
