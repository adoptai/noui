#!/usr/bin/env python3
"""Publish a harness skill (SKILL.md + aux files) to an org's skill store.

This is the *packaging* counterpart to ``build_bundle.py``: build the bundle,
then publish the skill so the harness lists/loads it. It writes via the
workflows-side ``upload_skill`` (S3 under
``skills/source=org/org_id=<org>/skill_name=<name>/``), so it must run with the
**adoptai-workflows** package importable:

    cd /path/to/adoptai-workflows
    venv/bin/python /path/to/noui/skills/noui-harness/publish_bundle.py \
        --org-id <ORG_UUID> [--skill-name noui] [--skill-md SKILL.md] \
        [--aux noui-bundle.zip] [--bucket <bucket>] [--no-replace]

Defaults target THIS folder's ``SKILL.md`` + ``noui-bundle.zip`` and skill name
``noui`` — i.e. re-publishing the NoUI harness skill — but every value is a flag,
so it publishes any harness skill to any org.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--org-id", required=True, help="target org UUID")
    p.add_argument("--skill-name", default="noui", help="catalog skill name/slug (default: noui)")
    p.add_argument(
        "--skill-md",
        default=str(_HERE / "SKILL.md"),
        help="path to the SKILL.md to publish (default: this folder's SKILL.md)",
    )
    p.add_argument(
        "--aux",
        action="append",
        default=None,
        help="aux file to ship, as 'arcname=path' or just 'path' (repeatable). "
        "Default: noui-bundle.zip from this folder.",
    )
    p.add_argument("--bucket", default=None, help="skill store bucket (default: deployment default)")
    p.add_argument("--no-replace", action="store_true", help="fail if the skill already exists")
    return p.parse_args()


def _load_aux(specs: list[str] | None) -> list[dict]:
    if specs is None:
        specs = [f"noui-bundle.zip={_HERE / 'noui-bundle.zip'}"]
    aux: list[dict] = []
    for spec in specs:
        arcname, _, path = spec.partition("=")
        if not path:  # bare path → arcname is the file's basename
            path, arcname = arcname, Path(arcname).name
        data = Path(path).read_bytes()
        aux.append({"path": arcname, "content": data})
    return aux


async def _main() -> int:
    args = _parse_args()
    from src.workflows.agent_harness.skills_upload import upload_skill

    skill_md = Path(args.skill_md).read_bytes()
    if not skill_md.lstrip().startswith(b"---"):
        raise SystemExit(
            f"refusing to publish: {args.skill_md} has no YAML frontmatter "
            "(a SKILL.md must start with '---'). Did you point --skill-md at the right file?"
        )
    aux = _load_aux(args.aux)
    print(
        f"publishing skill '{args.skill_name}' to org={args.org_id} "
        f"(SKILL.md {len(skill_md)}B, {len(aux)} aux file(s))"
    )
    keys = await upload_skill(
        org_id=args.org_id,
        skill_name=args.skill_name,
        skill_md=skill_md,
        aux_files=aux,
        bucket_name=args.bucket,
        replace=not args.no_replace,
    )
    print(f"published {len(keys)} object(s):")
    for k in keys:
        print("  ", k)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
