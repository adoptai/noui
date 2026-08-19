#!/usr/bin/env python3
"""Skill operation: create_v1_search_ngb
Method: POST
Path: /cb-flight-search/v1/search/NGB

No Tabby required — static ocp-apim-subscription-key is sufficient for search.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any

BASE_URL = "https://cb-api.jetblue.com"

_OCP_KEY = "a5ee654e981b4577a58264fed9b1669c"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


def execute(
    origin: str = "JFK",
    destination: str = "BOS",
    date: str = "2026-08-21",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
) -> dict[str, Any]:
    """Search JetBlue flights for a given route and date.

    Args:
        origin: Origin metro or airport code (e.g. "JFK", "NYC", "BOS").
        destination: Destination metro or airport code (e.g. "BOS", "LAX", "LON").
        date: Departure date in YYYY-MM-DD format.
        adults: Number of adult travelers.
        children: Number of child travelers.
        infants: Number of infant travelers.
    """
    url = f"{BASE_URL}/cb-flight-search/v1/search/NGB"

    traveler_types: list[dict[str, Any]] = [{"type": "ADULT", "quantity": adults}]
    if children > 0:
        traveler_types.append({"type": "CHILD", "quantity": children})
    if infants > 0:
        traveler_types.append({"type": "INFANT", "quantity": infants})

    body = {
        "awardBooking": False,
        "travelerTypes": traveler_types,
        "searchComponents": [
            {
                "from": origin,
                "to": destination,
                "date": date,
            }
        ],
    }

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "ocp-apim-subscription-key": _OCP_KEY,
            "Origin": "https://www.jetblue.com",
            "Referer": "https://www.jetblue.com/",
            "User-Agent": _UA,
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_v1_search_ngb",
        description="Search JetBlue flights for a given route and date.",
    )
    parser.add_argument(
        "--origin", required=True, help='Origin metro/airport code, e.g. "JFK" or "NYC".'
    )
    parser.add_argument(
        "--destination", required=True, help='Destination metro/airport code, e.g. "BOS" or "LAX".'
    )
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
    parser.add_argument("--children", type=int, default=0, help="Number of child travelers.")
    parser.add_argument("--infants", type=int, default=0, help="Number of infant travelers.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = execute(
            origin=args.origin,
            destination=args.destination,
            date=args.date,
            adults=args.adults,
            children=args.children,
            infants=args.infants,
        )
    except Exception as exc:
        print(f"create_v1_search_ngb failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
