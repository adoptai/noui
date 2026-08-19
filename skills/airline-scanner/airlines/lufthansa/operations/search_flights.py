#!/usr/bin/env python3
"""Search Lufthansa for available flights on a given route and date.

Two-step flow via Tabby execute_fetch (POST /execute/fetch):
1. POST api.shop.lufthansa.com/one-booking/v2/auth/token with static
   client_id/client_secret → get Bearer JWT.
2. POST api.shop.lufthansa.com/one-booking/v2/search/air-bounds with the
   JWT → get flight availability (air bounds).

Both endpoints sit behind Cloudflare. execute_fetch runs inside Tabby's
browser session with real cookies and TLS fingerprint.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import uuid
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.execute import execute_fetch  # noqa: E402

_PROFILE_ID = "lufthansa"
_AUTH_URL = "https://api.shop.lufthansa.com/one-booking/v2/auth/token"
_SEARCH_URL = "https://api.shop.lufthansa.com/one-booking/v2/search/air-bounds"
_CLIENT_ID = "onebooking-ui-lh"
_CLIENT_SECRET = "RNJg0okYPt9fkh8BMkmrbDvpMLh5A3sl"
_AUTH_BODY = (
    f"client_id={_CLIENT_ID}&client_secret={_CLIENT_SECRET}"
    "&context=%7B%22country%22%3A%22DE%22%7D&grant_type=client_credentials"
)

_CABIN_CODES = {"ECONOMY": "ECONOMY", "BUSINESS": "BUSINESS", "FIRST": "FIRST"}


def _ama_client_facts(cabin: str) -> str:
    header = (
        base64.b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    )
    payload = (
        base64.b64encode(json.dumps({"sub": "fact", "cabin": cabin}).encode()).rstrip(b"=").decode()
    )
    return f"{header}.{payload}."


async def execute(
    origin: str = "FRA",
    destination: str = "NYC",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search Lufthansa for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "FRA", "MUC", "LHR").
        destination: Arrival IATA city or airport code (e.g. "NYC", "JFK", "LHR").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    profile_id = profile_slug or _PROFILE_ID
    cabin = _CABIN_CODES.get(cabin_class.upper(), "ECONOMY")
    call_id = str(uuid.uuid4())
    ama_facts = _ama_client_facts(cabin)

    # Step 1: Authenticate to get Bearer token
    auth_data = await execute_fetch(
        profile_id,
        _AUTH_URL,
        method="POST",
        body=_AUTH_BODY,  # URL-encoded string passed as-is
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "callid": call_id,
            "traceparent": f"00-{call_id.replace('-', '')}-0000000000000000-00",
        },
    )
    token = auth_data.get("access_token", "")
    if not token:
        raise RuntimeError(f"Lufthansa auth did not return access_token. Got: {list(auth_data.keys())}")

    # Step 2: Search air bounds
    search_body = {
        "commercialFareFamilies": ["DEMALLFPP"],
        "itineraries": [
            {
                "departureDateTime": f"{date}T00:00:00.000",
                "originLocationCode": origin,
                "destinationLocationCode": destination,
                "isRequestedBound": True,
            }
        ],
        "travelers": [{"discounts": [], "passengerTypeCode": "ADT"} for _ in range(adults)],
        "searchPreferences": {"showSoldOut": False, "showMilesPrice": False},
    }

    return await execute_fetch(
        profile_id,
        _SEARCH_URL,
        method="POST",
        body=search_body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "authorization": f"Bearer {token}",
            "ama-client-ref": f"{call_id}:1",
            "ama-client-facts": ama_facts,
            "callid": f"{call_id}:1",
            "traceparent": f"00-{call_id.replace('-', '')}-0000000000000002-00",
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search Lufthansa for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "FRA".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "NYC".')
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
