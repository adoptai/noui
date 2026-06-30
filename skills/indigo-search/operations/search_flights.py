#!/usr/bin/env python3
"""Search IndiGo (6E) for available flights on a given route and date.

IndiGo uses Akamai bot protection on its API. Requests must be made from
inside Tabby's browser via CDP eval. The DotRez anonymous session JWT is
captured via CDP Fetch interception when the browser navigates to the IndiGo
homepage (which triggers a PUT /v1/token/refresh call). The JWT has no exp
claim and persists for the browser session; it is cached in window._indigoJWT
so subsequent calls within the same session skip the re-capture step.
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

CDP_HOST_MATCH = "goindigo.in"
_SEARCH_URL = "https://api-prod-flight-skyplus6e.goindigo.in/v2/flight/search"
_HOME_URL = "https://www.goindigo.in/"
_TOKEN_REFRESH_PATTERN = "*/v1/token/refresh"
_USER_KEY = "31e90be8fff2f5e2eea242c225f21b1a"


async def _capture_jwt(ws_url: str) -> str:
    """Navigate to the IndiGo homepage and intercept the JWT from the
    PUT /v1/token/refresh request that the Angular app fires on load."""
    captured: list[str] = []

    async with websockets.connect(ws_url) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Fetch.enable",
                    "params": {
                        "patterns": [
                            {"urlPattern": _TOKEN_REFRESH_PATTERN, "requestStage": "Request"}
                        ]
                    },
                }
            )
        )
        await asyncio.sleep(0.1)
        await ws.send(
            json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": _HOME_URL}})
        )

        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline and not captured:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                if msg.get("method") == "Fetch.requestPaused":
                    rid = msg["params"]["requestId"]
                    hdrs = msg["params"]["request"].get("headers", {})
                    auth = hdrs.get("authorization") or hdrs.get("Authorization") or ""
                    if auth and auth.startswith("eyJ"):
                        captured.append(auth)
                    await ws.send(
                        json.dumps(
                            {
                                "id": 50,
                                "method": "Fetch.continueRequest",
                                "params": {"requestId": rid},
                            }
                        )
                    )
            except TimeoutError:
                continue

        await ws.send(json.dumps({"id": 3, "method": "Fetch.disable"}))

    if not captured:
        raise RuntimeError(
            "Could not capture IndiGo JWT — token/refresh was not observed. "
            "Check that the Tabby session for 'indigo-search' is healthy."
        )
    return captured[0]


async def _get_jwt(ws_url: str) -> str:
    """Return a valid JWT, re-capturing if the browser session doesn't have one."""
    stored = await cdp_eval(ws_url, "JSON.stringify(window._indigoJWT || null)")
    if stored and stored != "null":
        return stored
    jwt = await _capture_jwt(ws_url)
    await cdp_eval(ws_url, f"(window._indigoJWT = {json.dumps(jwt)}, null)")
    return jwt


async def execute(
    origin: str = "DEL",
    destination: str = "BOM",
    date: str = "2026-08-20",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    currency: str = "INR",
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
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile indigo-search"
        )

    jwt = await _get_jwt(ws_url)

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

    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Authorization": jwt,
        "User_key": _USER_KEY,
        "Origin": "https://www.goindigo.in",
        "Referer": "https://www.goindigo.in/",
    }

    js = (
        f"fetch({json.dumps(_SEARCH_URL)}, {{"
        f"  method: 'POST', mode: 'cors',"
        f"  headers: {json.dumps(headers)},"
        f"  body: JSON.stringify({json.dumps(body)})"
        f"}}).then(r => r.text().then(t => JSON.stringify({{status: r.status, body: t}})))"
    )

    raw = await cdp_eval(ws_url, js)
    status = raw.get("status")
    body_str = raw.get("body", "")
    if not (isinstance(status, int) and 200 <= status < 300):
        # JWT might have been invalidated — clear cached JWT and raise
        await cdp_eval(ws_url, "delete window._indigoJWT")
        raise RuntimeError(f"POST {_SEARCH_URL} -> {status}: {body_str[:300]}")
    return json.loads(body_str) if body_str else {}


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
        print(f"search_flights failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
