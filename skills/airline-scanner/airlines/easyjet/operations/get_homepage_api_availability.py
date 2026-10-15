#!/usr/bin/env python3
"""Skill operation: get_homepage_api_availability (PATCHED)
Method: GET
Path: /homepage/api/availability

Patched manually after auto-generation:
  - Accepts --origin, --destination, --date, --currency, --round-trip,
    --date-in CLI args
  - Uses stdlib urllib only (no httpx, no Tabby, no cdp_fetch)
  - Bootstraps EasyJet session cookies via homepage visit before calling
    the availability API
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.parse
import urllib.request
from typing import Any

BASE_URL = "https://www.easyjet.com"
_BOOTSTRAP_URL = "https://www.easyjet.com/en/"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)


def execute(
    origin: str = "LGW",
    destination: str = "AMS",
    date: str = "2026-07-15",
    currency: str = "GBP",
    round_trip: bool = False,
    date_in: str = "",
) -> dict[str, Any]:
    """Search EasyJet flights for a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "LGW", "LTN", "BRS").
        destination: Arrival IATA airport code (e.g. "AMS", "BCN").
        date: Outbound date in YYYY-MM-DD format.
        currency: Currency code for prices (e.g. "GBP", "EUR").
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

    # Bootstrap session cookies.
    with opener.open(_BOOTSTRAP_URL, timeout=15):
        pass

    params: dict[str, str] = {
        "origin": origin,
        "destination": destination,
        "currency": currency,
        "isReturn": "true" if round_trip else "false",
        "startDate": date,
        "endDate": date_in if round_trip and date_in else date,
    }
    url = f"{BASE_URL}/homepage/api/availability?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://www.easyjet.com/en/",
        },
        method="GET",
    )

    with opener.open(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="get_homepage_api_availability",
        description="Search EasyJet flights for a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA airport code, e.g. "LGW".')
    parser.add_argument(
        "--destination", required=True, help='Arrival IATA airport code, e.g. "AMS".'
    )
    parser.add_argument("--date", required=True, help="Outbound date in YYYY-MM-DD format.")
    parser.add_argument(
        "--currency", default="GBP", help="Currency code for prices (default: GBP)."
    )
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
            currency=args.currency,
            round_trip=args.round_trip,
            date_in=args.date_in,
        )
    except Exception as exc:
        print(f"get_homepage_api_availability failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
