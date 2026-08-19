#!/usr/bin/env python3
"""Search Singapore Airlines for available flights on a given route and date.

SIA's booking API is protected by Akamai Bot Manager. This skill calls the
getHistogram.form API from inside Tabby's browser session via execute_fetch,
which returns fare prices for a 15-day window around the requested date.
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

from noui_runtime.execute import execute_fetch  # noqa: E402

_PROFILE_ID = "singapore-airlines"
_HISTOGRAM_URL = "https://www.singaporeair.com/home/getHistogram.form"

_CABIN_CODES = {
    "ECONOMY": "Y",
    "PREMIUM ECONOMY": "W",
    "BUSINESS": "J",
    "FIRST": "F",
}


async def execute(
    origin: str = "SIN",
    destination: str = "LHR",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search Singapore Airlines for available flights on a given route and date.

    Returns fare prices for a 15-day window centred around the requested date.

    Args:
        origin: Departure IATA airport code (e.g. "SIN", "LHR", "JFK").
        destination: Arrival IATA airport code (e.g. "LHR", "SIN", "SYD").
        date: Target departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, PREMIUM ECONOMY, BUSINESS, or FIRST.
    """
    profile_id = profile_slug or _PROFILE_ID
    cabin_code = _CABIN_CODES.get(cabin_class.upper(), "Y")

    body = {
        "request": {
            "itineraryDetails": {
                "originAirportCode": origin,
                "destinationAirportCode": destination,
                "departureDate": date,
            },
            "cabinClass": cabin_code,
        }
    }

    response_data = await execute_fetch(
        profile_id,
        _HISTOGRAM_URL,
        method="POST",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://www.singaporeair.com",
            "Referer": "https://www.singaporeair.com/",
        },
    )

    fares = response_data.get("histogramResponse", {}).get("fares", [])
    target_fare = next((f for f in fares if f.get("departureDate") == date), None)

    return {
        "origin": origin,
        "destination": destination,
        "requestedDate": date,
        "cabinClass": cabin_class,
        "targetDateFare": target_fare,
        "fareWindow": fares,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search Singapore Airlines for fare availability on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "SIN".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "LHR".')
    parser.add_argument("--date", required=True, help="Target departure date in YYYY-MM-DD format.")
    parser.add_argument(
        "--cabin-class",
        default="ECONOMY",
        choices=["ECONOMY", "PREMIUM ECONOMY", "BUSINESS", "FIRST"],
        help="Cabin class (default: ECONOMY).",
    )
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
