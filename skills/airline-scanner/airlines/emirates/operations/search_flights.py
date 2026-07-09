#!/usr/bin/env python3
"""Search Emirates for available flights on a given route and date.

Emirates uses an SSR Next.js SRP (Search Results Page) with Akamai bot
protection and ESI fragments. Flight data is loaded into the browser's Redux
store (window.__NEXT_REDUX_STORE__) after the page hydrates.

Flow:
  1. POST the search form from inside the browser (gets session cookies set).
  2. Extract the ttid (search-session token) from the SSR HTML response.
  3. Navigate the browser to the search results URL with the ttid.
  4. Poll until api.brandedFares is populated in the Redux store.
  5. Return the full brandedFares payload (flights, fares, prices).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import websockets

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.cdp import cdp_eval, find_page  # noqa: E402

CDP_HOST_MATCH = "emirates.com"
_SEARCH_POST_URL = (
    "https://www.emirates.com/booking/search-results/?pageurl=/IBE&pub=/us/english&j=f&section=IBE"
)
_RESULTS_BASE_URL = "https://www.emirates.com/booking/search-results/?pub=%2Fus%2Fenglish"

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Cabin class codes: 0=Economy, 1=Business, 2=First
_CABIN_CODES = {"ECONOMY": "0", "BUSINESS": "1", "FIRST": "2"}
_CABIN_KEYS = {"ECONOMY": "Y", "BUSINESS": "J", "FIRST": "F"}


def _build_form(origin: str, destination: str, date: str, adults: int, cabin: str) -> str:
    d = datetime.strptime(date, "%Y-%m-%d")
    cabin_code = _CABIN_CODES.get(cabin.upper(), "0")
    return urlencode(
        {
            "TID": "OW",
            "chkFlexibleDates": "false",
            "depShortDate": d.strftime("%d%m%y"),
            "departDate": d.strftime("%d%m%Y"),
            "gacabinclass": cabin_code,
            "j": "t",
            "multiCity": "",
            "resultby": "0",
            "retShortDate": "",
            "selacity1": destination,
            "seladate1": "",
            "seladults": adults,
            "selcabinclass": cabin_code,
            "selcabinclass1": "",
            "selchildren": "0",
            "seldcity1": origin,
            "selddate1": f"{d.day:02d}-{_MONTHS[d.month - 1]}-{str(d.year)[2:]}",
            "selinfants": "0",
            "selofw": "0",
            "selteenager": "0",
            "showOFW": "false",
            "showTeenager": "false",
            "showsearch": "false",
        }
    )


async def _post_and_get_ttid(ws_url: str, form_body: str) -> str:
    """POST the search form inside the browser and extract the ttid from the SSR response."""
    js = (
        f"fetch({json.dumps(_SEARCH_POST_URL)}, {{"
        f"  method: 'POST',"
        f"  headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},"
        f"  body: {json.dumps(form_body)}"
        f"}}).then(function(r) {{ return r.text(); }})"
        f".then(function(html) {{"
        f'  var m = html.match(/"ttid":"([^"]+)"/);'
        f"  return JSON.stringify(m ? m[1] : null);"
        f"}})"
    )
    ttid = await cdp_eval(ws_url, js)
    if not ttid:
        raise RuntimeError(
            "Emirates search POST did not return a ttid. Bot protection may have triggered."
        )
    return ttid


async def _navigate_to_results(ws_url: str, ttid: str) -> None:
    """Navigate the browser to the search results page."""
    results_url = f"{_RESULTS_BASE_URL}&refreshId={uuid.uuid4().hex[:8]}&ttid={ttid}"
    async with websockets.connect(ws_url) as ws:
        await ws.send(
            json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": results_url}})
        )
        deadline = asyncio.get_event_loop().time() + 15
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if msg.get("method") == "Page.loadEventFired":
                    break
            except TimeoutError:
                break


async def _wait_for_branded_fares(ws_url: str, timeout: float = 30.0) -> dict[str, Any]:
    """Poll the Redux store until brandedFares is populated."""
    js = """(function() {
        var store = window.__NEXT_REDUX_STORE__;
        if (!store) return JSON.stringify({ready: false, error: 'no store'});
        var bf = store.getState().api.brandedFares;
        var key = '1-1';
        if (bf && bf.data && bf.data[key] && bf.data[key].bounds) {
            return JSON.stringify({ready: true, data: bf.data[key]});
        }
        var fetching = bf && bf.fetching && bf.fetching[key];
        return JSON.stringify({ready: false, fetching: !!fetching});
    })()"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await cdp_eval(ws_url, js)
        if result.get("ready"):
            return result["data"]
        await asyncio.sleep(2)
    raise RuntimeError(f"Emirates brandedFares did not populate within {timeout}s.")


async def execute(
    origin: str = "DXB",
    destination: str = "LHR",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
) -> dict[str, Any]:
    """Search Emirates for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "DXB", "LHR", "JFK").
        destination: Arrival IATA airport code (e.g. "LHR", "DXB", "SYD").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile emirates-search"
        )

    form_body = _build_form(origin, destination, date, adults, cabin_class)
    ttid = await _post_and_get_ttid(ws_url, form_body)
    await _navigate_to_results(ws_url, ttid)
    await asyncio.sleep(5)

    ws_url2 = await find_page(CDP_HOST_MATCH)
    return await _wait_for_branded_fares(ws_url2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search Emirates for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "DXB".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "LHR".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument(
        "--cabin-class",
        default="ECONOMY",
        choices=["ECONOMY", "BUSINESS", "FIRST"],
        help="Cabin class (default: ECONOMY).",
    )
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
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
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
