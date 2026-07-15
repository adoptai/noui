#!/usr/bin/env python3
"""Google Flights shopping results via Tabby execute/fetch."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from noui_runtime.execute import execute_fetch

BASE_URL = "https://www.google.com"
DEFAULT_PROFILE = "google-flights"

_QS = {
    "f.sid": "-7456312471093751493",
    "bl": "boq_travel-frontend-flights-ui_20260330.02_p2",
    "hl": "en",
    "soc-app": "162",
    "soc-platform": "1",
    "soc-device": "1",
    "rt": "c",
}

_TRIP_ONE_WAY = 2
_TRIP_ROUND = 1


def _profile_slug(override: str | None = None) -> str:
    return override or os.environ.get("PROFILE_SLUG") or DEFAULT_PROFILE


async def execute(
    origin_entity_id: str,
    destination_entity_id: str,
    departure_date: str,
    return_date: str = "",
    adults: int = 1,
    csrf_token: str = "",
    profile_slug: str | None = None,
) -> dict:
    """Search available flights. Leave return_date empty for one-way."""
    trip_type = _TRIP_ROUND if return_date else _TRIP_ONE_WAY

    legs = [
        [
            [[[origin_entity_id, 5]]],
            [[[destination_entity_id, 5]]],
            None,
            0,
            None,
            None,
            departure_date,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            3,
        ],
    ]
    if return_date:
        legs.append(
            [
                [[[destination_entity_id, 5]]],
                [[[origin_entity_id, 5]]],
                None,
                0,
                None,
                None,
                return_date,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                3,
            ]
        )

    inner = [
        [],
        [
            None,
            None,
            trip_type,
            None,
            [],
            1,
            [adults, 0, 0, 0],
            None,
            None,
            None,
            None,
            None,
            None,
            legs,
            None,
            None,
            None,
            1,
        ],
        0,
        0,
        0,
        1,
    ]
    freq = json.dumps([None, json.dumps(inner)])
    body_parts = {"f.req": freq}
    if csrf_token:
        body_parts["at"] = csrf_token
    body = urllib.parse.urlencode(body_parts)
    path = (
        "/_/FlightsFrontendUi/data/travel.frontend.flights."
        "FlightsFrontendService/GetShoppingResults"
    )
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(_QS)}"

    return await execute_fetch(
        _profile_slug(profile_slug),
        url,
        method="POST",
        body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="search_flights")
    p.add_argument("--origin-entity-id", dest="origin_entity_id", required=True)
    p.add_argument("--destination-entity-id", dest="destination_entity_id", required=True)
    p.add_argument("--departure-date", dest="departure_date", required=True)
    p.add_argument("--return-date", dest="return_date", default="")
    p.add_argument("--adults", type=int, default=1)
    p.add_argument("--csrf-token", dest="csrf_token", default="")
    p.add_argument("--profile-slug", dest="profile_slug", default=None)
    args = p.parse_args(argv)
    try:
        result = asyncio.run(
            execute(
                origin_entity_id=args.origin_entity_id,
                destination_entity_id=args.destination_entity_id,
                departure_date=args.departure_date,
                return_date=args.return_date,
                adults=args.adults,
                csrf_token=args.csrf_token,
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
