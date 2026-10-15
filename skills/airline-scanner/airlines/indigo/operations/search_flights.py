#!/usr/bin/env python3
"""Search IndiGo (6E) for available flights on a given route and date.

IndiGo uses Akamai bot protection. Flow via Tabby execute API:
  1. execute_browser("har_start") — begin HAR capture.
  2. execute_browser("navigate", {"url": homepage}) — Angular app fires PUT
     /v1/token/refresh on load; HAR captures that request's Authorization header.
  3. execute_browser("har_stop") — parse HAR for the JWT.
  4. execute_fetch with the captured JWT for the actual flight search.
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

_PROFILE_ID = "indigo"
_SEARCH_URL = "https://api-prod-flight-skyplus6e.goindigo.in/v2/flight/search"
_HOME_URL = "https://www.goindigo.in/"
_USER_KEY = "31e90be8fff2f5e2eea242c225f21b1a"


async def _get_jwt(profile_id: str) -> str:
    """Navigate the IndiGo homepage and extract the DotRez JWT from the HAR."""
    await execute_browser(profile_id, "har_start")
    await execute_browser(profile_id, "navigate", {"url": _HOME_URL}, timeout_ms=30_000)
    har_data = await execute_browser(profile_id, "har_stop")

    entries = (har_data or {}).get("har", {}).get("log", {}).get("entries", [])
    for entry in entries:
        request = entry.get("request", {})
        if "token/refresh" in request.get("url", ""):
            for header in request.get("headers", []):
                if header.get("name", "").lower() == "authorization":
                    value = header.get("value", "")
                    if value.startswith("eyJ"):
                        return value
    raise RuntimeError(
        "Could not capture IndiGo JWT from HAR — token/refresh request not found. "
        "Ensure the Tabby session for 'indigo' is healthy and goindigo.in is accessible."
    )


async def execute(
    origin: str = "DEL",
    destination: str = "BOM",
    date: str = "2026-08-20",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    currency: str = "INR",
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search IndiGo for available flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "DEL", "BOM", "BLR").
        destination: Arrival IATA airport code (e.g. "BOM", "DEL", "HYD").
        date: Departure date in YYYY-MM-DD format.
        adults: Number of adult travelers.
        children: Number of child travelers (2-11 years).
        infants: Number of infant travelers (under 2).
        currency: Currency code for prices (default: INR).
    """
    profile_id = profile_slug or _PROFILE_ID

    jwt = await _get_jwt(profile_id)

    passenger_types: list[dict[str, Any]] = [{"count": adults, "discountCode": "", "type": "ADT"}]
    if children:
        passenger_types.append({"count": children, "discountCode": "", "type": "CHD"})
    if infants:
        passenger_types.append({"count": infants, "discountCode": "", "type": "INF"})

    body: dict[str, Any] = {
        "codes": {"currency": currency, "promotionCode": ""},
        "criteria": [
            {
                "dates": {"beginDate": date},
                "flightFilters": {"type": "All"},
                "stations": {
                    "originStationCodes": [origin],
                    "destinationStationCodes": [destination],
                },
            }
        ],
        "passengers": {"residentCountry": "IN", "types": passenger_types},
        "taxesAndFees": "TaxesAndFees",
        "tripCriteria": "oneWay",
        "isRedeemTransaction": False,
    }

    return await execute_fetch(
        profile_id,
        _SEARCH_URL,
        method="POST",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Authorization": jwt,
            "User_key": _USER_KEY,
            "Origin": "https://www.goindigo.in",
            "Referer": "https://www.goindigo.in/",
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search IndiGo for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "DEL".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "BOM".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
    parser.add_argument("--children", type=int, default=0, help="Number of child travelers.")
    parser.add_argument("--infants", type=int, default=0, help="Number of infant travelers.")
    parser.add_argument("--currency", default="INR", help="Currency code (default: INR).")
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
                adults=args.adults,
                children=args.children,
                infants=args.infants,
                currency=args.currency,
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
