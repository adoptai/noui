#!/usr/bin/env python3
"""Search KLM Royal Dutch Airlines for available one-way flights on a given route and date.

KLM uses the same AFKL GraphQL platform as Air France, but with:
  - AFKL-TRAVEL-Host: KL (not AF)
  - Same SearchResultAvailableOffersQuery endpoint at www.klm.com/gql/v1
  - Akamai blocks direct calls — requires Tabby browser session

The skill navigates the Tabby browser to the KLM homepage, fills the search form
(origin, destination, departure date, one-way), clicks "View offers", then
intercepts the SearchResultAvailableOffersQuery GQL response via CDP Fetch.
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

CDP_HOST_MATCH = "klm.com"
_HOME_URL = "https://www.klm.com/en-us/flights"
_GQL_PATTERN = "*/gql/v1*SearchResultAvailableOffersQuery*"


async def _fill_and_submit(ws_url: str, origin: str, destination: str, date: str) -> None:
    """Navigate to KLM homepage, fill the search form, and click search."""
    import websockets as _ws

    async with _ws.connect(ws_url, max_size=None) as ws:
        await ws.send(
            json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": _HOME_URL}})
        )
        deadline = asyncio.get_event_loop().time() + 20
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if msg.get("method") == "Page.loadEventFired":
                    break
            except TimeoutError:
                break

    await asyncio.sleep(5)
    ws_url2 = await find_page(CDP_HOST_MATCH) or ws_url

    # Switch to one-way
    await cdp_eval(
        ws_url2,
        """(function() {
        var btn = Array.from(document.querySelectorAll('button')).find(b=>(b.innerText||'').toLowerCase().includes('round'));
        if (btn) btn.click();
        return null;
    })()""",
    )
    await asyncio.sleep(0.8)
    await cdp_eval(
        ws_url2,
        """(function() {
        var opt = Array.from(document.querySelectorAll('li,[role=option],button')).find(b=>(b.innerText||'').toLowerCase().includes('one way')||(b.innerText||'').toLowerCase().includes('one-way'));
        if (opt) opt.click();
        return null;
    })()""",
    )
    await asyncio.sleep(0.5)

    # Set origin via native setter + autocomplete
    await cdp_eval(
        ws_url2,
        f"""(function() {{
        var el = document.getElementById('flights-booking-id-1-input');
        if (!el) return null;
        el.scrollIntoView();
        var s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        s.call(el, {json.dumps(origin)});
        el.dispatchEvent(new Event('focus', {{bubbles:true}}));
        el.dispatchEvent(new Event('input', {{bubbles:true}}));
        return null;
    }})()""",
    )
    await asyncio.sleep(2)
    await cdp_eval(
        ws_url2,
        """(function() {
        var opts = Array.from(document.querySelectorAll('[role=option]'));
        if (opts.length) opts[0].click();
        return null;
    })()""",
    )
    await asyncio.sleep(0.8)

    # Set destination
    await cdp_eval(
        ws_url2,
        f"""(function() {{
        var el = document.getElementById('flights-booking-id-2-input');
        if (!el) return null;
        var s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        s.call(el, {json.dumps(destination)});
        el.dispatchEvent(new Event('focus', {{bubbles:true}}));
        el.dispatchEvent(new Event('input', {{bubbles:true}}));
        return null;
    }})()""",
    )
    await asyncio.sleep(2)
    await cdp_eval(
        ws_url2,
        """(function() {
        var opts = Array.from(document.querySelectorAll('[role=option]'));
        if (opts.length) opts[0].click();
        return null;
    })()""",
    )
    await asyncio.sleep(0.8)

    # Open date picker and navigate to correct month
    await cdp_eval(
        ws_url2,
        r"""(function() {
        var btn = Array.from(document.querySelectorAll('button')).find(b=>/\d{2}\/\d{2}\/\d{4}/.test(b.innerText));
        if (btn) btn.click();
        return null;
    })()""",
    )
    await asyncio.sleep(0.5)

    # Parse target month and navigate — read actual calendar month from the DOM
    from datetime import datetime

    target = datetime.strptime(date, "%Y-%m-%d")
    # Read the current calendar month header (e.g. "June 2026") from the DOM
    cal_header = await cdp_eval(
        ws_url2,
        'JSON.stringify(document.querySelector(\'[class*="month-header"] span,[class*="calendar"] h2,[class*="calendar"] strong\')?.innerText || \'\')',
    )
    try:
        cal_start = datetime.strptime(cal_header.strip(), "%B %Y")
    except ValueError:
        # Fallback: assume calendar starts 1 month after today
        cal_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if cal_start.month == 12:
            cal_start = cal_start.replace(year=cal_start.year + 1, month=1)
        else:
            cal_start = cal_start.replace(month=cal_start.month + 1)
    months_forward = (target.year - cal_start.year) * 12 + target.month - cal_start.month
    for _ in range(months_forward):
        await cdp_eval(
            ws_url2,
            """(function() {
            var btn = Array.from(document.querySelectorAll('button')).find(b=>b.getAttribute('aria-label')&&b.getAttribute('aria-label').includes('next-month'));
            if (btn) btn.click();
            return null;
        })()""",
        )
        await asyncio.sleep(0.4)

    # Click the target day
    day_str = str(target.day)
    await cdp_eval(
        ws_url2,
        f"""(function() {{
        var cells = Array.from(document.querySelectorAll('[role=gridcell]')).filter(el=>el.innerText.trim()==={json.dumps(day_str)});
        if (cells.length) cells[0].click();
        return null;
    }})()""",
    )
    await asyncio.sleep(0.8)

    # Click search/view offers
    await cdp_eval(
        ws_url2,
        """(function() {
        var btn = Array.from(document.querySelectorAll('button')).find(b=>{
            var t = (b.innerText||'').toLowerCase().trim();
            return t.includes('offer') || t.includes('search');
        });
        if (btn) { btn.scrollIntoView({block:'center'}); btn.click(); }
        return null;
    })()""",
    )


async def execute(
    origin: str = "AMS",
    destination: str = "JFK",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
) -> dict[str, Any]:
    """Search KLM for available one-way flights on a given route and date.

    Args:
        origin: Departure city name or IATA code (e.g. "Amsterdam" or "AMS").
        destination: Arrival city name or IATA code (e.g. "New York" or "JFK").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile klm-search"
        )

    flight_data: dict[str, Any] = {}

    # Fill the form and click search, while intercepting the GQL response
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Fetch.enable",
                    "params": {
                        "patterns": [{"urlPattern": _GQL_PATTERN, "requestStage": "Response"}]
                    },
                }
            )
        )
        await asyncio.sleep(0.1)

        # Run form fill in background
        fill_task = asyncio.create_task(_fill_and_submit(ws_url, origin, destination, date))

        deadline = asyncio.get_event_loop().time() + 90
        intercepted = False
        while asyncio.get_event_loop().time() < deadline and not intercepted:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if msg.get("method") == "Fetch.requestPaused":
                    rid = msg["params"]["requestId"]
                    await ws.send(
                        json.dumps(
                            {
                                "id": 10,
                                "method": "Fetch.getResponseBody",
                                "params": {"requestId": rid},
                            }
                        )
                    )
                    for _ in range(15):
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if m.get("id") == 10:
                            import base64

                            body = m["result"].get("body", "")
                            if m["result"].get("base64Encoded"):
                                body = base64.b64decode(body).decode("utf-8", "replace")
                            if body:
                                flight_data = json.loads(body)
                                intercepted = True
                            break
                    await ws.send(
                        json.dumps(
                            {
                                "id": 11,
                                "method": "Fetch.continueRequest",
                                "params": {"requestId": rid},
                            }
                        )
                    )
            except TimeoutError:
                continue

        await ws.send(json.dumps({"id": 2, "method": "Fetch.disable"}))
        fill_task.cancel()

    if not flight_data:
        raise RuntimeError("KLM GQL response not captured — search may have failed or timed out.")

    return flight_data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search KLM for available flights on a given route and date.",
    )
    parser.add_argument(
        "--origin", required=True, help='City name or IATA code, e.g. "Amsterdam" or "AMS".'
    )
    parser.add_argument(
        "--destination", required=True, help='City name or IATA code, e.g. "New York" or "JFK".'
    )
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument(
        "--cabin-class", default="ECONOMY", choices=["ECONOMY", "BUSINESS", "FIRST"]
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
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
