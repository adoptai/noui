#!/usr/bin/env python3
"""Search American Airlines for available one-way flights on a given route and date.

AA's booking search is protected by PerimeterX + Akamai. The search URL includes
all parameters as query-string arguments — no form fill needed. This skill uses
Tabby's execute_browser to navigate to the search results page and capture
underlying API calls via HAR.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.execute import execute_browser  # noqa: E402

_PROFILE_ID = "american-airlines"
_SEARCH_BASE = "https://www.aa.com/booking/search"
_CABIN_MAP = {"ECONOMY": "", "BUSINESS": "Business", "FIRST": "First"}


async def execute(
    origin: str = "JFK",
    destination: str = "LAX",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search American Airlines for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "JFK", "LAX", "ORD").
        destination: Arrival IATA airport code (e.g. "LAX", "JFK", "MIA").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    profile_id = profile_slug or _PROFILE_ID

    cabin = _CABIN_MAP.get(cabin_class.upper(), "")
    slices = json.dumps(
        [
            {
                "orig": origin,
                "origNearby": False,
                "dest": destination,
                "destNearby": False,
                "date": date,
            }
        ]
    )
    params = urllib.parse.urlencode(
        {
            "locale": "en_US",
            "fareType": "Lowest",
            "pax": adults,
            "adult": adults,
            "type": "OneWay",
            "searchType": "Revenue",
            "cabin": cabin,
            "carriers": "ALL",
            "travelType": "personal",
            "slices": slices,
        }
    )
    search_url = f"{_SEARCH_BASE}?{params}"

    await execute_browser(profile_id, "har_start")
    await execute_browser(profile_id, "navigate", {"url": search_url}, timeout_ms=45_000)
    # Wait for the flight results container to appear
    try:
        await execute_browser(
            profile_id,
            "wait_for_selector",
            {"selector": "[class*='flight-listing'], [class*='choose-flights'], [class*='results']"},
            timeout_ms=20_000,
        )
    except Exception:
        pass  # Continue even if selector not found — results may still be in HAR

    har_data = await execute_browser(profile_id, "har_stop")

    entries = (har_data or {}).get("har", {}).get("log", {}).get("entries", [])
    # Look for the internal AA fare-search API response
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if any(k in url for k in ("flight-search", "availability", "fare-search", "booking/search")):
            response = entry.get("response", {})
            content = response.get("content", {})
            text = content.get("text", "")
            if text:
                try:
                    return {"source": "har", "url": url, "data": json.loads(text)}
                except (ValueError, TypeError):
                    continue

    # Fall back to page summary if no API call captured
    summary = await execute_browser(profile_id, "get_page_summary")
    return {
        "origin": origin,
        "destination": destination,
        "date": date,
        "cabinClass": cabin_class,
        "pageSummary": summary,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search American Airlines for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "JFK".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "LAX".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument(
        "--cabin-class",
        default="ECONOMY",
        choices=["ECONOMY", "BUSINESS", "FIRST"],
        help="Cabin class (default: ECONOMY).",
    )
    parser.add_argument("--adults", type=int, default=1)
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
    if isinstance(result, dict) and result.get("error"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
