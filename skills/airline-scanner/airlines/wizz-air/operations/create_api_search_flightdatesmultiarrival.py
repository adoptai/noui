#!/usr/bin/env python3
"""Skill operation: create_api_search_flightdatesmultiarrival (PATCHED)
Method: POST
Path: /28.10.1/Api/search/FlightDatesMultiArrival

Patched manually after auto-generation:
  - Correct body format from HAR: top-level departureStation, arrivalStations array,
    ISO timestamp dates, isReturn flag — NOT the flightList wrapper that was auto-generated
  - Accepts --origin, --destination, --date, --days CLI args
  - Fetches X-RequestVerificationToken dynamically from live browser cookies
  - Drops stale Kasada headers; session cookies via CDP cover auth
  - Requires Tabby with an active wizz-air-search session

Version note: the API version "28.10.1" is embedded in the URL path and changes
with Wizzair deployments. If requests start returning 404, check /buildnumber:
  fetch('/buildnumber').then(r => r.text())
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.cdp import cdp_eval, cdp_fetch, find_page  # noqa: E402

BASE_URL = "https://be.wizzair.com"
CDP_HOST_MATCH = "wizzair.com"
API_VERSION = "28.10.1"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)


async def execute(
    origin: str = "BUD",
    destination: str = "LON",
    date: str = "2026-07-01",
    days: int = 60,
    is_return: bool = False,
) -> dict[str, Any]:
    """Search Wizz Air flight availability for a route over a date range.

    Args:
        origin: Departure IATA airport or city code (e.g. "BUD", "WMI").
        destination: Arrival IATA airport or city code (e.g. "LON", "LTN").
        date: Start of the search window in YYYY-MM-DD format.
        days: Number of days to search ahead from date (default 60).
        is_return: Whether to search for return flights.
    """
    url = f"{BASE_URL}/{API_VERSION}/Api/search/FlightDatesMultiArrival"

    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile wizz-air-search"
        )

    token: str = await cdp_eval(
        ws_url,
        (
            "JSON.stringify(document.cookie.split(';')"
            ".map(c => c.trim().split('='))"
            ".reduce((acc, [k, ...v]) => { acc[k] = v.join('='); return acc; }, {})"
            "['RequestVerificationToken'] || '')"
        ),
    )

    # Parse start date and build ISO timestamp range
    start_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    end_dt = start_dt + timedelta(days=days)
    from_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    body = {
        "departureStation": origin,
        "arrivalStations": [destination],
        "from": from_iso,
        "to": to_iso,
        "isReturn": is_return,
    }

    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "X-RequestVerificationToken": token,
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "Origin": "https://www.wizzair.com",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.wizzair.com/en-gb",
        "Accept-Language": "en-US,en;q=0.9",
    }

    return await cdp_fetch(ws_url, url, method="POST", body=body, headers=headers)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_api_search_flightdatesmultiarrival",
        description="Search Wizz Air flight availability for a route over a date range.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "BUD".')
    parser.add_argument(
        "--destination", required=True, help='Arrival IATA or city code, e.g. "LON", "LTN".'
    )
    parser.add_argument(
        "--date", required=True, help="Start of search window in YYYY-MM-DD format."
    )
    parser.add_argument(
        "--days", type=int, default=60, help="Days to search ahead from --date (default 60)."
    )
    parser.add_argument(
        "--return", dest="is_return", action="store_true", help="Search for return flights."
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
                days=args.days,
                is_return=args.is_return,
            )
        )
    except Exception as exc:
        print(f"create_api_search_flightdatesmultiarrival failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
