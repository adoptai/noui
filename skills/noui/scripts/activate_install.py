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
from noui_core.verify.gate import NotApprovedError, check_installable


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("skill_dir", help="generated skill directory (or skill name for --uninstall)")
    p.add_argument("agent", choices=SKILL_AGENTS)
    p.add_argument("--project", action="store_true", help="project-scoped install path")
    p.add_argument("--uninstall", action="store_true", help="remove instead of install")
    p.add_argument(
        "--skip-replay-gate",
        action="store_true",
        help="install a browser skill without an approved replay. For recovering a "
        "known-good skill, not for shipping a new one: a browser skill that has "
        "never been run against the live app is exactly the kind that looks "
        "correct in review and fails in production.",
    )
    args = p.parse_args()

    try:
        if args.uninstall:
            removed = uninstall_skill(args.skill_dir, args.agent, project=args.project)
            print("removed" if removed else "nothing to remove")
            return 0
        # A browser skill installs only after a human approved its replay. The
        # check is here rather than in the caller so no path -- an agent in a
        # hurry, a script, a retry -- can install one that was never run.
        if not args.skip_replay_gate:
            check_installable(args.skill_dir)
        dest = install_skill(args.skill_dir, args.agent, project=args.project)
        print(f"installed → {dest}")
        return 0
    except NotApprovedError as exc:
        print(f"Refusing to install: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, ValueError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
