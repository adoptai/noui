#!/usr/bin/env python3
"""Search United Airlines for available one-way flights on a given route and date.

United uses SSE (Server-Sent Events) for flight data — the search URL includes
all parameters as query-string arguments. This skill uses Tabby's execute_browser
to navigate to the choose-flights URL and capture the SSE data via HAR.
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

from noui_runtime.execute import execute_browser  # noqa: E402

_PROFILE_ID = "united"
_SEARCH_BASE = "https://www.united.com/en/us/fsr/choose-flights"
_CABIN_MAP = {"ECONOMY": "ECONOMY", "BUSINESS": "BUSINESS", "FIRST": "FIRST"}


async def execute(
    origin: str = "ORD",
    destination: str = "SFO",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search United Airlines for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "ORD", "SFO", "IAH").
        destination: Arrival IATA airport code (e.g. "SFO", "ORD", "LAX").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    profile_id = profile_slug or _PROFILE_ID
    cabin = _CABIN_MAP.get(cabin_class.upper(), "ECONOMY")
    search_url = (
        f"{_SEARCH_BASE}?f={origin}&t={destination}&d={date}"
        f"&tt=1&sc=7&px={adults}&taxng=1&newHP=True&clm=7&st=best&fareFamily={cabin}"
    )

    await execute_browser(profile_id, "har_start")
    await execute_browser(profile_id, "navigate", {"url": search_url}, timeout_ms=45_000)
    # Wait for flight results container
    try:
        await execute_browser(
            profile_id,
            "wait_for_selector",
            {"selector": "[class*='app-components-Shopping'], [data-cy='flightCard'], [class*='flight-card']"},
            timeout_ms=20_000,
        )
    except Exception:
        pass

    har_data = await execute_browser(profile_id, "har_stop")

    entries = (har_data or {}).get("har", {}).get("log", {}).get("entries", [])
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if any(k in url for k in ("availability", "flight-search", "offers", "fsr/choose")):
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
        description="Search United Airlines for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "ORD".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "SFO".')
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
