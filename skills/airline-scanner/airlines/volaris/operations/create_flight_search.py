#!/usr/bin/env python3
"""Skill operation: create_flight_search
Method: POST
Path: /prod/api/v3/availability/search

Volaris uses AWS WAF bot detection on their API gateway — direct Python HTTP
calls return 406. Requests must be made from inside Tabby's real Chrome browser
via CDP eval. The DotRez anonymous session JWT is read from the browser's
sessionStorage (set on page load by the Angular app at /prod/api/v1/session).
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

from noui_runtime.cdp import cdp_eval, find_page  # noqa: E402

CDP_HOST_MATCH = "volaris.com"
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
    ws_url = await find_page(CDP_HOST_MATCH)
    if not ws_url:
        raise RuntimeError(
            f"No Tabby page matching {CDP_HOST_MATCH!r}. "
            "Run: tabby session ensure --profile volaris-search"
        )

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

    def _get_token_js() -> str:
        """Return a JS expression (Promise<string>) that resolves to a fresh JWT."""
        return f"""(function() {{
            const raw = sessionStorage.getItem('UserToken');
            const needsRefresh = !raw || (() => {{
                try {{
                    const exp = new Date(JSON.parse(raw).expirationDate).getTime();
                    return exp - Date.now() < 30000;
                }} catch (e) {{ return true; }}
            }})();
            if (needsRefresh) {{
                return fetch({json.dumps(_SESSION_URL)}, {{
                    mode: 'cors',
                    headers: {{'Accept': 'application/json'}}
                }})
                .then(r => r.json())
                .then(sess => {{
                    sessionStorage.setItem('UserToken', JSON.stringify(sess));
                    return sess.token;
                }});
            }}
            return Promise.resolve(JSON.parse(raw).token);
        }})()"""

    search_js = (
        f"({_get_token_js()})"
        f".then(jwt => fetch({json.dumps(_SEARCH_URL)}, {{"
        f"  method: 'POST', mode: 'cors',"
        f"  headers: {{'Content-Type': 'application/json', 'Accept': 'application/json',"
        f"             'Authorization': jwt, 'Flow': 'MBS', 'Frontend': 'WEB'}},"
        f"  body: JSON.stringify({json.dumps(body)})"
        f"}}))"
        f".then(r => r.text().then(t => JSON.stringify({{status: r.status, body: t}})))"
    )
    raw = await cdp_eval(ws_url, search_js)
    status = raw.get("status")
    body_str = raw.get("body", "")
    if not (isinstance(status, int) and 200 <= status < 300):
        raise RuntimeError(f"POST {_SEARCH_URL} -> {status}: {body_str[:300]}")
    return json.loads(body_str) if body_str else {}


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
        print(f"create_flight_search failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and "error" in result:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
