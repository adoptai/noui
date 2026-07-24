"""capture_record defaults to ONE session for the login and the workflow.

Recording the halves separately means two viewer links and two sign-ins, and an
agent holding two links in one conversation tends to surface both at once — which
is what a live harness run did, and it read as more confusing than the problem it
was meant to solve. So `combined` is the default, and `workflow` only when the
auth already exists (--profile/--from).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from noui_core.capture import ledger
from noui_core.config import settings

_NOUI_ROOT = Path(__file__).resolve().parent.parent
for _p in (_NOUI_ROOT / "skills" / "noui", _NOUI_ROOT / "skills" / "noui" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import capture_record as cr  # noqa: E402

_PROVISIONED = {
    "session_id": "sess-new",
    "login_url": "https://tabby.test/s/abc123",
    "warm": True,
}


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    """Run capture_record.main() with argv; every Tabby call stubbed.

    ``resolve_agent_token`` is stubbed too, not just provisioning: the
    duplicate-template pre-check resolves a bearer *before* calling
    ``find_similar_templates``, and in agent_token mode that mints a real token
    over the network. Left unstubbed, these tests would hit Tabby whenever the
    developer happens to have credentials in their environment — and silently
    take the "no match" branch (RuntimeError → ``matches = []``) wherever they
    don't, e.g. CI.
    """
    monkeypatch.setattr(settings, "workbench_dir", str(tmp_path))
    monkeypatch.setattr(cr.recording, "resolve_agent_token", lambda: "test-token")

    def _run(*argv: str):
        monkeypatch.setattr(sys, "argv", ["capture_record.py", *argv])
        with patch.object(
            cr.recording, "provision_live_link", return_value=dict(_PROVISIONED)
        ) as prov:
            rc = cr.main()
        out = capsys.readouterr()
        return rc, out.out + out.err, prov

    return _run


class TestDefaultMode:
    def test_no_flags_defaults_to_combined(self, run):
        rc, text, prov = run("--url", "https://app.test/login")
        assert rc == 0
        assert prov.call_args.args[0] == "login"  # combined provisions as a login session
        assert "→ 'combined': one session, one link" in text
        assert ledger.declared_mode("sess-new") == "combined"

    @pytest.mark.parametrize("auth_flag", [("--profile", "acme"), ("--from", "sess-old")])
    def test_existing_auth_defaults_to_workflow(self, run, auth_flag):
        rc, text, prov = run("--url", "https://app.test/app", *auth_flag)
        assert rc == 0
        assert prov.call_args.args[0] == "workflow"
        assert "→ 'workflow': auth comes from --profile/--from" in text
        assert ledger.declared_mode("sess-new") == "workflow"

    @pytest.mark.parametrize("mode", ["login", "workflow", "combined"])
    def test_explicit_mode_is_honoured_and_not_announced(self, run, mode):
        rc, text, _prov = run("--mode", mode, "--url", "https://app.test/x", "--force")
        assert rc == 0
        assert "--mode not given" not in text
        assert ledger.declared_mode("sess-new") == mode


class TestOneLinkAtATime:
    def test_combined_instructions_cover_sign_in_then_workflow(self, run):
        _rc, text, _prov = run("--url", "https://app.test/login")
        assert "SIGN IN" in text
        assert "DRIVE THE WORKFLOW" in text
        assert "ONLY link to give the human right now" in text
        # The import command needs no --combined: the ledger already knows.
        assert "capture_import.py sess-new --as skill" in text
        assert "--combined" not in text

    def test_workflow_run_also_warns_against_a_second_link(self, run):
        _rc, text, _prov = run("--url", "https://app.test/app", "--profile", "acme")
        assert "ONLY link to give the human right now" in text


class TestTemplateReuseCheck:
    """A template that already covers this app makes the login half wasted work."""

    def _match(self):
        return [{"name": "Acme", "profile_name_pattern": "acme", "id": "tpl-1"}]

    @pytest.mark.parametrize("argv", [(), ("--mode", "combined")])
    def test_combined_checks_for_an_existing_template(self, run, argv):
        with patch.object(cr, "find_similar_templates", return_value=self._match()):
            rc, text, prov = run("--url", "https://app.test/login", "--name", "acme", *argv)
        assert rc == 0
        prov.assert_not_called()  # nothing recorded
        assert "Skipping the login capture" in text
        assert "--profile acme" in text

    def test_login_mode_still_checks(self, run):
        with patch.object(cr, "find_similar_templates", return_value=self._match()):
            rc, text, prov = run(
                "--mode", "login", "--url", "https://app.test/login", "--name", "acme"
            )
        assert rc == 0
        prov.assert_not_called()
        assert "Skipping the login capture" in text

    def test_force_records_anyway(self, run):
        with patch.object(cr, "find_similar_templates", return_value=self._match()):
            rc, _text, prov = run("--url", "https://app.test/login", "--name", "acme", "--force")
        assert rc == 0
        prov.assert_called_once()

    def test_workflow_mode_does_not_check(self, run):
        """There is no login to skip when auth comes from a profile."""
        with patch.object(cr, "find_similar_templates") as find:
            rc, _text, prov = run(
                "--url", "https://app.test/app", "--profile", "acme", "--name", "acme"
            )
        assert rc == 0
        find.assert_not_called()
        prov.assert_called_once()
