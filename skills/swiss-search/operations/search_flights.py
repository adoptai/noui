#!/usr/bin/env python3
"""Search Swiss International for available flights on a given route and date.

Same LH Group one-booking API as Lufthansa, with Swiss-specific credentials:
  client_id=onebooking-ui-lx, context=CH, api.shop.swiss.com.

Both endpoints sit behind Cloudflare — direct Python calls return 403.
Requests run inside Tabby's shop.swiss.com browser session via cdp_eval.
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

from noui_runtime.cdp import cdp_eval, find_page  # noqa: E402

CDP_HOST_MATCH = "swiss"
_AUTH_URL = "https://api.shop.swiss.com/one-booking/v2/auth/token"
_SEARCH_URL = "https://api.shop.swiss.com/one-booking/v2/search/air-bounds"
_CLIENT_ID = "onebooking-ui-lx"
_CLIENT_SECRET = "FLNDvVWswe65NIEfbkjjPAJvpGgVm1nB"
_AUTH_BODY = (
    f"client_id={_CLIENT_ID}&client_secret={_CLIENT_SECRET}"
    "&context=%7B%22country%22%3A%22CH%22%7D&grant_type=client_credentials"
)

_CABIN_CODES = {"ECONOMY": "ECONOMY", "BUSINESS": "BUSINESS", "FIRST": "FIRST"}


def _ama_client_facts(cabin: str) -> str:
    """Build the unsigned JWT metadata header expected by the Lufthansa API."""
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
) -> dict[str, Any]:
    """Search Swiss International for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "ZRH", "GVA", "LHR").
        destination: Arrival IATA city or airport code (e.g. "NYC", "JFK", "LHR").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile swiss-search"
        )

    cabin = _CABIN_CODES.get(cabin_class.upper(), "ECONOMY")
    call_id = str(uuid.uuid4())
    ama_facts = _ama_client_facts(cabin)

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

    js = f"""(function() {{
        var callId = {json.dumps(call_id)};
        var traceparent = '00-' + callId.replace(/-/g,'') + '-0000000000000000-00';
        return fetch({json.dumps(_AUTH_URL)}, {{
            method: 'POST', credentials: 'include',
            headers: {{'Content-Type': 'application/x-www-form-urlencoded',
                      'Accept': 'application/json',
                      'callid': callId, 'traceparent': traceparent}},
            body: {json.dumps(_AUTH_BODY)}
        }})
        .then(r => r.json())
        .then(auth => {{
            var token = auth.access_token;
            return fetch({json.dumps(_SEARCH_URL)}, {{
                method: 'POST', credentials: 'include',
                headers: {{'Content-Type': 'application/json', 'Accept': 'application/json',
                          'authorization': 'Bearer ' + token,
                          'ama-client-ref': callId + ':1',
                          'ama-client-facts': {json.dumps(ama_facts)},
                          'callid': callId + ':1',
                          'traceparent': '00-' + callId.replace(/-/g,'') + '-0000000000000002-00'}},
                body: JSON.stringify({json.dumps(search_body)})
            }});
        }})
        .then(r => r.text().then(t => JSON.stringify({{status: r.status, body: t}})))
        .catch(e => JSON.stringify({{error: e.message}}));
    }})()"""

    raw = await cdp_eval(ws_url, js)
    if "error" in raw:
        raise RuntimeError(f"Lufthansa search failed: {raw['error']}")
    status = raw.get("status")
    body_str = raw.get("body", "")
    if not (isinstance(status, int) and 200 <= status < 300):
        raise RuntimeError(f"POST {_SEARCH_URL} -> {status}: {body_str[:300]}")
    return json.loads(body_str) if body_str else {}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search Swiss International for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "ZRH".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "NYC".')
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
