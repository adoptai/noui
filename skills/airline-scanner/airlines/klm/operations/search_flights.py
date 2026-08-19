#!/usr/bin/env python3
"""Search KLM Royal Dutch Airlines for available one-way flights on a given route and date.

KLM uses the AFKL GraphQL platform (SearchResultAvailableOffersQuery) with
Akamai bot protection. This skill uses Tabby execute_browser HAR capture:
navigate to the KLM search results URL — the page fires the GQL query internally
and the response is captured in the HAR.
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

_PROFILE_ID = "klm"
_RESULTS_BASE = "https://www.klm.com/search/offers"


async def execute(
    origin: str = "AMS",
    destination: str = "JFK",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search KLM for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA code (e.g. "AMS", "CDG").
        destination: Arrival IATA code (e.g. "JFK", "LHR").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    profile_id = profile_slug or _PROFILE_ID

    # Build search results URL with parameters
    params = urllib.parse.urlencode(
        {
            "origin": origin,
            "destination": destination,
            "outboundDate": date,
            "cabinClass": cabin_class.upper(),
            "adults": adults,
            "tripType": "ONE_WAY",
        }
    )
    search_url = f"{_RESULTS_BASE}?{params}"

    await execute_browser(profile_id, "har_start")
    await execute_browser(profile_id, "navigate", {"url": search_url}, timeout_ms=60_000)
    try:
        await execute_browser(
            profile_id,
            "wait_for_selector",
            {"selector": "[class*='flight-card'], [class*='offer-card'], [data-testid*='flight']"},
            timeout_ms=25_000,
        )
    except Exception:
        pass

    har_data = await execute_browser(profile_id, "har_stop")

    entries = (har_data or {}).get("har", {}).get("log", {}).get("entries", [])
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if "SearchResultAvailableOffersQuery" in url or (
            "gql" in url and entry.get("request", {}).get("method") == "POST"
        ):
            response = entry.get("response", {})
            content = response.get("content", {})
            text = content.get("text", "")
            if text:
                try:
                    return {"source": "har", "url": url, "data": json.loads(text)}
                except (ValueError, TypeError):
                    continue

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
        description="Search KLM for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='IATA code, e.g. "AMS".')
    parser.add_argument("--destination", required=True, help='IATA code, e.g. "JFK".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument(
        "--cabin-class", default="ECONOMY", choices=["ECONOMY", "BUSINESS", "FIRST"]
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
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
