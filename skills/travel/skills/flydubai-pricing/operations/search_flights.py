#!/usr/bin/env python3
"""Search Flydubai fares via Tabby execute/fetch."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from noui_runtime.execute import execute_fetch

API_URL = "https://flights2.flydubai.com/api/flights/7"
DEFAULT_PROFILE = "flydubai"


def _profile_slug(override: str | None = None) -> str:
    return override or os.environ.get("PROFILE_SLUG") or DEFAULT_PROFILE


def _format_date(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%m/%d/%Y 12:00 AM")


def build_payload(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str = "",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    cabin_class: str = "Economy",
) -> dict:
    criteria = [
        {
            "date": _format_date(depart_date),
            "dest": destination,
            "direction": "outBound",
            "origin": origin,
            "isOriginMetro": False,
            "isDestMetro": False,
        }
    ]

    if return_date:
        criteria.append(
            {
                "date": _format_date(return_date),
                "dest": origin,
                "direction": "inBound",
                "origin": destination,
                "isOriginMetro": False,
                "isDestMetro": False,
            }
        )

    return {
        "promoCode": "",
        "campaignCode": "",
        "cabinClass": cabin_class,
        "isDestMetro": "false",
        "isOriginMetro": "false",
        "paxInfo": {"adultCount": adults, "childCount": children, "infantCount": infants},
        "searchCriteria": criteria,
        "variant": "1",
    }


async def execute(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str = "",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    cabin_class: str = "Economy",
    include_nearby: bool = True,
    profile_slug: str | None = None,
) -> list[dict]:
    payload = build_payload(
        origin, destination, depart_date, return_date, adults, children, infants, cabin_class
    )

    data = await execute_fetch(
        _profile_slug(profile_slug),
        API_URL,
        method="POST",
        body=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://flights2.flydubai.com",
            "Referer": "https://flights2.flydubai.com/",
            "appID": "DESKTOP",
        },
    )

    requested_dates = {depart_date}
    if return_date:
        requested_dates.add(return_date)

    results = []
    for seg in data.get("segments", []) if isinstance(data, dict) else []:
        fare = seg.get("lowestAdultFarePerPax")
        departure = seg.get("departureDate", "")
        departure_day = departure[:10]

        if not fare or fare == "0.00":
            continue
        if not include_nearby and departure_day not in requested_dates:
            continue

        results.append(
            {
                "route": seg.get("route"),
                "direction": seg.get("direction"),
                "departureDate": departure,
                "fare": fare,
                "tax": seg.get("lowestAdultFareTaxSumPerPax"),
                "currency": seg.get("currencyCode"),
                "soldOut": seg.get("isSoldOut"),
            }
        )

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="search_flights")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--depart-date", required=True)
    parser.add_argument("--return-date", default="")
    parser.add_argument("--adults", type=int, default=1)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--infants", type=int, default=0)
    parser.add_argument("--cabin-class", default="Economy")
    parser.add_argument("--exact-dates-only", action="store_true")
    parser.add_argument("--profile-slug", dest="profile_slug", default=None)
    args = parser.parse_args(argv)

    try:
        result = asyncio.run(
            execute(
                origin=args.origin,
                destination=args.destination,
                depart_date=args.depart_date,
                return_date=args.return_date,
                adults=args.adults,
                children=args.children,
                infants=args.infants,
                cabin_class=args.cabin_class,
                include_nearby=not args.exact_dates_only,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"search_flights failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
