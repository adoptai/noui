#!/usr/bin/env python3
"""Skill operation: create_air_booking_shopping
Method: POST
Path: /api/air-booking/v1/air-booking/page/air/booking/shopping

Southwest uses Akamai bot protection. Akamai's JS intercepts fetch() inside the
real browser and adds sensor headers automatically — that's why we run requests
inside Tabby's browser session via execute_fetch.
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

_PROFILE_ID = "southwest"
_SEARCH_URL = "https://www.southwest.com/api/air-booking/v1/air-booking/page/air/booking/shopping"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
_API_KEY = "l7xx944d175ea25f4b9c903a583ea82a1c4c"


async def execute(
    origin: str = "LAX",
    destination: str = "LGA",
    date: str = "2026-08-21",
    adults: int = 1,
    round_trip: bool = False,
    date_in: str = "",
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search Southwest flights for a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "LAX", "DAL").
        destination: Arrival IATA airport code (e.g. "LGA", "MDW").
        date: Departure date in YYYY-MM-DD format.
        adults: Number of adult travelers.
        round_trip: Whether to search for a return flight.
        date_in: Return date in YYYY-MM-DD format (used when round_trip=True).
    """
    profile_id = profile_slug or _PROFILE_ID

    body = {
        "adultPassengersCount": str(adults),
        "adultsCount": str(adults),
        "departureDate": date,
        "departureTimeOfDay": "ALL_DAY",
        "destinationAirportCode": destination,
        "fareType": "USD",
        "int": "HOMEQBOMAIR",
        "originationAirportCode": origin,
        "passengerType": "ADULT",
        "promoCode": "",
        "returnDate": date_in if round_trip and date_in else "",
        "returnTimeOfDay": "ALL_DAY",
        "tripType": "roundtrip" if round_trip else "oneway",
        "application": "air-booking",
        "site": "southwest",
    }

    return await execute_fetch(
        profile_id,
        _SEARCH_URL,
        method="POST",
        body=body,
        headers={
            "User-Agent": _UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "X-API-Key": _API_KEY,
            "X-App-ID": "air-booking",
            "X-Channel-ID": "southwest",
            "X-User-Experience-ID": str(uuid.uuid4()),
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "Origin": "https://www.southwest.com",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://www.southwest.com/air/booking/",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_air_booking_shopping",
        description="Search Southwest flights for a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "LAX".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "LGA".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
    parser.add_argument("--round-trip", action="store_true", help="Search for a return flight.")
    parser.add_argument("--date-in", default="", help="Return date in YYYY-MM-DD format.")
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
                round_trip=args.round_trip,
                date_in=args.date_in,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"create_air_booking_shopping failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
