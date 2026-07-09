#!/usr/bin/env python3
"""Skill operation: create_air_booking_shopping (PATCHED)
Method: POST
Path: /api/air-booking/v1/air-booking/page/air/booking/shopping

Patched manually after auto-generation:
  - Accepts --origin, --destination, --date, --adults, --round-trip, --date-in CLI args
  - Drops stale EE30zvQLWf-* Akamai sensor headers — Akamai's JS intercepts fetch()
    inside the real browser and adds them automatically via cdp_fetch
  - Generates a fresh X-User-Experience-ID UUID per request
  - Requires Tabby with an active southwest-search session (Akamai bot protection)
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

BASE_URL = "https://www.southwest.com"
CDP_HOST_MATCH = "www.southwest.com"

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
    url = f"{BASE_URL}/api/air-booking/v1/air-booking/page/air/booking/shopping"

    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile southwest-search"
        )

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

    headers = {
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
    }

    return await cdp_fetch(ws_url, url, method="POST", body=body, headers=headers)


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
