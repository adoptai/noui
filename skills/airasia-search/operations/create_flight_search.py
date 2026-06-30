#!/usr/bin/env python3
"""Skill operation: create_flight_search
Method: POST
Path: /web/fp/search/flights/v5/aggregated-results

AirAsia uses Cloudflare Bot Management on the flights API, so all requests must
be made from inside Tabby's real Chrome browser (via CDP eval + fetch) rather
than direct HTTP calls. The skill:
  1. Gets a short-lived JWT by POSTing to /fp/authentication/auth/login
     (hardcoded web-client credentials — not user credentials)
  2. Calls the aggregated-results search endpoint with that JWT
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

from noui_runtime.cdp import cdp_eval, find_page  # noqa: E402

BASE_URL = "https://flights.airasia.com"
CDP_HOST_MATCH = "airasia.com"

_CHANNEL_HASH = "c5e9028b4295dcf4d7c239af8231823b520c3cc15b99ab04cde71d0ab18d65bc"

# Static anonymous SSO tokens for unauthenticated searches (all-zeros userId)
_ANON_ACCESS_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJ0eXBlIjoiQUNDRVNTX1RPS0VOIiwidXNlcklkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiaWF0IjoxNzE1MDY5MzExLCJleHAiOjI3MTUwNjkzMTF9"
    ".oXnyKOmRNaSZdWn9MAuo1_lm74Y4fhVSF1OYLh3Yk7M"
)
_ANON_REFRESH_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJ0eXBlIjoiUkVGUkVTSF9UT0tFTiIsInVzZXJJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsImlhdCI6MTcxNTA2OTMxMSwiZXhwIjoyNzE1MDY5MzExfQ"
    "._VcC9cowXg7ZG0HpVdNtFemEVqxy8GTzyMJGPfBPcHU"
)

_SEARCH_QUERY_PARAMS = (
    "?page=1"
    "&include_list=searchResults%2Ccurrency%2Ccontent%2CfeatureFlags%2Clocale%2Cvouchers%2CupsellSnap%2CupsellFlatbed%2CupsellPremiumFlatBed"
    "&airlineProfile=d%2Cv%2Cg%2Ck"
    "&type=paired"
    "&isPromoMessagesByCode=true"
    "&isOriginCity=false"
    "&isDestinationCity=false"
    "&uce=true"
)


async def _cdp_post(ws_url: str, url: str, body: Any, headers: dict[str, str]) -> Any:
    """Run a CORS fetch (no credentials) inside the browser and return parsed JSON body."""
    init = {
        "method": "POST",
        "mode": "cors",
        "headers": headers,
        "body": json.dumps(body),
    }
    js = (
        f"fetch({json.dumps(url)}, {json.dumps(init)})"
        ".then(r => r.text().then(t => JSON.stringify({status: r.status, body: t})))"
    )
    raw = await cdp_eval(ws_url, js)
    status = raw.get("status")
    body_str = raw.get("body", "")
    if not (isinstance(status, int) and 200 <= status < 300):
        raise RuntimeError(f"POST {url} -> {status}: {body_str[:300]}")
    return json.loads(body_str) if body_str else {}


async def execute(
    origin: str = "KUL",
    destination: str = "SIN",
    date: str = "2026-08-21",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    currency: str = "USD",
) -> dict[str, Any]:
    """Search AirAsia for available flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "KUL", "SIN", "BKK").
        destination: Arrival IATA airport code (e.g. "SIN", "KUL", "BKK").
        date: Departure date in YYYY-MM-DD format.
        adults: Number of adult travelers.
        children: Number of child travelers.
        infants: Number of infant travelers.
        currency: Currency code for displayed prices (default: USD).
    """
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile airasia-search"
        )

    # Convert YYYY-MM-DD → DD/MM/YYYY (AirAsia's expected format)
    year, month, day = date.split("-")
    depart_date = f"{day}/{month}/{year}"

    # Step 1: Get a short-lived JWT (web-client anonymous auth)
    login_data = await _cdp_post(
        ws_url,
        f"{BASE_URL}/fp/authentication/auth/login",
        {"username": "flightsweb", "password": "6x6jF7bYrrkVqV2Y"},
        {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "channel_hash": _CHANNEL_HASH,
        },
    )
    jwt = login_data.get("jwt", "")
    if not jwt:
        raise RuntimeError(f"Login did not return a JWT. Got: {list(login_data.keys())}")

    # Step 2: Search flights
    search_url = f"{BASE_URL}/web/fp/search/flights/v5/aggregated-results{_SEARCH_QUERY_PARAMS}"
    search_body: dict[str, Any] = {
        "consumerId": "Website",
        "flightJourney": {
            "journeyType": "O",
            "journeyDetails": [
                {
                    "origin": origin,
                    "destination": destination,
                    "departDate": depart_date,
                    "returnDate": None,
                }
            ],
            "passengers": {"adult": adults, "child": children, "infant": infants},
        },
        "searchContext": {
            "promocode": "",
            "filters": {
                "cabin": {"cabinClass": "ECONOMY", "applyMixedClasses": False},
                "stops": {"stopType": "ANY", "allowOvernight": False},
                "duration": {
                    "maxTravelTimeInHrs": 59,
                    "maxStopoverTimeInHrs": 25,
                    "minStopoverTimeInHrs": 0,
                },
                "carriers": {
                    "allowAllCarriers": True,
                    "onlyAllowedCarriers": [],
                    "excludedCarriers": [],
                },
                "departAirports": {"allowAllAirports": True, "allowedDepartAirports": []},
                "returnAirports": {"allowAllAirports": True, "allowedReturnAirports": []},
                "journey": "O",
            },
        },
        "userContext": {
            "currency": currency,
            "geoId": "CN",
            "locale": "en-gb",
            "platform": "web",
        },
        "ssoDetails": {
            "accessToken": _ANON_ACCESS_TOKEN,
            "refreshToken": _ANON_REFRESH_TOKEN,
            "userId": "00000000-0000-0000-0000-000000000000",
        },
        "selectedDepartFlight": None,
    }

    return await _cdp_post(
        ws_url,
        search_url,
        search_body,
        {
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "channel_hash": _CHANNEL_HASH,
            "User-Type": "ANONYMOUS",
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_flight_search",
        description="Search AirAsia for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "KUL".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "SIN".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
    parser.add_argument("--children", type=int, default=0, help="Number of child travelers.")
    parser.add_argument("--infants", type=int, default=0, help="Number of infant travelers.")
    parser.add_argument("--currency", default="USD", help="Currency code (default: USD).")
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
            )
        )
    except Exception as exc:
        print(f"create_flight_search failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
