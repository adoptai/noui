#!/usr/bin/env python3
"""Skill operation: create_current_api_airbounds
Method: POST
Path: /d/fcom/offers-prod/current/api/airBounds

Finnair uses Akamai bot protection — requests run inside Tabby's browser session
via execute_fetch (POST /execute/fetch).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.execute import execute_fetch  # noqa: E402

_PROFILE_ID = "finnair"
_SEARCH_URL = "https://api.finnair.com/d/fcom/offers-prod/current/api/airBounds"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


async def execute(
    origin: str = "HEL",
    destination: str = "LON",
    date: str = "2026-08-21",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search Finnair flights for a given route and date.

    Args:
        origin: Departure IATA airport or city code (e.g. "HEL", "NYC").
        destination: Arrival IATA airport or city code (e.g. "LON", "BKK").
        date: Departure date in YYYY-MM-DD format.
        adults: Number of adult travelers.
        children: Number of child travelers.
        infants: Number of infant travelers.
    """
    profile_id = profile_slug or _PROFILE_ID

    body = {
        "locale": "en_US",
        "cabin": "MIXED",
        "travelers": {
            "adults": adults,
            "children": children,
            "c15s": 0,
            "infants": infants,
        },
        "itineraries": [
            {
                "directFlights": False,
                "departureLocationCode": origin,
                "destinationLocationCode": destination,
                "departureDate": date,
                "isRequestedBound": True,
            }
        ],
    }

    return await execute_fetch(
        profile_id,
        _SEARCH_URL,
        method="POST",
        body=body,
        headers={
            "User-Agent": _UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Client-Id": "FCOM",
            "X-Session-Id": str(uuid.uuid4()),
            "x-dd-flow-type": "flight",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "Origin": "https://www.finnair.com",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://www.finnair.com/",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_current_api_airbounds",
        description="Search Finnair flights for a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "HEL".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "LON".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
    parser.add_argument("--children", type=int, default=0, help="Number of child travelers.")
    parser.add_argument("--infants", type=int, default=0, help="Number of infant travelers.")
    parser.add_argument("--profile-slug", dest="profile_slug", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = asyncio.run(
            execute(
                origin=args.origin,
                destination=args.destination,
                date=args.date,
                adults=args.adults,
                children=args.children,
                infants=args.infants,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"create_current_api_airbounds failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
