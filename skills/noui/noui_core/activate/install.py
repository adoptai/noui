"""Pillar 3 — Activate: install generated assets into an agent, agent-agnostically.

Skill install is a directory copy into the target agent's skills dir. The
`agents` target writes to the shared .agents/skills/ convention honored by
Codex, OpenCode, and the npx skills ecosystem.
"""

from __future__ import annotations

import shutil
from pathlib import Path

SKILL_AGENTS = ("claude-code", "codex", "cline", "opencode", "agents")


def _skill_install_root(agent: str, project: bool) -> Path:
    """Return the skills directory for a given agent + scope.

    Path.cwd() / Path.home() are resolved at call time so tests (and any
    caller that chdirs) get the current working directory.
    """
    if agent == "claude-code":
        return Path.cwd() / ".claude" / "skills" if project else Path.home() / ".claude" / "skills"
    if agent in ("codex", "agents"):
        return Path.cwd() / ".agents" / "skills" if project else Path.home() / ".agents" / "skills"
    if agent == "cline":
        return Path.cwd() / ".cline" / "skills" if project else Path.home() / ".cline" / "skills"
    if agent == "opencode":
        return (
            Path.cwd() / ".opencode" / "skills"
            if project
            else Path.home() / ".config" / "opencode" / "skills"
        )
    raise ValueError(f"Unknown agent {agent!r}; expected one of {SKILL_AGENTS}")


def install_skill(skill_dir: str | Path, agent: str, *, project: bool = False) -> Path:
    """Copy a generated skill directory into the agent's skills root.

    Returns the destination path. Overwrites an existing install of the same name.
    """
    src = Path(skill_dir)
    if not (src / "SKILL.md").exists():
        raise RuntimeError(f"{src} is not a skill directory (no SKILL.md)")
    root = _skill_install_root(agent, project)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / src.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def uninstall_skill(skill_name: str, agent: str, *, project: bool = False) -> bool:
    """Remove an installed skill. Returns True if something was removed."""
    dest = _skill_install_root(agent, project) / skill_name
    if dest.exists():
        shutil.rmtree(dest)
        return True
    return False
