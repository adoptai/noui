#!/usr/bin/env python3
"""Skill operation: create_flight_search
Method: POST
Path: /prod/api/v3/availability/search

Volaris uses AWS WAF bot detection. This skill runs requests inside Tabby's
browser session via execute_fetch (POST /execute/fetch):
  1. GET /prod/api/v1/session → fresh anonymous DotRez JWT.
  2. POST /prod/api/v3/availability/search with that JWT.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.execute import execute_fetch  # noqa: E402

_PROFILE_ID = "volaris"
_SEARCH_URL = "https://apigw.volaris.com/prod/api/v3/availability/search"
_SESSION_URL = "https://apigw.volaris.com/prod/api/v1/session"

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _format_date(date_str: str) -> str:
    """Convert YYYY-MM-DD to 'Thu, Aug 20, 2026' (Volaris's expected format)."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = _WEEKDAYS[d.weekday()]
    month = _MONTHS[d.month - 1]
    return f"{weekday}, {month} {d.day}, {d.year}"


async def execute(
    origin: str = "MEX",
    destination: str = "GDL",
    date: str = "2026-08-20",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    currency: str = "MXN",
    profile_slug: str | None = None,
) -> dict[str, Any]:
    """Search Volaris for available flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "MEX", "GDL", "CUN").
        destination: Arrival IATA airport code (e.g. "GDL", "MEX", "CUN").
        date: Departure date in YYYY-MM-DD format.
        adults: Number of adult travelers.
        children: Number of child travelers (2-11 years).
        infants: Number of infant travelers (under 2).
        currency: Currency code for prices (default: MXN).
    """
    profile_id = profile_slug or _PROFILE_ID

    # Step 1: Get a fresh anonymous session JWT
    session_data = await execute_fetch(
        profile_id,
        _SESSION_URL,
        method="GET",
        headers={"Accept": "application/json"},
    )
    jwt = session_data.get("token", "")
    if not jwt:
        raise RuntimeError(f"Volaris session did not return a token. Got: {list(session_data.keys())}")

    begin_date = _format_date(date)

    passenger_types = [{"type": "ADT", "count": adults}]
    if children:
        passenger_types.append({"type": "CHD", "count": children})
    if infants:
        passenger_types.append({"type": "INF", "count": infants})

    body: dict[str, Any] = {
        "passengers": {"types": passenger_types},
        "criteria": [
            {
                "stations": {
                    "originStationCodes": [origin],
                    "destinationStationCodes": [destination],
                },
                "dates": {"beginDate": begin_date},
                "filters": {
                    "fareTypes": ["R"],
                    "maxConnections": 20,
                    "bundleControlFilter": 2,
                },
            }
        ],
        "codes": {"currencyCode": currency, "promotionCode": ""},
        "taxesAndFees": 2,
        "shouldIncludeSoldOut": True,
        "shouldIncludeTua": True,
    }

    return await execute_fetch(
        profile_id,
        _SEARCH_URL,
        method="POST",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": jwt,
            "Flow": "MBS",
            "Frontend": "WEB",
            "Origin": "https://www.volaris.com",
            "Referer": "https://www.volaris.com/",
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_flight_search",
        description="Search Volaris for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "MEX".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "GDL".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
    parser.add_argument("--children", type=int, default=0, help="Number of child travelers.")
    parser.add_argument("--infants", type=int, default=0, help="Number of infant travelers.")
    parser.add_argument("--currency", default="MXN", help="Currency code (default: MXN).")
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
