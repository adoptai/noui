#!/usr/bin/env python3
"""Search American Airlines for available one-way flights on a given route and date.

AA's booking search is protected by PerimeterX + Akamai — direct calls return 403.
The approach: navigate Tabby's browser to the search URL, which redirects to the
React choose-flights page. The React SPA renders all flights in the DOM. After
React hydrates, we parse the DOM text for departure times, flight numbers,
stops, and fare prices.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import websockets

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.cdp import cdp_eval, find_page  # noqa: E402

CDP_HOST_MATCH = "aa.com"
_SEARCH_BASE = "https://www.aa.com/booking/search"
_CABIN_MAP = {"ECONOMY": "", "BUSINESS": "Business", "FIRST": "First"}

_PARSE_JS = r"""(function() {
    var text = document.body.innerText;
    var url = window.location.href;

    // Date carousel (prices per day)
    var datePrices = [];
    var dateRe = /carousel flight \d+ of \d+,\s*(\w{3}, \w{3} \d+)\s*\$([\d,]+)/g;
    var dm;
    while ((dm = dateRe.exec(text)) !== null) {
        datePrices.push({date: dm[1], priceUSD: parseInt(dm[2].replace(/,/g,''))});
    }

    // Individual flights
    var flights = [];
    var fRe = /(\w{3})\s+(\d{1,2}:\d{2} [AP]M)\s+(\w{3})\s+(\d{1,2}:\d{2} [AP]M)\s+([\dhm ]+)\s+(Nonstop|\d+ stop[s]?)[\s\S]*?AA (\d+)\s+([\w-]+[\w]+)/g;
    var fm;
    while ((fm = fRe.exec(text)) !== null) {
        flights.push({
            origin: fm[1], departure: fm[2], destination: fm[3], arrival: fm[4],
            duration: fm[5].trim(), stops: fm[6], flightNumber: "AA " + fm[7], aircraft: fm[8]
        });
    }

    // Count and summary
    var countMatch = text.match(/(\d+) results/);
    var flightCount = countMatch ? parseInt(countMatch[1]) : flights.length;

    return JSON.stringify({url: url, flightCount: flightCount, datePrices: datePrices, flights: flights});
})()"""


async def execute(
    origin: str = "JFK",
    destination: str = "LAX",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
) -> dict[str, Any]:
    """Search American Airlines for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "JFK", "LAX", "ORD").
        destination: Arrival IATA airport code (e.g. "LAX", "JFK", "MIA").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile american-airlines-search"
        )

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

    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(
            json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": search_url}})
        )
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if msg.get("method") == "Page.loadEventFired":
                    break
            except TimeoutError:
                break

    await asyncio.sleep(8)  # Wait for React to hydrate and render flights

    ws_url2 = await find_page(CDP_HOST_MATCH)
    result = await cdp_eval(ws_url2 or ws_url, _PARSE_JS)

    return {
        "origin": origin,
        "destination": destination,
        "date": date,
        "cabinClass": cabin_class,
        "flightCount": result.get("flightCount", 0),
        "flights": result.get("flights", []),
        "datePrices": result.get("datePrices", []),
        "pageUrl": result.get("url", ""),
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
