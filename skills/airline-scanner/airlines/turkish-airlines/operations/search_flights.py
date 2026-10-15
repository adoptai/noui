#!/usr/bin/env python3
"""Search Turkish Airlines for available flights on a given route and date.

Turkish Airlines uses PerimeterX bot protection. Requests run inside Tabby's
browser session via execute_fetch (POST /execute/fetch) — Tabby's real browser
session includes PX cookies that satisfy the PerimeterX challenge.
Date format accepted by the API is DD-MM-YYYY.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.execute import execute_fetch  # noqa: E402

_PROFILE_ID = "turkish-airlines"
_SEARCH_URL = "https://www.turkishairlines.com/api/v1/availability"
_CABIN_CODES = {
    "ECONOMY": "ECONOMY",
    "BUSINESS": "BUSINESS",
    "FIRST": "FIRST",
}


async def execute(
    origin: str = "IST",
    destination: str = "LHR",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search Turkish Airlines for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "IST", "LHR", "JFK").
        destination: Arrival IATA airport code (e.g. "LHR", "IST", "NYC").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    profile_id = profile_slug or _PROFILE_ID
    cabin = _CABIN_CODES.get(cabin_class.upper(), "ECONOMY")

    d = datetime.strptime(date, "%Y-%m-%d")
    dept_date = d.strftime("%d-%m-%Y")
    now_utc = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    body: dict[str, Any] = {
        "selectedBookerSearch": "O",
        "selectedCabinClass": cabin,
        "moduleType": "TICKETING",
        "passengerTypeList": [{"quantity": adults, "code": "ADULT"}],
        "originDestinationInformationList": [
            {
                "originAirportCode": origin,
                "originCountryCode": "",
                "originMultiPort": False,
                "originDomestic": False,
                "destinationAirportCode": destination,
                "destinationCountryCode": "",
                "destinationMultiPort": False,
                "destinationDomestic": False,
                "departureDate": dept_date,
                "originCityCode": origin,
                "destinationCityCode": destination,
                "originCity": origin,
                "destinationCity": destination,
            }
        ],
        "savedDate": now_utc,
        "preselectedOptionDetails": [],
    }

    return await execute_fetch(
        profile_id,
        _SEARCH_URL,
        method="POST",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-platform": "WEB",
            "X-country": "us",
            "X-conversationId": str(uuid.uuid4()),
            "X-requestId": str(uuid.uuid4()),
            "Origin": "https://www.turkishairlines.com",
            "Referer": "https://www.turkishairlines.com/",
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search Turkish Airlines for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "IST".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "LHR".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument(
        "--cabin-class",
        default="ECONOMY",
        choices=["ECONOMY", "BUSINESS", "FIRST"],
        help="Cabin class (default: ECONOMY).",
    )
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
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
                cabin_class=args.cabin_class,
                adults=args.adults,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"search_flights failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
