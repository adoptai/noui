#!/usr/bin/env python3
"""Search Emirates for available flights on a given route and date.

Emirates uses an SSR Next.js SRP with Akamai bot protection. Flow via Tabby:
  1. execute_fetch: POST the search form to get the ttid (search-session token).
  2. execute_browser HAR capture: navigate to the results URL with the ttid;
     the page fires brandedFares API calls which appear in the HAR.
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

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.execute import execute_browser, execute_fetch  # noqa: E402

_PROFILE_ID = "emirates"
_SEARCH_POST_URL = (
    "https://www.emirates.com/booking/search-results/?pageurl=/IBE&pub=/us/english&j=f&section=IBE"
)
_RESULTS_BASE_URL = "https://www.emirates.com/booking/search-results/?pub=%2Fus%2Fenglish"

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_CABIN_CODES = {"ECONOMY": "0", "BUSINESS": "1", "FIRST": "2"}


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


async def execute(
    origin: str = "DXB",
    destination: str = "LHR",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search Emirates for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "DXB", "LHR", "JFK").
        destination: Arrival IATA airport code (e.g. "LHR", "DXB", "SYD").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    profile_id = profile_slug or _PROFILE_ID

    form_body = _build_form(origin, destination, date, adults, cabin_class)

    # Step 1: POST form to get the ttid (search session token)
    raw_html = await execute_fetch(
        profile_id,
        _SEARCH_POST_URL,
        method="POST",
        body=form_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout_ms=30_000,
    )
    # execute_fetch returns parsed JSON but this endpoint returns HTML
    # Extract ttid from whatever we got back
    html_str = raw_html if isinstance(raw_html, str) else json.dumps(raw_html)
    import re
    m = re.search(r'"ttid":"([^"]+)"', html_str)
    if not m:
        raise RuntimeError(
            "Emirates search POST did not return a ttid. Bot protection may have triggered."
        )
    ttid = m.group(1)

    # Step 2: Navigate to results URL, capture brandedFares API calls via HAR
    results_url = f"{_RESULTS_BASE_URL}&refreshId={uuid.uuid4().hex[:8]}&ttid={ttid}"

    await execute_browser(profile_id, "har_start")
    await execute_browser(profile_id, "navigate", {"url": results_url}, timeout_ms=45_000)
    try:
        await execute_browser(
            profile_id,
            "wait_for_selector",
            {"selector": "[class*='flight-card'], [class*='results-list'], [class*='branded-fares']"},
            timeout_ms=20_000,
        )
    except Exception:
        pass

    har_data = await execute_browser(profile_id, "har_stop")

    entries = (har_data or {}).get("har", {}).get("log", {}).get("entries", [])
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if "brandedFares" in url or "branded-fares" in url or "flight-search" in url:
            response = entry.get("response", {})
            content = response.get("content", {})
            text = content.get("text", "")
            if text:
                try:
                    return {"source": "har", "ttid": ttid, "url": url, "data": json.loads(text)}
                except (ValueError, TypeError):
                    continue

    summary = await execute_browser(profile_id, "get_page_summary")
    return {
        "origin": origin,
        "destination": destination,
        "date": date,
        "cabinClass": cabin_class,
        "ttid": ttid,
        "pageSummary": summary,
    }


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
