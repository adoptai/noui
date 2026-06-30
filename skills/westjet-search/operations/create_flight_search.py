#!/usr/bin/env python3
"""Skill operation: create_flight_search (PATCHED)
Method: POST
Path: /ecomm/booktrip/flight-search-api/v1

Patched manually after auto-generation:
  - Accepts --origin, --destination, --date, --adults, --children, --infants CLI args
  - Returns available WestJet flights for the given route and date
  - Requires Tabby with an active westjet-search session (Kasada protection)
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

BASE_URL = "https://apiw.westjet.com"
CDP_HOST_MATCH = "westjet.com"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


async def execute(
    origin: str = "YYC",
    destination: str = "YYZ",
    date: str = "2026-08-20",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    currency: str = "CAD",
) -> dict[str, Any]:
    """Search WestJet for available flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "YYC", "YVR", "YYZ").
        destination: Arrival IATA airport code (e.g. "YYZ", "YYC", "YVR").
        date: Departure date in YYYY-MM-DD format.
        adults: Number of adult travelers.
        children: Number of child travelers.
        infants: Number of infant travelers.
        currency: Currency code (default: CAD).
    """
    url = f"{BASE_URL}/ecomm/booktrip/flight-search-api/v1"

    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile westjet-search"
        )

    body = {
        "appSource": "widgetOW",
        "bookId": str(uuid.uuid4()),
        "isBereavement": False,
        "isCompanion": False,
        "currency": currency,
        "currentFlightIndex": 1,
        "guests": [
            {"type": "adult", "count": str(adults)},
            {"type": "child", "count": str(children)},
            {"type": "infant", "count": str(infants)},
        ],
        "showMemberExclusives": False,
        "showTravelPrivileges": False,
        "trips": [
            {
                "arrival": destination,
                "calLowestPrice": "",
                "departure": origin,
                "departureDate": date,
                "order": 1,
            }
        ],
        "isCommissionable": False,
        "promoCode": "",
    }

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://www.westjet.com",
        "Referer": "https://www.westjet.com/",
        "User-Agent": _UA,
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Accept-Language": "en-US,en;q=0.9",
    }

    return await cdp_fetch(ws_url, url, method="POST", body=body, headers=headers)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_flight_search",
        description="Search WestJet for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "YYC".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "YYZ".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
    parser.add_argument("--children", type=int, default=0, help="Number of child travelers.")
    parser.add_argument("--infants", type=int, default=0, help="Number of infant travelers.")
    parser.add_argument("--currency", default="CAD", help="Currency code (default: CAD).")
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
                currency=args.currency,
            )
        )
    except Exception as exc:
        print(f"create_flight_search failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
