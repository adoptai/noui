"""The skill KIND travels with the recording, not with a command line.

capture_record took --browser-driven, provisioned with it, and never wrote it
down — so capture_import had to be told again. Forget it there, which is easily
done since nothing in the recording says so, and an app that must be DRIVEN
compiles to replayed API calls: an ICICI capture asked for as a "BROWSER BASED"
skill shipped as two Finacle POSTs.
"""

from noui_core.capture import ledger


def test_the_ledger_remembers_a_browser_driven_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("NOUI_WORKBENCH_DIR", str(tmp_path))
    from noui_core.config import settings

    monkeypatch.setattr(settings, "workbench_dir", str(tmp_path))

    ledger.record("sess-1", declared_mode="login", browser_driven=True)
    assert ledger.declared_browser_driven("sess-1") is True


def test_an_ordinary_recording_is_not_marked_browser_driven(tmp_path, monkeypatch):
    from noui_core.config import settings

    monkeypatch.setattr(settings, "workbench_dir", str(tmp_path))

    ledger.record("sess-2", declared_mode="workflow")
    assert ledger.declared_browser_driven("sess-2") is False


def test_an_unknown_session_is_not_browser_driven(tmp_path, monkeypatch):
    from noui_core.config import settings

    monkeypatch.setattr(settings, "workbench_dir", str(tmp_path))

    assert ledger.declared_browser_driven("never-recorded") is False


def test_the_platform_can_declare_the_kind_by_environment(monkeypatch):
    """The member's words become an environment fact the agent cannot forget.

    Passing --browser-driven survived only if the agent typed it.
    NOUI_BROWSER_DRIVEN lets the platform state the kind once, from the member's
    own request, so the recording is provisioned correctly whether or not anyone
    remembers the flag.
    """
    import os

    monkeypatch.setenv("NOUI_BROWSER_DRIVEN", "1")
    assert os.environ.get("NOUI_BROWSER_DRIVEN", "").strip().lower() in ("1", "true", "yes")

    monkeypatch.setenv("NOUI_BROWSER_DRIVEN", "")
    assert os.environ.get("NOUI_BROWSER_DRIVEN", "").strip().lower() not in ("1", "true", "yes")
