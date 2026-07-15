#!/usr/bin/env python3
"""Retrieve Flydubai route calendar via Tabby execute/fetch."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from noui_runtime.execute import execute_fetch

DEFAULT_PROFILE = "flydubai"


def _profile_slug(override: str | None = None) -> str:
    return override or os.environ.get("PROFILE_SLUG") or DEFAULT_PROFILE


async def execute(
    origin: str,
    destination: str,
    from_date: str = "",
    is_origin_metro: str = "false",
    is_dest_metro: str = "false",
    profile_slug: str | None = None,
) -> dict:
    url = f"https://www.flydubai.com/api/Calendar/{origin}/{destination}"
    params = {
        "fromDate": from_date,
        "isOriginMetro": is_origin_metro,
        "isDestMetro": is_dest_metro,
    }
    params = {k: v for k, v in params.items() if v not in ("", None)}
    if params:
        url += "?" + urllib.parse.urlencode(params)

    return await execute_fetch(
        _profile_slug(profile_slug),
        url,
        headers={
            "Accept": "application/json,*/*",
            "Referer": "https://www.flydubai.com/en-in/",
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="get_calendar")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--from-date", dest="from_date", default="")
    parser.add_argument("--is-origin-metro", dest="is_origin_metro", default="false")
    parser.add_argument("--is-dest-metro", dest="is_dest_metro", default="false")
    parser.add_argument("--profile-slug", dest="profile_slug", default=None)
    args = parser.parse_args(argv)

    try:
        result = asyncio.run(
            execute(
                origin=args.origin,
                destination=args.destination,
                from_date=args.from_date,
                is_origin_metro=args.is_origin_metro,
                is_dest_metro=args.is_dest_metro,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"get_calendar failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
