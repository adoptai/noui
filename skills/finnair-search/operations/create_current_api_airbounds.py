#!/usr/bin/env python3
"""Skill operation: create_current_api_airbounds (PATCHED)
Method: POST
Path: /d/fcom/offers-prod/current/api/airBounds

Patched manually after auto-generation:
  - Accepts --origin, --destination, --date, --adults, --children, --infants CLI args
  - Fixed CDP_HOST_MATCH: browser is on www.finnair.com, not api.finnair.com
  - Generates a fresh X-Session-Id UUID per request (recording used a stale one)
  - Requires Tabby with an active finnair-search session (Akamai bot protection)
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

from noui_runtime.cdp import cdp_fetch, find_page  # noqa: E402

BASE_URL = "https://api.finnair.com"
CDP_HOST_MATCH = "finnair.com"

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
    url = f"{BASE_URL}/d/fcom/offers-prod/current/api/airBounds"

    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile finnair-search"
        )

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

    headers = {
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
    }

    return await cdp_fetch(ws_url, url, method="POST", body=body, headers=headers)


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
