"""The kind is decided before a single click is recorded.

It used to be an optional flag, so a request opening with the words "BROWSER
BASED skill" still recorded with the kind unset, auto-detection chose replay,
and the first compile was wrong. Everything after — patching the manifest,
recompiling, re-recording — was compensation for a decision nobody was asked to
make while it was still cheap.
"""

import sys
from unittest.mock import patch

import capture_record as cr
from noui_core.config import settings


def _run(monkeypatch, capsys, tmp_path, *argv):
    monkeypatch.setattr(settings, "workbench_dir", str(tmp_path))
    monkeypatch.setattr(cr.recording, "resolve_agent_token", lambda: "t")
    monkeypatch.setattr(sys, "argv", ["capture_record.py", *argv])
    with patch.object(
        cr.recording,
        "provision_live_link",
        return_value={"session_id": "s1", "login_url": "https://x/s/1", "warm": True},
    ) as prov:
        rc = cr.main()
    return rc, capsys.readouterr(), prov


def test_recording_without_a_kind_refuses(monkeypatch, capsys, tmp_path):
    rc, out, prov = _run(monkeypatch, capsys, tmp_path, "--url", "https://bank.test/login")
    assert rc == 1
    assert "--kind is required" in out.err
    assert "browser | api | auto" in out.err
    # and it must not have spent a session on an undecided recording
    prov.assert_not_called()


def test_the_refusal_says_where_the_answer_usually_is(monkeypatch, capsys, tmp_path):
    _, out, _ = _run(monkeypatch, capsys, tmp_path, "--url", "https://bank.test/login")
    assert "browser based skill" in out.err
    assert "cannot be relabelled afterwards" in out.err


def test_kind_browser_provisions_browser_driven(monkeypatch, capsys, tmp_path):
    rc, _, prov = _run(
        monkeypatch, capsys, tmp_path, "--kind", "browser", "--url", "https://bank.test/login"
    )
    assert rc == 0
    assert prov.call_args.kwargs["browser_driven"] is True


def test_kind_auto_is_a_choice_not_a_default(monkeypatch, capsys, tmp_path):
    rc, _, prov = _run(
        monkeypatch, capsys, tmp_path, "--kind", "auto", "--url", "https://bank.test/login"
    )
    assert rc == 0
    # combined still asks for evidence; the KIND is what auto leaves open.
    assert prov.called


def test_the_old_flag_still_satisfies_the_requirement(monkeypatch, capsys, tmp_path):
    rc, _, prov = _run(
        monkeypatch, capsys, tmp_path, "--browser-driven", "--url", "https://bank.test/login"
    )
    assert rc == 0
    assert prov.call_args.kwargs["browser_driven"] is True


def test_replay_is_not_a_kind(monkeypatch, capsys, tmp_path):
    """One word, one meaning. 'replay' is the verification of a draft before
    install; it was never a skill kind, and accepting it as an alias would have
    preserved exactly the ambiguity the rename removed."""
    import pytest

    with pytest.raises(SystemExit):
        _run(monkeypatch, capsys, tmp_path, "--kind", "replay", "--url", "https://bank.test/x")
    assert "invalid choice: 'replay'" in capsys.readouterr().err


def test_api_is_accepted_as_a_kind(monkeypatch, capsys, tmp_path):
    """A combined capture always asks Tabby for locator evidence, whatever the
    kind — evidence is cheap and unknowable in advance. The KIND decides which
    COMPILER runs later, which is a different question from what is captured."""
    rc, _, prov = _run(
        monkeypatch, capsys, tmp_path, "--kind", "api", "--url", "https://bank.test/x"
    )
    assert rc == 0
    assert prov.called
