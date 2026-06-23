"""Tests for noui_core.activate.install — agent-agnostic skill install."""

from __future__ import annotations

from pathlib import Path

import pytest
from noui_core.activate.install import (
    SKILL_AGENTS,
    _skill_install_root,
    install_skill,
    uninstall_skill,
)


@pytest.mark.parametrize(
    "agent, project, expected",
    [
        ("claude-code", False, Path.home() / ".claude" / "skills"),
        ("claude-code", True, Path.cwd() / ".claude" / "skills"),
        ("codex", False, Path.home() / ".agents" / "skills"),
        ("codex", True, Path.cwd() / ".agents" / "skills"),
        ("cline", False, Path.home() / ".cline" / "skills"),
        ("cline", True, Path.cwd() / ".cline" / "skills"),
        ("opencode", False, Path.home() / ".config" / "opencode" / "skills"),
        ("opencode", True, Path.cwd() / ".opencode" / "skills"),
        ("agents", False, Path.home() / ".agents" / "skills"),
        ("agents", True, Path.cwd() / ".agents" / "skills"),
    ],
)
def test_skill_install_root(agent: str, project: bool, expected: Path) -> None:
    assert _skill_install_root(agent, project) == expected


def test_codex_and_agents_resolve_to_same_path() -> None:
    for project in (False, True):
        assert _skill_install_root("codex", project) == _skill_install_root("agents", project)


def test_unknown_agent_raises() -> None:
    with pytest.raises(ValueError, match="Unknown agent"):
        _skill_install_root("windsurf", False)


def test_skill_agents_tuple_matches_resolver() -> None:
    for agent in SKILL_AGENTS:
        _skill_install_root(agent, False)
        _skill_install_root(agent, True)


def test_install_and_uninstall_skill(tmp_path, monkeypatch) -> None:
    # A minimal generated skill.
    src = tmp_path / "demo-skill"
    src.mkdir()
    (src / "SKILL.md").write_text("# demo\n")
    (src / "operations").mkdir()
    (src / "operations" / "op.py").write_text("print('hi')\n")

    # Install project-scoped into a temp CWD.
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    dest = install_skill(src, "agents", project=True)
    assert dest == workdir / ".agents" / "skills" / "demo-skill"
    assert (dest / "SKILL.md").exists()
    assert (dest / "operations" / "op.py").exists()

    # Re-install overwrites cleanly.
    install_skill(src, "agents", project=True)
    assert (dest / "SKILL.md").exists()

    assert uninstall_skill("demo-skill", "agents", project=True) is True
    assert not dest.exists()
    assert uninstall_skill("demo-skill", "agents", project=True) is False


def test_install_rejects_non_skill_dir(tmp_path) -> None:
    bad = tmp_path / "notaskill"
    bad.mkdir()
    with pytest.raises(RuntimeError, match="not a skill directory"):
        install_skill(bad, "claude-code")
