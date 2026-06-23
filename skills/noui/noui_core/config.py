"""NoUI core configuration.

Slim settings object read from environment / `.env`. Carries only what the
three pillars (capture / compile / activate) need to reach Tabby. The former
FastAPI backend, its SQLite store, and the Anthropic key are gone — the
record→compile→export pipeline makes no LLM calls.

Resolution order for `.env`: the bundle root (``skills/noui/.env``) first, then
the current working directory, so an agent can drop a `.env` next to wherever it
runs the scripts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the bundle root (two levels up: noui_core/ -> skills/noui/),
# then from CWD. Later loads do not override already-set vars.
_BUNDLE_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_BUNDLE_ROOT / ".env")
load_dotenv(Path.cwd() / ".env")


@dataclass
class Settings:
    # Tabby API — the only external dependency.
    tabby_api_host: str = "http://localhost:8000"
    tabby_admin_token: str = ""

    # Default output dir for generated assets (workbench inside the bundle).
    workbench_dir: str = ""

    def __post_init__(self) -> None:
        if not self.workbench_dir:
            self.workbench_dir = str(_BUNDLE_ROOT / "workbench")


settings = Settings(
    tabby_api_host=os.environ.get("TABBY_API_URL", "http://localhost:8000"),
    tabby_admin_token=os.environ.get("TABBY_ADMIN_TOKEN", ""),
    workbench_dir=os.environ.get("NOUI_WORKBENCH_DIR", ""),
)
