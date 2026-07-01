#!/usr/bin/env python3
"""Publish a harness skill (SKILL.md + aux files) to an org's skill store.

This is the *packaging* counterpart to ``build_bundle.py``: build the bundle,
then publish the skill so the harness lists/loads it. It publishes via
adoptwebui's app-path upload endpoint (``POST
/v1/end-user/agent-harness/skills``) -- no AWS/S3 credentials and no
adoptai-workflows import required, just an org-admin bearer JWT:

    python3 /path/to/noui/skills/noui-harness/publish_bundle.py \
        --token <ORG_ADMIN_JWT> [--org-id <ORG_UUID>] [--skill-name noui] \
        [--skill-md SKILL.md] [--aux noui-bundle.zip] \
        [--base-url https://api.adopt.ai] [--no-replace]

Defaults target THIS folder's ``SKILL.md`` + ``noui-bundle.zip`` and skill
name ``noui`` -- i.e. re-publishing the NoUI harness skill -- but every value
is a flag, so it publishes any harness skill to any org.

The server derives ``org_id`` from the token's own ``tenantId`` claim, not
from a body/query param -- ``--org-id`` here is only a client-side sanity
check against that claim (see ``plans/handoffs/enable-tabby-for-new-org.md``
for the story behind why that check exists: a token's variable name is not
proof of which org it's actually scoped to).

If the upload 403s with "Console admin required for this action", the caller
needs a console-admin role grant first (separate from the Frontegg JWT
``roles`` claim):

    PUT /v1/org/console-access/<sub claim of your JWT>
    {"email": "<you>", "role": "admin"}
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--token", required=True, help="org-admin bearer JWT")
    p.add_argument(
        "--org-id",
        default=None,
        help="expected target org UUID; only checked against the token's own "
        "tenantId claim (the server derives org_id from the token, not this flag)",
    )
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
    p.add_argument(
        "--base-url",
        default="https://api.adopt.ai",
        help="adoptwebui base URL (default: prod, https://api.adopt.ai)",
    )
    p.add_argument("--no-replace", action="store_true", help="fail if the skill already exists")
    return p.parse_args()


def _load_aux(specs: list[str] | None) -> list[dict]:
    if specs is None:
        specs = [f"noui-bundle.zip={_HERE / 'noui-bundle.zip'}"]
    aux: list[dict] = []
    for spec in specs:
        arcname, _, path = spec.partition("=")
        if not path:  # bare path -> arcname is the file's basename
            path, arcname = arcname, Path(arcname).name
        content_b64 = base64.b64encode(Path(path).read_bytes()).decode()
        aux.append({"path": arcname, "content_b64": content_b64})
    return aux


def _jwt_tenant_id(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("tenantId")
    except Exception:
        return None


def _main() -> int:
    args = _parse_args()

    skill_md_path = Path(args.skill_md)
    skill_md = skill_md_path.read_bytes()
    if not skill_md.lstrip().startswith(b"---"):
        raise SystemExit(
            f"refusing to publish: {args.skill_md} has no YAML frontmatter "
            "(a SKILL.md must start with '---'). Did you point --skill-md at the right file?"
        )

    tenant_id = _jwt_tenant_id(args.token)
    if args.org_id and tenant_id and args.org_id != tenant_id:
        raise SystemExit(
            f"refusing to publish: --org-id {args.org_id!r} does not match the "
            f"token's own tenantId claim {tenant_id!r}. The server derives org_id "
            "from the token, so this token would publish to a different org than "
            "intended -- get a token actually scoped to the target org."
        )

    aux = _load_aux(args.aux)
    payload = {
        "skill_name": args.skill_name,
        "skill_md_b64": base64.b64encode(skill_md).decode(),
        "aux_files": aux,
        "replace": not args.no_replace,
    }
    print(
        f"publishing skill '{args.skill_name}' to org={tenant_id or '<unknown, check token>'} "
        f"via {args.base_url} (SKILL.md {len(skill_md)}B, {len(aux)} aux file(s))"
    )

    req = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/v1/end-user/agent-harness/skills",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {args.token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        print(f"publish failed: HTTP {e.code}: {detail}", file=sys.stderr)
        if e.code == 403 and "Console admin" in detail:
            print(
                "hint: grant yourself console admin first via "
                "PUT /v1/org/console-access/<your JWT 'sub' claim> "
                '{"email": "<you>", "role": "admin"}',
                file=sys.stderr,
            )
        return 1

    keys = body.get("keys", [])
    print(f"published {len(keys)} object(s):")
    for k in keys:
        print("  ", k)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
