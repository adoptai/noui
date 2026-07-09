#!/usr/bin/env python3
"""Search British Airways for available one-way flights on a given route and date.

BA's BFF API (Next.js backend-for-frontend) is a public GET endpoint that does
not require bot protection or session tokens. Headers include static BA app IDs
and fresh UUIDs for correlation tracking.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

_SEARCH_URL = "https://www.britishairways.com/nx/b/bff/offer-flight/v0/oneway/outbound"
_CABIN_MAP = {
    "ECONOMY": "economy",
    "PREMIUM_ECONOMY": "premiumeconomy",
    "BUSINESS": "business",
    "FIRST": "first",
}

_SKILL_ROOT = Path(__file__).resolve().parent.parent


def _search(
    origin: str,
    destination: str,
    date: str,
    cabin: str,
    adults: int,
    children: int,
    infants: int,
) -> dict[str, Any]:
    params = (
        f"?from={origin}&to={destination}&departureDate={date}"
        f"&adults={adults}&youngAdults=0&children={children}&infants={infants}"
        f"&page=1&maxResult=100&travelClass={cabin}"
    )
    req = urllib.request.Request(
        _SEARCH_URL + params,
        headers={
            "Accept": "*/*",
            "x-ba-application-name": "airselect",
            "x-ba-client-name": "airselect",
            "x-ba-market": "us",
            "x-ba-language": "en",
            "x-ba-channel": "WEB",
            "x-ba-interaction-id": str(uuid.uuid4()),
            "x-ba-request-id": str(uuid.uuid4()),
            "x-ba-track-id": str(uuid.uuid4()),
            "x-ba-device-id": str(uuid.uuid4()),
            "x-ba-user-anon-id": str(uuid.uuid4()),
            "x-amzn-waf-ba-rule": "EMPTY",
            "x-ba-action-name": "search-flights-get-flights-oneway-outbound",
            "Content-Type": "application/json",
            "Referer": "https://www.britishairways.com/nx/b/airselect/en/usa/book/search/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


async def execute(
    origin: str = "LHR",
    destination: str = "JFK",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
) -> dict[str, Any]:
    """Search British Airways for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "LHR", "LGW", "JFK").
        destination: Arrival IATA airport code (e.g. "JFK", "LHR", "SYD").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
        children: Number of child travelers.
        infants: Number of infant travelers.
    """
    cabin = _CABIN_MAP.get(cabin_class.upper(), "economy")
    return await asyncio.get_event_loop().run_in_executor(
        None, _search, origin, destination, date, cabin, adults, children, infants
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search British Airways for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "LHR".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "JFK".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument(
        "--cabin-class",
        default="ECONOMY",
        choices=["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"],
        help="Cabin class (default: ECONOMY).",
    )
    parser.add_argument("--adults", type=int, default=1)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--infants", type=int, default=0)
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
                children=args.children,
                infants=args.infants,
            )
        )
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"search_flights failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"search_flights failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and result.get("error"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
