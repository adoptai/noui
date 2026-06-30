#!/usr/bin/env python3
"""Search United Airlines for available one-way flights on a given route and date.

United uses SSE (Server-Sent Events) for flight data with a session-specific
X-Authorization-api token — too complex to call directly. Instead, this skill
navigates the Tabby browser to the choose-flights URL, waits for the SSE data
to render in the DOM, then parses the page text for flight details.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import websockets

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.cdp import cdp_eval, find_page  # noqa: E402

CDP_HOST_MATCH = "united.com"
_SEARCH_BASE = "https://www.united.com/en/us/fsr/choose-flights"
_CABIN_MAP = {"ECONOMY": "ECONOMY", "BUSINESS": "BUSINESS", "FIRST": "FIRST"}

_PARSE_JS = r"""(function() {
    var text = document.body.innerText;
    var url = window.location.href;

    var flights = [];
    // Pattern: NONSTOP/XSTOP  H:MM AM/PM  H:MM AM/PM  ORG  Dur  DEST  UA NNN (Aircraft)
    var fRe = /(NONSTOP|\d+ STOP[S]?)\s+(\d{1,2}:\d{2} [AP]M)\s+Departing[^\n]+\s+(\d{1,2}:\d{2} [AP]M)\s+Arriving[^\n]+\s+(\w{3})\s+Origin[^\n]+\s+(\d+H, \d+M)\s+Duration[^\n]+\s+(\w{3})\s+Destination[^\n]+\s+(UA \d+)[^\n]+\n+(?:Flight Number [^\n]+\n+)?([^\n]+(?:Boeing|Airbus|Embraer)[^\n]+)/g;
    var fm;
    while ((fm = fRe.exec(text)) !== null) {
        flights.push({
            stops: fm[1], departure: fm[2], arrival: fm[3],
            origin: fm[4], duration: fm[5], destination: fm[6],
            flightNumber: fm[7], aircraft: fm[8].replace(/^Flight Number [^\n]+\n/, '').trim()
        });
    }

    // Price info per flight (capture From $N pattern)
    var priceRe = /From\s*\$([\d,]+)\s*United Economy/g;
    var prices = [];
    var pm;
    while ((pm = priceRe.exec(text)) !== null) {
        prices.push(parseInt(pm[1].replace(/,/g,'')));
    }

    // Count
    var countMatch = text.match(/(\d+) result[s]? found/) || text.match(/Showing (\d+)/);
    var flightCount = countMatch ? parseInt(countMatch[1]) : flights.length;

    return JSON.stringify({url: url, flightCount: flightCount, flights: flights, lowestPrices: prices.slice(0, flights.length)});
})()"""


async def execute(
    origin: str = "ORD",
    destination: str = "SFO",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
) -> dict[str, Any]:
    """Search United Airlines for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "ORD", "SFO", "IAH").
        destination: Arrival IATA airport code (e.g. "SFO", "ORD", "LAX").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile united-search"
        )

    cabin = _CABIN_MAP.get(cabin_class.upper(), "ECONOMY")
    search_url = (
        f"{_SEARCH_BASE}?f={origin}&t={destination}&d={date}"
        f"&tt=1&sc=7&px={adults}&taxng=1&newHP=True&clm=7&st=best&fareFamily={cabin}"
    )

    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(
            json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": search_url}})
        )
        deadline = asyncio.get_event_loop().time() + 25
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if msg.get("method") == "Page.loadEventFired":
                    break
            except TimeoutError:
                break

    await asyncio.sleep(12)  # Wait for SSE data to load and render

    ws_url2 = await find_page(CDP_HOST_MATCH)
    result = await cdp_eval(ws_url2 or ws_url, _PARSE_JS)

    return {
        "origin": origin,
        "destination": destination,
        "date": date,
        "cabinClass": cabin_class,
        "flightCount": result.get("flightCount", 0),
        "flights": result.get("flights", []),
        "lowestPricesUSD": result.get("lowestPrices", []),
        "pageUrl": result.get("url", ""),
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
