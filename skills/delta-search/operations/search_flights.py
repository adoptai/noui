#!/usr/bin/env python3
"""Search Delta Air Lines for available one-way flights on a given route and date.

Delta uses a GraphQL endpoint at offer-api-prd.delta.com with Authorization: GUEST
(no real auth needed), but Akamai blocks direct Python calls. Requests run inside
Tabby's www.delta.com browser session via cdp_fetch (cross-origin to offer-api-prd
works because Akamai cookies are valid from the browser).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from noui_runtime.cdp import cdp_fetch, find_page  # noqa: E402

CDP_HOST_MATCH = "delta.com"
_GQL_URL = "https://offer-api-prd.delta.com/prd/rm-offer-gql"

_GQL_QUERY = """query ($offerSearchCriteria: OfferSearchCriteriaInput!) {
  gqlSearchOffers(offerSearchCriteria: $offerSearchCriteria) {
    offerResponseId
    gqlOffersSets {
      trips {
        tripId
        scheduledDepartureLocalTs
        scheduledArrivalLocalTs
        originAirportCode
        destinationAirportCode
        stopCnt
        flightSegment {
          aircraftTypeCode
          destinationAirportCode
          marketingCarrier { carrierCode carrierNum }
          originAirportCode
          scheduledArrivalLocalTs
          scheduledDepartureLocalTs
        }
        totalTripTime { hourCnt minuteCnt }
      }
      offers {
        offerId
        soldOut
        additionalOfferProperties { fareType dominantSegmentBrandId }
        offerItems {
          retailItems {
            retailItemMetaData {
              fareInformation {
                farePrice {
                  totalFarePrice {
                    currencyEquivalentPrice { roundedCurrencyAmt formattedCurrencyAmt }
                  }
                }
              }
            }
          }
        }
      }
    }
    offerDataList {
      responseProperties { pageResultCnt resultsPageNum }
    }
  }
}"""

_CABIN_BRAND = {"ECONOMY": "MAIN", "BUSINESS": "FIRST", "FIRST": "FIRST"}


async def execute(
    origin: str = "ATL",
    destination: str = "JFK",
    date: str = "2026-08-20",
    cabin_class: str = "ECONOMY",
    adults: int = 1,
) -> dict[str, Any]:
    """Search Delta Air Lines for available one-way flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "ATL", "JFK", "LAX").
        destination: Arrival IATA airport or city code (e.g. "JFK", "NYC", "LAX").
        date: Departure date in YYYY-MM-DD format.
        cabin_class: Cabin class — ECONOMY, BUSINESS, or FIRST.
        adults: Number of adult travelers.
    """
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile delta-search"
        )

    brand = _CABIN_BRAND.get(cabin_class.upper(), "MAIN")
    txn_id = f"{uuid.uuid4()}_{int(time.time() * 1000)}"

    body = {
        "variables": {
            "offerSearchCriteria": {
                "productGroups": [{"productCategoryCode": "FLIGHTS"}],
                "offersCriteria": {
                    "resultsPageNum": 1,
                    "resultsPerRequestNum": 20,
                    "preferences": {
                        "refundableOnly": False,
                        "showGlobalRegionalUpgradeCertificate": True,
                        "nonStopOnly": False,
                        "excludeBrandTypes": [],
                    },
                    "pricingCriteria": {"priceableIn": ["CURRENCY"]},
                    "flightRequestCriteria": {
                        "currentTripIndexId": "0",
                        "sortableOptionId": None,
                        "selectedOfferId": "",
                        "searchOriginDestination": [
                            {
                                "departureLocalTs": f"{date}T00:00:00",
                                "destinations": [{"airportCode": destination}],
                                "origins": [{"airportCode": origin}],
                            }
                        ],
                        "sortByBrandId": brand,
                        "additionalCriteriaMap": {"rollOutTag": "GBB"},
                    },
                },
                "customers": [
                    {"passengerTypeCode": "ADT", "passengerId": str(i + 1)} for i in range(adults)
                ],
            }
        },
        "query": _GQL_QUERY,
    }

    return await cdp_fetch(
        ws_url,
        _GQL_URL,
        method="POST",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "GUEST",
            "applicationId": "DC",
            "x-app-type": "dcom-shop",
            "Airline": "DL",
            "x-app-route": "search",
            "channelId": "DCOM",
            "TransactionId": txn_id,
            "Origin": "https://www.delta.com",
            "Referer": "https://www.delta.com/",
        },
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_flights",
        description="Search Delta Air Lines for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "ATL".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "JFK".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument(
        "--cabin-class",
        default="ECONOMY",
        choices=["ECONOMY", "BUSINESS", "FIRST"],
        help="Cabin class (default: ECONOMY).",
    )
    parser.add_argument("--adults", type=int, default=1)
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
