#!/usr/bin/env python3
"""Skill operation: get_v4_en_us_availability (PATCHED)
Method: GET
Path: /api/booking/v4/en-us/availability

Patched manually after auto-generation:
  - Accepts --origin, --destination, --date, --adults, --teens, --children,
    --infants, --round-trip, --date-in CLI args
  - Uses stdlib urllib only (no httpx, no Tabby, no cdp_fetch)
  - Bootstraps Ryanair session cookies via homepage visit before calling the
    availability API (required to avoid 409 "Availability declined")
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.parse
import urllib.request
from typing import Any

BASE_URL = "https://www.ryanair.com"
_BOOTSTRAP_URL = "https://www.ryanair.com/us/en"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)


def execute(
    origin: str = "DUB",
    destination: str = "STN",
    date: str = "2026-07-15",
    adults: int = 1,
    teens: int = 0,
    children: int = 0,
    infants: int = 0,
    round_trip: bool = False,
    date_in: str = "",
) -> dict[str, Any]:
    """Search Ryanair flights for a given route and date.

    Args:
        origin: Origin IATA airport code (e.g. "DUB").
        destination: Destination IATA airport code (e.g. "STN").
        date: Outbound date in YYYY-MM-DD format.
        adults: Number of adult travelers.
        teens: Number of teen travelers.
        children: Number of child travelers.
        infants: Number of infant travelers.
        round_trip: Whether to search for a return flight.
        date_in: Return date in YYYY-MM-DD format (used when round_trip=True).
    """
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [
        ("User-Agent", _UA),
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        ("Accept-Language", "en-US,en;q=0.9"),
    ]

    # Bootstrap session cookies — Ryanair returns 409 without them.
    with opener.open(_BOOTSTRAP_URL, timeout=15):
        pass

    params: dict[str, str] = {
        "ADT": str(adults),
        "TEEN": str(teens),
        "CHD": str(children),
        "INF": str(infants),
        "Origin": origin,
        "Destination": destination,
        "promoCode": "",
        "IncludeConnectingFlights": "false",
        "DateOut": date,
        "DateIn": date_in,
        "DestinationIsMac": "false",
        "FlexDaysBeforeOut": "2",
        "FlexDaysOut": "2",
        "FlexDaysBeforeIn": "2",
        "FlexDaysIn": "2",
        "RoundTrip": "true" if round_trip else "false",
        "IncludePrimeFares": "false",
        "ToUs": "AGREED",
    }
    url = f"{BASE_URL}/api/booking/v4/en-us/availability?" + urllib.parse.urlencode(params)

    referer_qs = urllib.parse.urlencode(
        {
            "adults": adults,
            "teens": teens,
            "children": children,
            "infants": infants,
            "dateOut": date,
            "dateIn": date_in,
            "isConnectedFlight": "false",
            "discount": 0,
            "promoCode": "",
            "isReturn": "true" if round_trip else "false",
            "originIata": origin,
            "destinationMac": destination,
        }
    )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "client-version": "3.196.0",
            "client": "desktop",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": f"https://www.ryanair.com/us/en/trip/flights/select?{referer_qs}",
        },
        method="GET",
    )

    with opener.open(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="get_v4_en_us_availability",
        description="Search Ryanair flights for a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Origin IATA airport code, e.g. "DUB".')
    parser.add_argument(
        "--destination", required=True, help='Destination IATA airport code, e.g. "STN".'
    )
    parser.add_argument("--date", required=True, help="Outbound date in YYYY-MM-DD format.")
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
    parser.add_argument("--teens", type=int, default=0, help="Number of teen travelers.")
    parser.add_argument("--children", type=int, default=0, help="Number of child travelers.")
    parser.add_argument("--infants", type=int, default=0, help="Number of infant travelers.")
    parser.add_argument("--round-trip", action="store_true", help="Search for a return flight.")
    parser.add_argument(
        "--date-in", default="", help="Return date in YYYY-MM-DD format (used with --round-trip)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = execute(
            origin=args.origin,
            destination=args.destination,
            date=args.date,
            adults=args.adults,
            teens=args.teens,
            children=args.children,
            infants=args.infants,
            round_trip=args.round_trip,
            date_in=args.date_in,
        )
    except Exception as exc:
        print(f"get_v4_en_us_availability failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
