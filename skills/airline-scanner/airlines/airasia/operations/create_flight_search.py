#!/usr/bin/env python3
"""Skill operation: create_flight_search
Method: POST
Path: /web/fp/search/flights/v5/aggregated-results

AirAsia uses Cloudflare Bot Management on the flights API. Requests run inside
Tabby's browser session via execute_fetch (POST /execute/fetch):
  1. POST to /fp/authentication/auth/login with anonymous web-client credentials to get JWT.
  2. POST to /web/fp/search/flights/v5/aggregated-results with that JWT.
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

from noui_runtime.execute import execute_fetch  # noqa: E402

_PROFILE_ID = "airasia"
_BASE_URL = "https://flights.airasia.com"
_LOGIN_URL = f"{_BASE_URL}/fp/authentication/auth/login"
_CHANNEL_HASH = "c5e9028b4295dcf4d7c239af8231823b520c3cc15b99ab04cde71d0ab18d65bc"

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


async def execute(
    origin: str = "KUL",
    destination: str = "SIN",
    date: str = "2026-08-21",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    currency: str = "USD",
    profile_slug: str | None = None,
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
    profile_id = profile_slug or _PROFILE_ID

    # Convert YYYY-MM-DD → DD/MM/YYYY (AirAsia's expected format)
    year, month, day = date.split("-")
    depart_date = f"{day}/{month}/{year}"

    # Step 1: Get a short-lived JWT (web-client anonymous auth)
    login_data = await execute_fetch(
        profile_id,
        _LOGIN_URL,
        method="POST",
        body={"username": "flightsweb", "password": "6x6jF7bYrrkVqV2Y"},
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "channel_hash": _CHANNEL_HASH,
        },
    )
    jwt = login_data.get("jwt", "")
    if not jwt:
        raise RuntimeError(f"Login did not return a JWT. Got: {list(login_data.keys())}")

    # Step 2: Search flights
    search_url = f"{_BASE_URL}/web/fp/search/flights/v5/aggregated-results{_SEARCH_QUERY_PARAMS}"
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

    return await execute_fetch(
        profile_id,
        search_url,
        method="POST",
        body=search_body,
        headers={
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
        print(f"create_flight_search failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
