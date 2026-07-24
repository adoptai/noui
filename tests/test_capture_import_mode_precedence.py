"""capture_import decides the capture's mode by declaration, not by Tabby's stamp.

Precedence: --mode flag > provision ledger > content inference. Tabby's
``bundle.recording_mode`` is not a source at all.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from noui_core.capture import ledger
from noui_core.config import settings

_NOUI_ROOT = Path(__file__).resolve().parent.parent
for _p in (_NOUI_ROOT / "skills" / "noui", _NOUI_ROOT / "skills" / "noui" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import capture_import as ci  # noqa: E402


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workbench_dir", str(tmp_path))
    return tmp_path


def _args(session_id: str = "sess-1", *, mode: str = "auto", combined: bool = False):
    return SimpleNamespace(session_id=session_id, mode=mode, combined=combined)


class TestPrecedence:
    def test_explicit_mode_wins_over_everything(self, workbench):
        ledger.record("sess-1", declared_mode="login")
        assert ci._resolve_mode(_args(mode="workflow"), "combined") == ("workflow", "--mode flag")

    def test_combined_flag_is_an_alias(self, workbench):
        ledger.record("sess-1", declared_mode="workflow")
        assert ci._resolve_mode(_args(combined=True), "login") == ("combined", "--combined flag")

    def test_ledger_beats_inference(self, workbench):
        """The production case: content-ambiguous capture, ledger says workflow."""
        ledger.record("sess-1", declared_mode="workflow", url="https://x.test/listings")
        assert ci._resolve_mode(_args(), "login") == ("workflow", "provision ledger")

    def test_inference_is_the_fallback(self, workbench):
        assert ci._resolve_mode(_args("unknown-session"), "workflow") == (
            "workflow",
            "bundle content",
        )

    def test_corrupt_ledger_entry_falls_back_to_inference(self, workbench):
        ledger.path_for("sess-1").parent.mkdir(parents=True, exist_ok=True)
        ledger.path_for("sess-1").write_text("{not json")
        assert ci._resolve_mode(_args(), "login") == ("login", "bundle content")

    def test_unknown_declared_mode_falls_back_to_inference(self, workbench):
        ledger.path_for("sess-1").parent.mkdir(parents=True, exist_ok=True)
        ledger.path_for("sess-1").write_text('{"declared_mode": "sideways"}')
        assert ci._resolve_mode(_args(), "workflow") == ("workflow", "bundle content")


class TestTabbyStampIsNeverASource:
    @pytest.mark.parametrize("stamped", ["login", "workflow", None])
    def test_stamp_does_not_influence_resolution(self, workbench, stamped):
        ledger.record("sess-1", declared_mode="workflow")
        mode, source = ci._resolve_mode(_args(), "workflow")
        assert (mode, source) == ("workflow", "provision ledger")


class TestLedger:
    def test_roundtrip(self, workbench):
        path = ledger.record(
            "sess-9",
            declared_mode="combined",
            url="https://x.test/login",
            from_session="sess-8",
            residential=True,
        )
        assert path.exists()
        entry = ledger.lookup("sess-9")
        assert entry["declared_mode"] == "combined"
        assert entry["from_session"] == "sess-8"
        assert entry["residential"] is True
        assert entry["created_at"].startswith("20")
        assert ledger.declared_mode("sess-9") == "combined"

    def test_missing_entry_is_empty_not_an_error(self, workbench):
        assert ledger.lookup("nope") is None
        assert ledger.declared_mode("nope") == ""
        assert ledger.declared_mode("") == ""

    def test_rejects_a_mode_it_cannot_honour(self, workbench):
        with pytest.raises(ValueError, match="declared_mode"):
            ledger.record("sess-x", declared_mode="sideways")


class TestReporting:
    def test_disagreement_with_the_stamp_is_surfaced(self, workbench, capsys):
        ci._report_mode("workflow", "provision ledger", "workflow", {"recording_mode": "login"})
        err = capsys.readouterr().err
        assert "Treating this capture as 'workflow' (source: provision ledger)" in err
        assert "recording_mode='login' — ignored" in err
        assert "warm-pool" in err

    def test_disagreement_with_inference_is_surfaced_with_a_hint(self, workbench, capsys):
        ci._report_mode("workflow", "provision ledger", "combined", {"recording_mode": "login"})
        err = capsys.readouterr().err
        assert "Content looks like 'combined'" in err
        assert "--mode combined" in err

    def test_quiet_when_everything_agrees(self, workbench, capsys):
        ci._report_mode("workflow", "--mode flag", "workflow", {"recording_mode": "workflow"})
        err = capsys.readouterr().err
        assert "Treating this capture as 'workflow'" in err
        assert "ignored" not in err
        assert "Content looks like" not in err
