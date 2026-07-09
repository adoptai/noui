#!/usr/bin/env python3
"""Search Cathay Pacific for available flights on a given route and date.

Cathay Pacific uses Akamai Bot Manager. This skill submits the booking form
on the CX homepage with the desired search parameters (all hidden fields —
no Vue/Angular interaction needed). The server processes the POST and
redirects to book.cathaypacific.com/CathayPacificV3/dyn/air/booking/owdAvail,
which renders flight options in the DOM. The skill waits for Angular to finish
rendering, then parses the page text for departure/arrival times, durations,
flight numbers, and fares.
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

CDP_HOST_MATCH = "cathaypacific.com"
_HOMEPAGE = "https://www.cathaypacific.com/cx/en_US.html"
_FORM_ID = "book-trip-flight"

_CABIN_CODES = {
    "ECONOMY": "Y",
    "PREMIUM ECONOMY": "W",
    "BUSINESS": "J",
    "FIRST": "F",
}

_PARSE_JS = r"""(function() {
    var text = document.body.innerText;
    var countMatch = text.match(/(\d+) flights? found/);
    var flightCount = countMatch ? parseInt(countMatch[1]) : 0;

    var datePrices = [];
    var dateRe = /(\w{3} \d+ \w{3})\s+From HKD\s*\$?\s*([\d,]+)/g;
    var dm;
    while ((dm = dateRe.exec(text)) !== null) {
        datePrices.push({date: dm[1], priceHKD: parseInt(dm[2].replace(/,/g,''))});
    }

    var flights = [];
    var fRe = /depart on\s+(\d{2}:\d{2})\s+from\s+(\w{3})\s+Duration\s+([\dhm ]+?)\s+(?:Connect at[\s\S]*?)?Arrives on\s+(\d{2}:\d{2})(?:\s+\+\d)?\s+at\s+(\w{3})\s+Flight\s+([\w ]+?)(?:\s+to\s+Flight\s+([\w ]+?))?\s+View/g;
    var fm;
    while ((fm = fRe.exec(text)) !== null) {
        flights.push({
            departure: fm[1], origin: fm[2],
            duration: fm[3].trim(), arrival: fm[4], destination: fm[5],
            flightNumber: fm[6].trim(),
            connectingFlight: fm[7] ? fm[7].trim() : null
        });
    }

    return JSON.stringify({flightCount: flightCount, datePrices: datePrices, flights: flights});
})()"""


async def _navigate_to_homepage(ws_url: str) -> None:
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(
            json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": _HOMEPAGE}})
        )
        deadline = asyncio.get_event_loop().time() + 20
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if msg.get("method") == "Page.loadEventFired":
                    break
            except TimeoutError:
                break


async def _submit_search(
    ws_url: str, origin: str, destination: str, date: str, cabin_code: str
) -> None:
    """Fill hidden form fields and submit to IBEFacade."""
    date_compact = date.replace("-", "")
    set_js = f"""(function() {{
        var form = document.getElementById({json.dumps(_FORM_ID)});
        if (!form) return 'no form';
        function set(name, value) {{
            var el = form.querySelector('[name="' + name + '"]');
            if (el) el.value = value;
        }}
        set('ORIGIN', {json.dumps(origin)});
        set('DESTINATION', {json.dumps(destination)});
        set('DEPARTUREDATE', {json.dumps(date_compact)});
        set('TRIPTYPE', 'O');
        set('CABINCLASS', {json.dumps(cabin_code)});
        set('ADULT', '1');
        set('YOUNGADULT', '0');
        set('CHILD', '0');
        form.submit();
        return 'submitted';
    }})()"""
    try:
        await cdp_eval(ws_url, set_js)
    except (RuntimeError, Exception):
        # form.submit() navigates the page — execution context destruction is expected
        pass


async def _wait_for_owdavail(ws_url: str, timeout: float = 30.0) -> str:
    """Poll until the browser lands on the owdAvail flight results page."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        ws_url2 = await find_page(CDP_HOST_MATCH)
        if ws_url2:
            current = await cdp_eval(ws_url2, "JSON.stringify(window.location.href)")
            if "owdAvail" in current or "Choose flights" in current:
                return ws_url2
        await asyncio.sleep(2)
    raise RuntimeError("Timed out waiting for owdAvail flight results page.")


async def execute(
    origin: str = "HKG",
    destination: str = "LHR",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
) -> dict[str, Any]:
    """Search Cathay Pacific for available one-way flights on a given route and date.

    Returns all available flights with departure/arrival times, flight numbers,
    durations, and a 7-day fare calendar in HKD. Uses IATA airport codes.

    Args:
        origin: Departure IATA airport code (e.g. "HKG", "LHR", "JFK").
        destination: Arrival IATA airport code (e.g. "LHR", "HKG", "SYD").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, PREMIUM ECONOMY, BUSINESS, or FIRST.
    """
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile cathay-pacific-search"
        )

    cabin_code = _CABIN_CODES.get(cabin_class.upper(), "Y")

    await _navigate_to_homepage(ws_url)
    await asyncio.sleep(4)

    ws_url2 = await find_page(CDP_HOST_MATCH) or ws_url
    await _submit_search(ws_url2, origin, destination, date, cabin_code)

    ws_url3 = await _wait_for_owdavail(ws_url2, timeout=30)
    await asyncio.sleep(5)  # Wait for Angular to render flights

    ws_url4 = await find_page(CDP_HOST_MATCH) or ws_url3
    result = await cdp_eval(ws_url4, _PARSE_JS)

    return {
        "origin": origin,
        "destination": destination,
        "date": date,
        "cabinClass": cabin_class,
        "flightCount": result.get("flightCount", 0),
        "flights": result.get("flights", []),
        "datePrices": result.get("datePrices", []),
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
