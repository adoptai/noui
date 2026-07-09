#!/usr/bin/env python3
"""Search Singapore Airlines for available flights on a given route and date.

SIA's booking form is protected by Akamai Bot Manager and uses server-side
session state (VSSESSION), making programmatic form submission unreliable.
Instead this skill calls the getHistogram.form API directly from inside
Tabby's browser, which returns fare prices for a 15-day window around the
requested date. The response includes total fare and tax per departure date,
and the lowest available cabin class for that date.

For full flight schedules (flight numbers, departure times), the SSR results
page at /flightsearch/searchFlight.form must be loaded via the booking form
workflow — which requires a human-initiated search session.
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

from noui_runtime.cdp import cdp_eval, find_page  # noqa: E402

CDP_HOST_MATCH = "singaporeair.com"
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
) -> dict[str, Any]:
    """Search Singapore Airlines for available flights on a given route and date.

    Returns fare prices for a 15-day window centred around the requested date.
    Each entry includes the departure date, base fare, tax, and total amount in
    the browser's currency (defaults to SGD for SIN-origin routes).

    Args:
        origin: Departure IATA airport code (e.g. "SIN", "LHR", "JFK").
        destination: Arrival IATA airport code (e.g. "LHR", "SIN", "SYD").
        date: Target departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, PREMIUM ECONOMY, BUSINESS, or FIRST.
    """
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile singapore-airlines-search"
        )

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

    js = (
        f"fetch({json.dumps(_HISTOGRAM_URL)}, {{"
        f"  method: 'POST',"
        f"  headers: {{'Content-Type': 'application/json', 'Accept': 'application/json'}},"
        f"  body: JSON.stringify({json.dumps(body)})"
        f"}}).then(r => r.text().then(t => JSON.stringify({{status: r.status, body: t}})))"
    )

    raw = await cdp_eval(ws_url, js)
    status = raw.get("status")
    body_str = raw.get("body", "")
    if not (isinstance(status, int) and 200 <= status < 300):
        raise RuntimeError(f"POST {_HISTOGRAM_URL} -> {status}: {body_str[:300]}")

    response_data = json.loads(body_str) if body_str else {}

    # Find the specific requested date's fare in the histogram window
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
