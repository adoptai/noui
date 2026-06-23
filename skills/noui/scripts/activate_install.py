#!/usr/bin/env python3
"""Install a generated Skill into an agent (agent-agnostic).

    python scripts/activate_install.py workbench/skills/<app> claude-code
    python scripts/activate_install.py workbench/skills/<app> codex --project

Agents: claude-code | codex | cline | opencode | agents.
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401

from noui_core.activate.install import SKILL_AGENTS, install_skill, uninstall_skill


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("skill_dir", help="generated skill directory (or skill name for --uninstall)")
    p.add_argument("agent", choices=SKILL_AGENTS)
    p.add_argument("--project", action="store_true", help="project-scoped install path")
    p.add_argument("--uninstall", action="store_true", help="remove instead of install")
    args = p.parse_args()

    try:
        if args.uninstall:
            removed = uninstall_skill(args.skill_dir, args.agent, project=args.project)
            print("removed" if removed else "nothing to remove")
            return 0
        dest = install_skill(args.skill_dir, args.agent, project=args.project)
        print(f"installed → {dest}")
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
