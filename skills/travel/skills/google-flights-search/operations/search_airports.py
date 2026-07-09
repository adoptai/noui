#!/usr/bin/env python3
"""Search Google Flights airports/cities via Tabby execute/fetch.

RPC: H028ib (airport/city autocomplete) on FlightsFrontendUi batchexecute.
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

BASE_URL = "https://www.google.com"
DEFAULT_PROFILE = "google-flights"

_QS = {
    "rpcids": "H028ib",
    "source-path": "/travel/flights",
    "f.sid": "-7456312471093751493",
    "bl": "boq_travel-frontend-flights-ui_20260330.02_p2",
    "hl": "en",
    "soc-app": "162",
    "soc-platform": "1",
    "soc-device": "1",
    "rt": "c",
}


def _profile_slug(override: str | None = None) -> str:
    return override or os.environ.get("PROFILE_SLUG") or DEFAULT_PROFILE


async def execute(
    query: str,
    csrf_token: str = "",
    profile_slug: str | None = None,
) -> dict:
    """Search for airports and cities by name.

    Returns matching locations with Google entity IDs
    (e.g. '/m/022pfm' for São Paulo) needed by get_calendar / search_flights.
    """
    freq = json.dumps(
        [[["H028ib", json.dumps([query, [1, 2, 3, 5], None, [2], 1]), None, "generic"]]]
    )
    body_parts = {"f.req": freq}
    if csrf_token:
        body_parts["at"] = csrf_token
    body = urllib.parse.urlencode(body_parts)
    url = f"{BASE_URL}/_/FlightsFrontendUi/data/batchexecute?{urllib.parse.urlencode(_QS)}"

    return await execute_fetch(
        _profile_slug(profile_slug),
        url,
        method="POST",
        body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="search_airports")
    p.add_argument("--query", required=True, help="Airport or city name")
    p.add_argument("--csrf-token", dest="csrf_token", default="")
    p.add_argument("--profile-slug", dest="profile_slug", default=None)
    args = p.parse_args(argv)
    try:
        result = asyncio.run(
            execute(
                query=args.query,
                csrf_token=args.csrf_token,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"search_airports failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
