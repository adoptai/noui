#!/usr/bin/env python3
"""Operation: search_hotels

Searches for hotels on Expedia via Tabby's execute endpoints.

Uses POST /execute/fetch for API calls (typeahead) and POST /execute/browser
for page navigation + HAR capture of search results. Replaces the previous
CDP WebSocket approach.

Skill-variant entry point:
    python operations/search_hotels.py --destination Paris --check-in 2026-05-01 \
        --check-out 2026-05-04 --guests 2 --rooms 1

Prints JSON to stdout on success; exits non-zero with a diagnostic on stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from noui_runtime.execute import execute_browser, execute_fetch

_TYPEAHEAD_PARAMS = {
    "locale": "en_US",
    "siteid": "1",
    "dest": "true",
    "regiontype": "2000",
    "features": "ta_hierarchy|typeahead_v3",
    "maxresults": "1",
}


async def _resolve_destination(profile_id: str, destination: str) -> dict:
    query = urllib.parse.quote(destination)
    params = urllib.parse.urlencode(_TYPEAHEAD_PARAMS)
    url = f"https://www.expedia.com/api/v4/typeahead/{query}?{params}"

    data = await execute_fetch(profile_id, url)

    results = data.get("sr", data) if isinstance(data, dict) else data
    if not results or not isinstance(results, list):
        return {"name": destination}

    top = results[0]
    region_id = str(top.get("gaiaId") or top.get("regionId") or top.get("id") or "")
    lat = top.get("lat") or (top.get("coordinates") or {}).get("lat")
    lon = top.get("lon") or (top.get("coordinates") or {}).get("long")
    resolved_name = (
        top.get("fullName")
        or top.get("regionNames", {}).get("fullName")
        or top.get("name")
        or destination
    )
    return {
        "name": resolved_name,
        "region_id": region_id,
        "lat_long": f"{lat},{lon}" if lat and lon else None,
    }


def _extract_listings_from_har(har_data: dict) -> list[dict]:
    """Extract hotel listings from HAR-captured API responses.

    Searches for JSON responses containing property/listing arrays returned
    by Expedia's SPA during the Hotel-Search page load.
    """
    entries = har_data.get("har", {}).get("log", {}).get("entries", [])
    for entry in entries:
        resp = entry.get("response", {})
        content = resp.get("content", {})
        if resp.get("status") != 200:
            continue
        body_text = content.get("text", "")
        if not body_text or len(body_text) < 500:
            continue
        try:
            body = json.loads(body_text)
        except (ValueError, TypeError):
            continue
        listings = _find_property_array(body)
        if listings:
            return listings
    return []


def _find_property_array(data: Any, depth: int = 0) -> list[dict] | None:
    """Recursively search a parsed API response for a hotel property array."""
    if depth > 8:
        return None

    if isinstance(data, list) and len(data) >= 3:
        if all(_looks_like_property(item) for item in data[:3]):
            return [_normalize_property(item) for item in data[:25]]

    if isinstance(data, dict):
        for key in (
            "properties",
            "propertySearchListings",
            "listings",
            "results",
            "searchResults",
            "hotels",
            "items",
        ):
            if key in data:
                found = _find_property_array(data[key], depth + 1)
                if found:
                    return found
        for val in data.values():
            if isinstance(val, (dict, list)):
                found = _find_property_array(val, depth + 1)
                if found:
                    return found
    return None


_PROPERTY_SIGNALS = {"name", "propertyName", "hotelName", "title"}
_PRICE_SIGNALS = {"price", "ratePlan", "rate", "lead", "displayPrice", "priceMetadata"}


def _looks_like_property(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    keys = set(item.keys())
    has_name = bool(keys & _PROPERTY_SIGNALS) or any(
        isinstance(v, dict) and set(v.keys()) & _PROPERTY_SIGNALS for v in item.values()
    )
    has_price = bool(keys & _PRICE_SIGNALS) or any(
        isinstance(v, dict) and set(v.keys()) & _PRICE_SIGNALS for v in item.values()
    )
    return has_name and has_price


def _deep_get(d: dict, *paths: str) -> Any:
    """Walk nested dicts for the first matching dotted path."""
    for path in paths:
        cur: Any = d
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
                break
        if cur is not None:
            return cur
    return None


def _normalize_property(item: dict) -> dict:
    name = (
        _deep_get(
            item,
            "name",
            "propertyName",
            "hotelName",
            "title",
            "header.headline",
            "cardHeader.headline",
        )
        or ""
    )

    nightly = (
        _deep_get(
            item,
            "price.lead.formatted",
            "price.displayPrice",
            "ratePlan.price.current",
            "price.options.0.formattedDisplayPrice",
            "mapMarker.label",
        )
        or ""
    )
    total = (
        _deep_get(
            item,
            "price.strikeOut.formatted",
            "price.displayTotalPrice",
            "ratePlan.price.total",
            "price.total.formatted",
        )
        or ""
    )

    rating = _deep_get(
        item,
        "reviews.score",
        "guestReviews.rating",
        "star",
        "starRating",
        "reviews.overallScore",
    )
    rating_str = f"{rating}/10" if rating else ""

    rating_label = (
        _deep_get(
            item,
            "reviews.qualitativeScoreText",
            "guestReviews.qualitativeScoreText",
            "reviews.qualitative",
        )
        or ""
    )

    reviews_count = _deep_get(
        item,
        "reviews.total",
        "guestReviews.total",
        "reviews.count",
        "reviews.totalCount",
    )
    reviews_str = str(reviews_count) if reviews_count else ""

    url = (
        _deep_get(
            item,
            "propertyUrl",
            "url",
            "deeplink",
            "cardLink.resource.value",
        )
        or ""
    )
    if url and not url.startswith("http"):
        url = f"https://www.expedia.com{url}"

    refundable = bool(
        _deep_get(
            item,
            "freeCancellation",
            "price.freeCancellation",
            "offerBadge.secondary",
        )
    )

    return {
        "name": str(name),
        "price_per_night": str(nightly),
        "price_total": str(total),
        "rating": rating_str,
        "rating_label": str(rating_label),
        "reviews_count": reviews_str,
        "refundable": refundable,
        "url": str(url),
    }


_FILTER_HEADINGS = {
    "total price",
    "popular filters",
    "star rating",
    "guest rating",
    "property amenities",
    "room amenities",
    "room views",
    "payment type",
    "property cancellation options",
    "property type",
    "property brand",
    "meal plans available",
    "traveler experience",
    "from",
}


def _extract_listings_from_summary(summary: dict) -> list[dict]:
    """Fallback: extract hotel names from get_page_summary links."""
    listings = []
    for link in summary.get("links", []):
        text = (link.get("text") or "").strip()
        href = link.get("href", "")
        if not text or not href:
            continue
        if "Hotel-Information" not in href and "hotel-deals" not in href.lower():
            continue
        if text.startswith("Photo gallery") or text.lower() in _FILTER_HEADINGS:
            continue
        for prefix in ("Opens ", "More information about "):
            if text.startswith(prefix):
                text = text[len(prefix) :]
                break
        for suffix in (", opens in a new tab", " in new tab"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                break
        listings.append(
            {
                "name": text,
                "price_per_night": "",
                "price_total": "",
                "rating": "",
                "rating_label": "",
                "reviews_count": "",
                "refundable": False,
                "url": href,
            }
        )
    return listings[:25]


async def _search_properties(
    profile_id: str,
    region_id: str,
    destination_name: str,
    check_in: str,
    check_out: str,
    guests: int,
    rooms: int,
) -> dict:
    params = {
        "destination": destination_name,
        "regionId": region_id,
        "d1": check_in,
        "startDate": check_in,
        "d2": check_out,
        "endDate": check_out,
        "adults": str(guests),
        "rooms": str(rooms),
        "sort": "RECOMMENDED",
    }
    search_url = "https://www.expedia.com/Hotel-Search?" + urllib.parse.urlencode(params)

    await execute_browser(profile_id, "har_start")

    await execute_browser(profile_id, "navigate", {"url": search_url}, timeout_ms=60_000)

    try:
        await execute_browser(
            profile_id,
            "wait_for_selector",
            {"selector": '[data-stid="lodging-card-responsive"]'},
            timeout_ms=15_000,
        )
    except RuntimeError:
        pass

    har_data = await execute_browser(profile_id, "har_stop")

    listings = _extract_listings_from_har(har_data)
    if listings:
        return {"total_on_page": len(listings), "results": listings}

    summary = await execute_browser(profile_id, "get_page_summary")
    fallback = _extract_listings_from_summary(summary)
    return {"total_on_page": len(fallback), "results": fallback}


async def execute(
    destination: str,
    check_in: str,
    check_out: str,
    guests: int = 2,
    rooms: int = 1,
    profile_slug: str | None = None,
) -> dict:
    """Search for hotels on Expedia using the Tabby execute endpoints."""
    profile_id = profile_slug or os.environ.get("PROFILE_SLUG") or "expedia"

    dest_info = await _resolve_destination(profile_id, destination)
    region_id = dest_info.get("region_id", "")

    if not region_id:
        return {
            "error": f"Could not resolve destination: {destination}",
            "destination": dest_info,
        }

    search_data = await _search_properties(
        profile_id,
        region_id,
        dest_info["name"],
        check_in,
        check_out,
        guests,
        rooms,
    )

    params = {
        "destination": dest_info["name"],
        "regionId": region_id,
        "d1": check_in,
        "d2": check_out,
        "adults": str(guests),
        "rooms": str(rooms),
    }
    search_url = "https://www.expedia.com/Hotel-Search?" + urllib.parse.urlencode(params)

    return {
        "search_url": search_url,
        "destination": dest_info["name"],
        "region_id": region_id,
        "check_in": check_in,
        "check_out": check_out,
        "guests": guests,
        "rooms": rooms,
        "listings_count": search_data.get("total_on_page", 0),
        "listings": search_data.get("results", []),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_hotels",
        description="Search Expedia for hotels by destination and dates via Tabby execute endpoints.",
    )
    parser.add_argument("--destination", required=True, help="City or place name (e.g. 'Paris').")
    parser.add_argument("--check-in", dest="check_in", required=True, help="YYYY-MM-DD.")
    parser.add_argument("--check-out", dest="check_out", required=True, help="YYYY-MM-DD.")
    parser.add_argument("--guests", type=int, default=2, help="Adult guests (default: 2).")
    parser.add_argument("--rooms", type=int, default=1, help="Number of rooms (default: 1).")
    parser.add_argument(
        "--profile-slug",
        dest="profile_slug",
        default=None,
        help="Tabby profile slug (default: $PROFILE_SLUG or 'expedia').",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = asyncio.run(
            execute(
                destination=args.destination,
                check_in=args.check_in,
                check_out=args.check_out,
                guests=args.guests,
                rooms=args.rooms,
                profile_slug=args.profile_slug,
            )
        )
    except Exception as exc:
        print(f"search_hotels failed: {exc}", file=sys.stderr)
        return 1

    if isinstance(result, dict) and "error" in result:
        print(json.dumps(result, indent=2))
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
