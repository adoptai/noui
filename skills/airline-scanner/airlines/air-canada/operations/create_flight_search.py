#!/usr/bin/env python3
"""Skill operation: create_flight_search
Method: POST (GraphQL)
Path: /graphql (AWS AppSync via Cognito unauthenticated identity)

Fetches Cognito temporary credentials from the public unauthenticated identity
pool, signs the AppSync GraphQL request with AWS Signature V4, and returns
available Air Canada flights for a given route and date.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import hmac
import json
import sys
import urllib.error
import urllib.request
from typing import Any

COGNITO_IDENTITY_POOL_ID = "us-east-2:5c802fef-a936-4d71-beb4-54ec4e10495c"
COGNITO_ENDPOINT = "https://cognito-identity.us-east-2.amazonaws.com/"
APPSYNC_URL = "https://ak-lfs-appsync-api-ecom.digital.aircanada.com/graphql"
# The actual AppSync service hostname used for AWS4 signing (behind CloudFront)
APPSYNC_SIGNING_HOST = "lfs.ac-aco.cloud.aircanada.com"
AWS_REGION = "ca-central-1"
AWS_SERVICE = "appsync"

_GRAPHQL_QUERY = """
query getFlightRecommendations($input: SearchParametersInput!) {
  getFlightRecommendations(searchParameters: $input) {
    responseData {
      boundSummary {
        recommendationsSummary {
          uniqueRecommendations
          minimumTravelTime
          cabinSummary { cabinCode cheapestFarePerCabin }
        }
      }
      recommendations {
        boundDetails {
          arrivalDateTime
          departureDateTime
          duration { hours minutes }
          numberOfStops
          offerType
          operatingDisclosureAirlines {
            operatingAirlines { code name }
          }
        }
        fareDetails {
          cabin {
            cabinCode
            cheapestFare
            offers {
              id
              prices {
                priceSummary {
                  totalFare
                  totalFareRounded
                  baseFare
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _hmac_sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _get_cognito_credentials() -> dict[str, str]:
    def _post(target: str, body: dict) -> dict:
        req = urllib.request.Request(
            COGNITO_ENDPOINT,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Content-Type": "application/x-amz-json-1.1",
                "X-Amz-Target": target,
                "X-Amz-User-Agent": "aws-amplify/5.3.0 framework/0",
                "Origin": "https://www.aircanada.com",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/148.0.0.0 Safari/537.36"
                ),
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    identity_id = _post(
        "AWSCognitoIdentityService.GetId",
        {"IdentityPoolId": COGNITO_IDENTITY_POOL_ID},
    )["IdentityId"]
    return _post(
        "AWSCognitoIdentityService.GetCredentialsForIdentity",
        {"IdentityId": identity_id},
    )["Credentials"]


def _aws4_auth_headers(creds: dict[str, str], body_str: str) -> dict[str, str]:
    now = datetime.datetime.now(datetime.UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    payload_hash = hashlib.sha256(body_str.encode()).hexdigest()
    canonical_headers = (
        f"content-type:application/json; charset=UTF-8\n"
        f"host:{APPSYNC_SIGNING_HOST}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-security-token:{creds['SessionToken']}\n"
    )
    signed_headers = "content-type;host;x-amz-date;x-amz-security-token"
    canonical_request = "\n".join(
        ["POST", "/graphql", "", canonical_headers, signed_headers, payload_hash]
    )
    credential_scope = f"{date_stamp}/{AWS_REGION}/{AWS_SERVICE}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    k = _hmac_sign(
        _hmac_sign(
            _hmac_sign(
                _hmac_sign(("AWS4" + creds["SecretKey"]).encode(), date_stamp),
                AWS_REGION,
            ),
            AWS_SERVICE,
        ),
        "aws4_request",
    )
    sig = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={creds['AccessKeyId']}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={sig}"
    )
    return {
        "Authorization": auth,
        "X-Amz-Date": amz_date,
        "X-Amz-Security-Token": creds["SessionToken"],
    }


def execute(
    origin: str = "YYZ",
    destination: str = "YVR",
    date: str = "2026-08-20",
    adults: int = 1,
    children: int = 0,
    youth: int = 0,
    infants_on_lap: int = 0,
    currency: str = "CAD",
) -> dict[str, Any]:
    """Search Air Canada for available flights on a given route and date.

    Args:
        origin: Departure IATA airport code (e.g. "YYZ", "YVR", "YYC").
        destination: Arrival IATA airport code (e.g. "YVR", "YYZ", "YYC").
        date: Departure date in YYYY-MM-DD format.
        adults: Number of adult travelers.
        children: Number of child travelers (2–11).
        youth: Number of youth travelers (12–17).
        infants_on_lap: Number of infants on lap.
        currency: Unused; Air Canada returns CAD by default.
    """
    creds = _get_cognito_credentials()

    variables: dict[str, Any] = {
        "input": {
            "source": "Home",
            "channel": "ARW",
            "country": "CA",
            "language": "EN",
            "countryOfResidence": "CA",
            "tripType": "OneWay",
            "itineraries": [
                {
                    "originLocationCode": origin,
                    "originQualifier": "Airport",
                    "destinationLocationCode": destination,
                    "destinationQualifier": "Airport",
                    "departureDate": date,
                    "isRequestedBound": True,
                }
            ],
            "passengers": {
                "adult": adults,
                "child": children,
                "youth": youth,
                "infantOnLap": infants_on_lap,
                "infantOnSeat": 0,
            },
            "isBasicUpsellRequested": False,
            "isLfcTriggered": False,
        }
    }

    body_str = json.dumps({"query": _GRAPHQL_QUERY, "variables": variables})
    auth_headers = _aws4_auth_headers(creds, body_str)

    req = urllib.request.Request(
        APPSYNC_URL,
        data=body_str.encode(),
        method="POST",
        headers={
            **auth_headers,
            "Content-Type": "application/json; charset=UTF-8",
            "Origin": "https://www.aircanada.com",
            "Referer": "https://www.aircanada.com/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            "X-Amz-User-Agent": "aws-amplify/5.3.0 api/1 framework/3",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_flight_search",
        description="Search Air Canada for available flights on a given route and date.",
    )
    parser.add_argument("--origin", required=True, help='Departure IATA code, e.g. "YYZ".')
    parser.add_argument("--destination", required=True, help='Arrival IATA code, e.g. "YVR".')
    parser.add_argument("--date", required=True, help="Departure date in YYYY-MM-DD format.")
    parser.add_argument("--adults", type=int, default=1, help="Number of adult travelers.")
    parser.add_argument("--children", type=int, default=0, help="Number of child travelers (2-11).")
    parser.add_argument("--youth", type=int, default=0, help="Number of youth travelers (12-17).")
    parser.add_argument(
        "--infants-on-lap",
        type=int,
        default=0,
        dest="infants_on_lap",
        help="Number of infants on lap.",
    )
    parser.add_argument("--currency", default="CAD", help="Currency code (default: CAD).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = execute(
            origin=args.origin,
            destination=args.destination,
            date=args.date,
            adults=args.adults,
            children=args.children,
            youth=args.youth,
            infants_on_lap=args.infants_on_lap,
            currency=args.currency,
        )
    except Exception as exc:
        print(f"create_flight_search failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "errors" in result and result.get("data") is None:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
