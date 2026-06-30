#!/usr/bin/env python3
"""Skill operation: create_search_api_shoulderdates (PATCHED)
Method: POST
Path: /search/api/shoulderDates

Patched manually after auto-generation:
  - Accepts --origin, --destination, --date, --adults CLI args
  - Returns prices per date around the search date (price calendar)
  - Requires Tabby with an active alaska-search session
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.cdp import cdp_fetch, find_page  # noqa: E402

BASE_URL = "https://www.alaskaair.com"
CDP_HOST_MATCH = "alaskaair.com"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


async def execute(
    origin: str = "SEA",
    destination: str = "LAX",
    date: str = "2026-08-21",
    adults: int = 1,
) -> dict[str, Any]:
    """Search Alaska Airlines for lowest fares on and around a given date.

    Args:
        origin: Departure IATA airport code (e.g. "SEA", "PDX", "ANC").
        destination: Arrival IATA airport code (e.g. "LAX", "JFK", "ORD").
        date: Target departure date in YYYY-MM-DD format.
        adults: Number of adult travelers.
    """
    url = f"{BASE_URL}/search/api/shoulderDates"

    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile alaska-search"
        )

    body = {
        "origins": [origin],
        "destinations": [destination],
        "dates": [date],
        "onba": False,
        "dnba": False,
        "numADTs": adults,
        "numCHDs": 0,
        "isAddingToAdultRes": False,
        "sliceToSearch": 0,
        "sliceSelections": [],
        "selectedSegments": [],
        "fareView": "None",
        "discount": {
            "code": "",
            "status": 0,
            "searchContainsDiscountedFare": False,
        },
        "isAlaska": True,
        "isWholeTripPricing": True,
        "businessRequest": {
            "TravelerId": "",
            "BusinessRequestType": 0,
            "CountryCode": "",
            "StateCode": "",
            "ShowOnlySpecialFares": True,
        },
    }

    headers = {
        "User-Agent": _UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.alaskaair.com",
        "Referer": "https://www.alaskaair.com/search/results",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Accept-Language": "en-US,en;q=0.9",
    }

    return await cdp_fetch(ws_url, url, method="POST", body=body, headers=headers)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_search_api_shoulderdates",
        description="Search Alaska Airlines for lowest fares on and around a given date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "SEA".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "LAX".')
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
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
            )
        )
    except Exception as exc:
        print(f"create_search_api_shoulderdates failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
