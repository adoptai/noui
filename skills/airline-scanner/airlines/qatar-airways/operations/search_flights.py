#!/usr/bin/env python3
"""Search Qatar Airways for available flights on a given route and date.

Qatar Airways uses Akamai bot protection — direct Python requests fail.
Requests run inside Tabby's browser via cdp_fetch (same-origin API).
Auth: static nbx_fs_api_key + X-AssignedDeviceID from localStorage
     + a fresh session-id UUID per call.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.cdp import cdp_eval, cdp_fetch, find_page  # noqa: E402

CDP_HOST_MATCH = "qatarairways.com"
_SEARCH_URL = "https://www.qatarairways.com/dapi/public/bff/web/flight-search/flight-offers"
_API_KEY = "74f2474702784207a9785eb3ef8ae4e4"
_DEVICE_ID_KEY = "booking-widget.device.id"


async def _get_device_id(ws_url: str) -> str:
    """Read the device ID from localStorage (set by QR booking widget on first visit)."""
    device_id = await cdp_eval(
        ws_url, f"JSON.stringify(localStorage.getItem({json.dumps(_DEVICE_ID_KEY)}))"
    )
    if not device_id:
        raise RuntimeError(
            "booking-widget.device.id not found in localStorage. "
            "Make sure the Tabby session has visited qatarairways.com at least once."
        )
    return device_id


async def execute(
    origin: str = "DOH",
    destination: str = "LHR",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
) -> dict[str, Any]:
    """Search Qatar Airways for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "DOH", "LHR", "JFK").
        destination: Arrival IATA airport code (e.g. "LHR", "DOH", "DXB").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
        children: Number of child travelers (2-11 years).
        infants: Number of infant travelers (under 2).
    """
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile qatar-airways-search"
        )

    device_id = await _get_device_id(ws_url)

    passengers: list[dict[str, Any]] = [{"type": "ADT", "count": adults}]
    if children:
        passengers.append({"type": "CHD", "count": children})
    if infants:
        passengers.append({"type": "INF", "count": infants})

    body: dict[str, Any] = {
        "channel": "WEB_DESKTOP",
        "itineraries": [
            {
                "origin": origin,
                "destination": destination,
                "departureDate": date,
                "isRequested": True,
            }
        ],
        "cabinClass": cabin_class,
        "ignoreInvalidPromoCode": True,
        "passengers": passengers,
    }

    return await cdp_fetch(
        ws_url,
        _SEARCH_URL,
        method="POST",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "nbx_fs_api_key": _API_KEY,
            "qr-lang": "en",
            "session-id": str(uuid.uuid4()),
            "X-AssignedDeviceID": device_id,
            "Origin": "https://www.qatarairways.com",
            "Referer": "https://www.qatarairways.com/",
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search Qatar Airways for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "DOH".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "LHR".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument(
        "--cabin-class",
        default="ECONOMY",
        choices=["ECONOMY", "BUSINESS", "FIRST"],
        help="Cabin class (default: ECONOMY).",
    )
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
    parser.add_argument("--children", type=int, default=0, help="Number of child travelers.")
    parser.add_argument("--infants", type=int, default=0, help="Number of infant travelers.")
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
                children=args.children,
                infants=args.infants,
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
