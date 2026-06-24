#!/usr/bin/env python3
"""Build ``noui-bundle.zip`` (the NoUI toolkit) into this folder.

The publishable ``noui`` harness skill ships the toolkit as a single binary aux
zip; the harness stages it into the sandbox and the agent runs ``unzip`` then
``pip install -e .``. The zip's root is the bundle root (``pyproject.toml`` at
top). Excludes the venv/workbench/caches/.env so it stays small and clean.

Run:  python skills/noui-harness/build_bundle.py
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # skills/noui-harness/
_BUNDLE = _HERE.parent / "noui"  # skills/noui/ (toolkit source)
_OUT = _HERE / "noui-bundle.zip"
_EXCLUDE_DIRS = {".venv", "workbench", "__pycache__", ".git", "noui.egg-info"}


def build() -> Path:
    if not (_BUNDLE / "pyproject.toml").exists():
        raise SystemExit(f"toolkit bundle not found at {_BUNDLE} (expected pyproject.toml)")
    buf = io.BytesIO()
    manifest: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(_BUNDLE.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(_BUNDLE)
            if any(part in _EXCLUDE_DIRS for part in rel.parts):
                continue
            # Drop the venv/caches and any env files: in the harness the toolkit
            # is configured by env vars the broker/harness inject, so a shipped
            # .env / .env.example is dead weight and only invites the agent to
            # "configure credentials" (which it must not do).
            if p.suffix == ".pyc" or rel.name in (".env", ".env.example"):
                continue
            zf.write(p, str(rel))
            manifest.append(str(rel))
    _OUT.write_bytes(buf.getvalue())
    print(f"wrote {_OUT} ({_OUT.stat().st_size} bytes, {len(manifest)} files)")
    print("includes pyproject.toml:", "pyproject.toml" in manifest)
    return _OUT


if __name__ == "__main__":
    build()
