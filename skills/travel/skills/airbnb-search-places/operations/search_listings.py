#!/usr/bin/env python3
"""Search Airbnb stay listings via Tabby execute/fetch.

Uses Airbnb's persisted StaysSearch GraphQL query. If Airbnb ships a
frontend update that rotates the persisted-query hash, re-record the
workflow to refresh `_PERSISTED_QUERY_HASH` below.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.parse
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from noui_runtime.execute import execute_fetch

AIRBNB_HOST = "https://www.airbnb.com"
AIRBNB_API_KEY = "d306zoyjsyarp7ifhu67rjxn52tv0t20"
DEFAULT_PROFILE = "airbnb"

_PERSISTED_QUERY_HASH = "753d97c7b19a1a402d2fa63882ff4d6802004d11f2499647deef923a19a1641a"

_TREATMENT_FLAGS = [
    "feed_map_decouple_m11_treatment",
    "recommended_amenities_2024_treatment_b",
    "filter_redesign_2024_treatment",
    "filter_reordering_2024_roomtype_treatment",
    "p2_category_bar_removal_treatment",
    "selected_filters_2024_treatment",
    "recommended_filters_2024_treatment_b",
    "m13_search_input_phase2_treatment",
    "m13_search_input_services_enabled",
    "m13_2025_experiences_p2_treatment",
]

_HEADERS = {
    "X-Airbnb-API-Key": AIRBNB_API_KEY,
    "X-Airbnb-GraphQL-Platform": "web",
    "X-Airbnb-GraphQL-Platform-Client": "minimalist-niobe",
    "X-Airbnb-Supports-Airlock-V2": "true",
    "X-Niobe-Short-Circuited": "true",
    "X-CSRF-Without-Token": "1",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": AIRBNB_HOST,
    "Referer": f"{AIRBNB_HOST}/",
}


def _profile_slug(override: str | None = None) -> str:
    return override or os.environ.get("PROFILE_SLUG") or DEFAULT_PROFILE


def _raw_params(
    place_id: str,
    query: str,
    checkin: str,
    checkout: str,
    adults: int,
    children: int,
    infants: int,
    pets: int,
    acp_id: str,
    include_items_per_grid: bool,
) -> list[dict]:
    params = [
        {"filterName": "acpId", "filterValues": [acp_id]},
        {"filterName": "adults", "filterValues": [str(adults)]},
        {"filterName": "cdnCacheSafe", "filterValues": ["false"]},
        {"filterName": "checkin", "filterValues": [checkin]},
        {"filterName": "checkout", "filterValues": [checkout]},
        {"filterName": "datePickerType", "filterValues": ["calendar"]},
    ]
    if children:
        params.append({"filterName": "children", "filterValues": [str(children)]})
    if infants:
        params.append({"filterName": "infants", "filterValues": [str(infants)]})
    if include_items_per_grid:
        params.append({"filterName": "itemsPerGrid", "filterValues": ["18"]})
    if pets:
        params.append({"filterName": "pets", "filterValues": [str(pets)]})
    params.extend(
        [
            {"filterName": "placeId", "filterValues": [place_id]},
            {"filterName": "query", "filterValues": [query]},
            {"filterName": "refinementPaths", "filterValues": ["/homes"]},
            {"filterName": "screenSize", "filterValues": ["large"]},
            {"filterName": "tabId", "filterValues": ["home_tab"]},
            {"filterName": "version", "filterValues": ["1.8.8"]},
        ]
    )
    return params


async def execute(
    place_id: str,
    query: str,
    checkin: str,
    checkout: str,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    pets: int = 0,
    locale: str = "en",
    currency: str = "USD",
    profile_slug: str | None = None,
) -> dict:
    """Search Airbnb stay listings via Tabby browser fetch."""
    acp_id = str(uuid.uuid4())

    stays_request = {
        "metadataOnly": False,
        "requestedPageType": "STAYS_SEARCH",
        "searchType": "autocomplete_click",
        "treatmentFlags": _TREATMENT_FLAGS,
        "maxMapItems": 9999,
        "rawParams": _raw_params(
            place_id,
            query,
            checkin,
            checkout,
            adults,
            children,
            infants,
            pets,
            acp_id,
            include_items_per_grid=True,
        ),
    }
    map_request = {
        "metadataOnly": False,
        "requestedPageType": "STAYS_SEARCH",
        "searchType": "autocomplete_click",
        "treatmentFlags": _TREATMENT_FLAGS,
        "rawParams": _raw_params(
            place_id,
            query,
            checkin,
            checkout,
            adults,
            children,
            infants,
            pets,
            acp_id,
            include_items_per_grid=False,
        ),
    }

    body = {
        "operationName": "StaysSearch",
        "variables": {
            "staysSearchRequest": stays_request,
            "staysMapSearchRequestV2": map_request,
            "isLeanTreatment": False,
            "aiSearchEnabled": False,
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": _PERSISTED_QUERY_HASH,
            }
        },
    }

    qs = urllib.parse.urlencode(
        {
            "operationName": "StaysSearch",
            "locale": locale,
            "currency": currency,
        }
    )
    url = f"{AIRBNB_HOST}/api/v3/StaysSearch/{_PERSISTED_QUERY_HASH}?{qs}"
    return await execute_fetch(
        _profile_slug(profile_slug),
        url,
        method="POST",
        body=body,
        headers=_HEADERS,
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="search_listings",
        description="Search Airbnb stay listings by place + dates + guests.",
    )
    p.add_argument("--place-id", dest="place_id", required=True)
    p.add_argument("--query", required=True, help="Human-readable place name.")
    p.add_argument("--checkin", required=True, help="YYYY-MM-DD")
    p.add_argument("--checkout", required=True, help="YYYY-MM-DD")
    p.add_argument("--adults", type=int, default=1)
    p.add_argument("--children", type=int, default=0)
    p.add_argument("--infants", type=int, default=0)
    p.add_argument("--pets", type=int, default=0)
    p.add_argument("--locale", default="en")
    p.add_argument("--currency", default="USD")
    p.add_argument(
        "--profile-slug",
        dest="profile_slug",
        default=None,
        help=f"Tabby profile slug (default: $PROFILE_SLUG or {DEFAULT_PROFILE}).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = asyncio.run(
            execute(
                place_id=args.place_id,
                query=args.query,
                checkin=args.checkin,
                checkout=args.checkout,
                adults=args.adults,
                children=args.children,
                infants=args.infants,
                pets=args.pets,
                locale=args.locale,
                currency=args.currency,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"search_listings failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
