"""An import that keeps an existing App Template must say so, loudly.

From a real build (org 87451b06, 2026-09-15): the member asked for a FRESH
profile. `capture_record --force` honoured that at record time, but the agent
reused the slug, so `capture_import` hit Tabby's 409 and — by a policy written
for "the SECOND import of the SAME recording" — kept a 29-day-old template and
compiled only the workflow half.

That template's browser_policy lacked `block_navigate` and `downloads`, which a
fresh compile of this recording would have set. Both halves of the task then
failed far from here: every `navigate` reloaded the bank and signed the member
out (4 sign-in prompts), and every download was discarded by the browser.
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


def test_without_force_it_names_the_reuse_and_the_flags_at_risk():
    out = _mod()._on_template_exists("icici-credit-card-statement", COMPILED, force=False)
    assert "was NOT registered" in out
    assert "not a fresh one" in out
    # The flags are the actionable part: they are why reuse is not cosmetic.
    assert "browser_policy.downloads=true" in out
    assert "browser_policy.block_navigate=true" in out
    assert "--force" in out


def test_with_force_it_merges_the_flags_onto_the_existing_template(monkeypatch):
    m = _mod()
    calls = {}

    class _Client:
        @staticmethod
        def get_app_template_by_profile_slug(slug, token):
            return {"id": "tpl-1", "browser_policy": {"clipboard": False, "downloads": False}}

        @staticmethod
        def update_app_template(tid, payload, token):
            calls["id"], calls["payload"] = tid, payload
            return {}

    import noui_core

    monkeypatch.setattr(noui_core, "tabby_client", _Client, raising=False)
    monkeypatch.setitem(sys.modules, "noui_core.tabby_client", _Client)
    monkeypatch.setattr(m.register, "resolve_admin_token", lambda: "tok")

    out = m._on_template_exists("icici-credit-card-statement", COMPILED, force=True)
    assert calls["id"] == "tpl-1"
    bp = calls["payload"]["browser_policy"]
    # Additive: turns flags ON, never off, and never drops a field it did not set.
    assert bp["downloads"] is True and bp["block_navigate"] is True
    assert bp["clipboard"] is False, "an unrelated flag must survive the merge"
    assert "merged" in out


def test_force_never_turns_a_flag_off(monkeypatch):
    """A recording that needs nothing must not clear what an operator set."""
    m = _mod()
    calls = {}

    class _Client:
        @staticmethod
        def get_app_template_by_profile_slug(slug, token):
            return {"id": "t", "browser_policy": {"downloads": True, "block_navigate": True}}

        @staticmethod
        def update_app_template(tid, payload, token):
            calls["payload"] = payload

    monkeypatch.setitem(sys.modules, "noui_core.tabby_client", _Client)
    monkeypatch.setattr(m.register, "resolve_admin_token", lambda: "tok")

    out = m._on_template_exists("x", {"application_draft": {"browser_policy": {}}}, force=True)
    assert "nothing to merge" in out
    assert not calls, "no write when the recording turns on nothing"


def test_a_failure_to_merge_is_reported_not_raised(monkeypatch):
    """The import must still complete: the workflow half is real work."""
    m = _mod()
    monkeypatch.setattr(
        m.register, "resolve_admin_token", lambda: (_ for _ in ()).throw(RuntimeError("no token"))
    )
    out = m._on_template_exists("x", COMPILED, force=True)
    assert "could not merge" in out
    assert "was NOT registered" in out, "the reuse notice survives a merge failure"
