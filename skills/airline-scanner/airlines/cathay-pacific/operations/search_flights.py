#!/usr/bin/env python3
"""Search Cathay Pacific for available flights on a given route and date.

Cathay Pacific uses Akamai Bot Manager. The booking form POST triggers an SSR
redirect to book.cathaypacific.com/CathayPacificV3/dyn/air/booking/owdAvail.
This skill uses execute_fetch to submit the form from inside Tabby's browser
session, then uses execute_browser HAR capture to navigate to the results URL
and collect flight data from the Angular page's internal API calls.
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

from noui_runtime.execute import execute_browser, execute_fetch  # noqa: E402

_PROFILE_ID = "cathay-pacific"
_HOMEPAGE = "https://www.cathaypacific.com/cx/en_US.html"
_FORM_ACTION = "https://www.cathaypacific.com/cx/en_US.html"

_CABIN_CODES = {
    "ECONOMY": "Y",
    "PREMIUM ECONOMY": "W",
    "BUSINESS": "J",
    "FIRST": "F",
}


async def execute(
    origin: str = "HKG",
    destination: str = "LHR",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search Cathay Pacific for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "HKG", "LHR", "JFK").
        destination: Arrival IATA airport code (e.g. "LHR", "HKG", "SYD").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, PREMIUM ECONOMY, BUSINESS, or FIRST.
    """
    profile_id = profile_slug or _PROFILE_ID
    cabin_code = _CABIN_CODES.get(cabin_class.upper(), "Y")
    date_compact = date.replace("-", "")

    # Build the form body that the CX booking widget POSTs to IBEFacade
    form_body = "&".join(
        [
            f"ORIGIN={origin}",
            f"DESTINATION={destination}",
            f"DEPARTUREDATE={date_compact}",
            "TRIPTYPE=O",
            f"CABINCLASS={cabin_code}",
            "ADULT=1",
            "YOUNGADULT=0",
            "CHILD=0",
        ]
    )

    # Navigate to homepage first to establish session cookies, then capture form results
    await execute_browser(profile_id, "har_start")
    await execute_browser(profile_id, "navigate", {"url": _HOMEPAGE}, timeout_ms=30_000)

    # Submit form via execute_fetch (browser cookies satisfy Akamai)
    try:
        await execute_fetch(
            profile_id,
            _FORM_ACTION,
            method="POST",
            body=form_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout_ms=30_000,
        )
    except Exception:
        pass  # Form submit may trigger redirect; execute_fetch may error on non-JSON response

    har_data = await execute_browser(profile_id, "har_stop")

    entries = (har_data or {}).get("har", {}).get("log", {}).get("entries", [])
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if any(k in url for k in ("owdAvail", "availableFlight", "flight-search", "IBEFacade")):
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
        description="Search Cathay Pacific for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "HKG".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "LHR".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
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
