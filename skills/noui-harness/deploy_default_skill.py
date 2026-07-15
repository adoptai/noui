#!/usr/bin/env python3
"""Deploy a harness skill to the platform **default** ("Built-in") tier.

CI counterpart to ``build_bundle.py`` + ``publish_bundle.py``. Unlike
``publish_bundle.py`` (which uploads an *org*-scoped copy via a pre-minted
org-admin JWT), this targets the platform-wide default tier:

    POST {base}/v1/end-user/agent-harness/default-skills

That endpoint is gated by ``require_adopt_user`` (any ``@adopt.ai`` JWT), so
this script mints the JWT itself from an Adopt service-account PAT
(client_id/secret) via ``POST {base}/v1/users/api-token`` -- the exact flow a
CI job uses. The token is never printed or written to disk.

Env vars (all overridable by the matching flag):

    ADOPT_BASE_URL         webui base, e.g. https://api.adopt.ai            (required)
    ADOPT_CLIENT_ID        Adopt service-account PAT client id              (required)
    ADOPT_CLIENT_SECRET    Adopt service-account PAT secret                 (required)
    SKILL_NAME             catalog slug (default: noui)
    BUNDLE_VERSION         CI label recorded on the pointer (default: "dev")
    MIN_PLATFORM_CONTRACT  runtime contract the bundle needs (default: 1)
    SKILL_MD               path to SKILL.md (default: this folder's SKILL.md)
    AUX                    ':'-separated aux files as 'arcname=path' or 'path'
                           (default: this folder's noui-bundle.zip)
    DRY_RUN                if truthy, validate + assemble the payload and exit
                           without minting a token or publishing (PR-time check;
                           needs no base URL or credentials)

Publish is idempotent for identical bytes (content-addressed ``v=<hash>/``); a
re-run with an unchanged bundle is a safe no-op pointer confirm. Exits non-zero
on any HTTP error or if the post-publish verify does not report the skill live.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise SystemExit(f"missing required env var: {name}")
    return val or ""


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _mint_token(base: str, client_id: str, secret: str) -> str:
    """Exchange a service-account PAT for a short-lived bearer JWT.

    Returns the raw access_token; the caller must never log it."""
    body = _post_json(
        f"{base}/v1/users/api-token",
        {"client_id": client_id, "secret": secret},
        headers={},
    )
    token = body.get("access_token")
    if not token:
        raise SystemExit("token mint succeeded but response had no access_token")
    return token


def _load_aux(spec: str | None) -> list[dict]:
    if not spec:
        specs = [f"noui-bundle.zip={_HERE / 'noui-bundle.zip'}"]
    else:
        specs = [s for s in spec.split(":") if s]
    aux: list[dict] = []
    for item in specs:
        arcname, _, path = item.partition("=")
        if not path:  # bare path -> arcname is the basename
            path, arcname = arcname, Path(arcname).name
        aux.append(
            {"path": arcname, "content_b64": base64.b64encode(Path(path).read_bytes()).decode()}
        )
    return aux


def main() -> int:
    dry_run = _env("DRY_RUN", "").lower() in ("1", "true", "yes")
    skill_name = _env("SKILL_NAME", "noui")
    bundle_version = _env("BUNDLE_VERSION", "dev")
    min_contract = int(_env("MIN_PLATFORM_CONTRACT", "1"))
    skill_md_path = Path(_env("SKILL_MD", str(_HERE / "SKILL.md")))
    aux = _load_aux(os.environ.get("AUX"))

    skill_md = skill_md_path.read_bytes()
    if not skill_md.lstrip().startswith(b"---"):
        raise SystemExit(f"refusing to publish: {skill_md_path} has no YAML frontmatter")

    payload = {
        "skill_name": skill_name,
        "skill_md_b64": base64.b64encode(skill_md).decode(),
        "aux_files": aux,
        "bundle_version": bundle_version,
        "min_platform_contract": min_contract,
    }

    print(
        f"skill='{skill_name}' bundle_version={bundle_version} "
        f"min_platform_contract={min_contract} SKILL.md={len(skill_md)}B "
        f"aux={[(a['path'], len(base64.b64decode(a['content_b64']))) for a in aux]}"
    )

    if dry_run:
        # PR-time safety check: prove the bundle is publishable (frontmatter,
        # non-empty aux, payload assembles) WITHOUT minting a token or writing
        # anything. Needs no base URL or credentials, so it runs on every PR.
        for a in aux:
            if not a["content_b64"]:
                raise SystemExit(f"aux file {a['path']!r} is empty")
        approx = len(payload["skill_md_b64"]) + sum(len(a["content_b64"]) for a in aux)
        print(f"DRY RUN OK: payload assembles (~{approx}B base64); skipping mint + publish.")
        return 0

    base = _env("ADOPT_BASE_URL", required=True).rstrip("/")
    client_id = _env("ADOPT_CLIENT_ID", required=True)
    client_secret = _env("ADOPT_CLIENT_SECRET", required=True)
    print(f"publishing to {base} (default tier)")

    try:
        token = _mint_token(base, client_id, client_secret)
        auth = {"Authorization": f"Bearer {token}"}
        result = _post_json(f"{base}/v1/end-user/agent-harness/default-skills", payload, auth)
        print(f"published content_dir={result.get('content_dir')}:")
        for k in result.get("keys", []):
            print("  ", k)

        pointer = _get_json(f"{base}/v1/end-user/agent-harness/default-skills/{skill_name}", auth)
    except urllib.error.HTTPError as e:
        # Surface the upstream reason (409 min_platform_contract / 422 bad bundle),
        # never the request auth header.
        print(f"deploy failed: HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1

    if not pointer.get("published"):
        print(f"verify FAILED: skill not live after publish: {pointer}", file=sys.stderr)
        return 1
    print(
        f"verified live: published={pointer.get('published')} "
        f"bundle_version={pointer.get('bundle_version')} "
        f"content_dir={pointer.get('content_dir')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
