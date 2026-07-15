#!/usr/bin/env python3
"""Search Airbnb place autocomplete via Tabby execute/fetch.

Takes a free-text query and returns place suggestions with place_id and
display name, ready to pass to search_listings.
"""

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

AIRBNB_HOST = "https://www.airbnb.com"
AIRBNB_API_KEY = "d306zoyjsyarp7ifhu67rjxn52tv0t20"
DEFAULT_PROFILE = "airbnb"

_HEADERS = {
    "X-Airbnb-API-Key": AIRBNB_API_KEY,
    "X-Airbnb-Supports-Airlock-V2": "true",
    "X-CSRF-Without-Token": "1",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{AIRBNB_HOST}/",
}


def _profile_slug(override: str | None = None) -> str:
    return override or os.environ.get("PROFILE_SLUG") or DEFAULT_PROFILE


async def execute(
    query: str,
    num_results: int = 10,
    locale: str = "en",
    currency: str = "USD",
    profile_slug: str | None = None,
) -> dict:
    """Autocomplete Airbnb places via Tabby browser fetch."""
    params = {
        "locale": locale,
        "currency": currency,
        "key": AIRBNB_API_KEY,
        "language": locale,
        "num_results": str(num_results),
        "user_input": query,
        "api_version": "1.2.0",
        "vertical_refinement": "homes",
        "region": "-1",
        "options": (
            "should_filter_by_vertical_refinement|hide_nav_results|should_show_stays|simple_search"
        ),
    }
    url = f"{AIRBNB_HOST}/api/v2/autocompletes-personalized?{urllib.parse.urlencode(params)}"
    return await execute_fetch(_profile_slug(profile_slug), url, headers=_HEADERS)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="search_places",
        description="Airbnb place autocomplete — returns place_id suggestions.",
    )
    p.add_argument("--query", required=True, help='Free-text place name (e.g. "Paris").')
    p.add_argument("--num-results", dest="num_results", type=int, default=10)
    p.add_argument("--locale", default="en")
    p.add_argument("--currency", default="USD")
    p.add_argument(
        "--profile-slug",
        dest="profile_slug",
        default=None,
        help=f"Tabby profile slug (default: $PROFILE_SLUG or {DEFAULT_PROFILE}).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = asyncio.run(
            execute(
                query=args.query,
                num_results=args.num_results,
                locale=args.locale,
                currency=args.currency,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"search_places failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
