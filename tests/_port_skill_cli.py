"""Unit tests for the skill CLI path-resolution helper.

Covers every (agent, scope) combination so a future contributor renaming a
path or adding a new agent fails fast instead of silently installing into
the wrong directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_NOUI_ROOT = Path(__file__).resolve().parent.parent
if str(_NOUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOUI_ROOT))

from cli.main import SKILL_AGENTS, _skill_install_root


@pytest.mark.parametrize(
    "agent, project, expected",
    [
        # Claude Code — Anthropic's own paths
        ("claude-code", False, Path.home() / ".claude" / "skills"),
        ("claude-code", True, Path.cwd() / ".claude" / "skills"),
        # Codex — per Codex skills docs, .agents/skills/ is canonical
        ("codex", False, Path.home() / ".agents" / "skills"),
        ("codex", True, Path.cwd() / ".agents" / "skills"),
        # Cline — per Cline skills docs, .cline/skills/
        ("cline", False, Path.home() / ".cline" / "skills"),
        ("cline", True, Path.cwd() / ".cline" / "skills"),
        # OpenCode — global at ~/.config/opencode/skills per its docs
        ("opencode", False, Path.home() / ".config" / "opencode" / "skills"),
        ("opencode", True, Path.cwd() / ".opencode" / "skills"),
        # `agents` meta-target — same on-disk path as codex
        ("agents", False, Path.home() / ".agents" / "skills"),
        ("agents", True, Path.cwd() / ".agents" / "skills"),
    ],
)
def test_skill_install_root(agent: str, project: bool, expected: Path) -> None:
    assert _skill_install_root(agent, project) == expected


def test_codex_and_agents_resolve_to_same_path() -> None:
    # The `agents` meta-target is deliberately equivalent to `codex`.
    # If this ever stops being true, update the docs + argparse help simultaneously.
    for project in (False, True):
        assert _skill_install_root("codex", project) == _skill_install_root("agents", project)


def test_unknown_agent_raises() -> None:
    with pytest.raises(ValueError, match="Unknown agent"):
        _skill_install_root("windsurf", False)


def test_skill_agents_tuple_matches_resolver() -> None:
    # Every choice exposed by argparse must have a resolvable path; no stragglers.
    for agent in SKILL_AGENTS:
        _skill_install_root(agent, False)
        _skill_install_root(agent, True)
