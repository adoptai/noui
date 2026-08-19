# API Reference: AirAsia Search

## Endpoint

**POST** `https://flights.airasia.com/web/fp/search/flights/v5/aggregated-results`

Returns available flights for a given route and date with pricing, schedules, and airline details.

## Authentication

Two-step:
1. **Anonymous JWT** — POST `https://flights.airasia.com/fp/authentication/auth/login` with hardcoded web-client credentials (`flightsweb`/`6x6jF7bYrrkVqV2Y`) and `channel_hash` header. Returns `{"jwt": "...", "refreshToken": "..."}`.
2. **Cloudflare Bot Management** — `__cf_bm` cookie set during browser session. All requests must be made from within a real Chrome browser via CDP (`credentials: "omit"`, `mode: "cors"`).

## Request

**Content-Type:** `application/json`

**Headers:**
- `Authorization: Bearer <jwt>` (from login step)
- `channel_hash: c5e9028b4295dcf4d7c239af8231823b520c3cc15b99ab04cde71d0ab18d65bc`
- `User-Type: ANONYMOUS`

**Query Parameters:**
```
page=1
include_list=searchResults,currency,content,featureFlags,locale,vouchers,upsellSnap,upsellFlatbed,upsellPremiumFlatBed
airlineProfile=d,v,g,k
type=paired
isPromoMessagesByCode=true
isOriginCity=false
isDestinationCity=false
uce=true
```

**Body:**

```json
{
  "consumerId": "Website",
  "flightJourney": {
    "journeyType": "O",
    "journeyDetails": [
      {
        "origin": "KUL",
        "destination": "SIN",
        "departDate": "21/08/2026",
        "returnDate": null
      }
    ],
    "passengers": {"adult": 1, "child": 0, "infant": 0}
  },
  "searchContext": {
    "promocode": "",
    "filters": {
      "cabin": {"cabinClass": "ECONOMY", "applyMixedClasses": false},
      "stops": {"stopType": "ANY", "allowOvernight": false},
      "duration": {"maxTravelTimeInHrs": 59, "maxStopoverTimeInHrs": 25, "minStopoverTimeInHrs": 0},
      "carriers": {"allowAllCarriers": true, "onlyAllowedCarriers": [], "excludedCarriers": []},
      "departAirports": {"allowAllAirports": true, "allowedDepartAirports": []},
      "returnAirports": {"allowAllAirports": true, "allowedReturnAirports": []},
      "journey": "O"
    }
  },
  "userContext": {"currency": "USD", "geoId": "CN", "locale": "en-gb", "platform": "web"},
  "ssoDetails": {
    "accessToken": "<anon-token>",
    "refreshToken": "<anon-refresh-token>",
    "userId": "00000000-0000-0000-0000-000000000000"
  },
  "selectedDepartFlight": null
}
```

**Note:** `departDate` uses `DD/MM/YYYY` format (not ISO 8601).

## Response

```json
{
  "searchResults": {
    "trips": [
      {
        "flightsList": [
          {
            "fare": {"adults": 43.707, "children": 0, "infants": 0},
            "currencyCode": "USD",
            "airlineProfile": "d",
            "fareTypeCategory": "Economy",
            "isSoldOut": false,
            "flightDetails": {
              "designator": {
                "departureStation": "KUL",
                "arrivalStation": "SIN",
                "departureTime": "2026-08-21T07:10:00.000Z",
                "arrivalTime": "2026-08-21T08:40:00.000Z"
              },
              "segments": [
                {
                  "airline": "AK",
                  "marketingCarrierCode": "AK",
                  "marketingFlightNo": "720",
                  "fareClass": "Economy"
                }
              ]
            }
          }
        ]
      }
    ]
  }
}
```

| Field | Description |
|---|---|
| `flightsList` | Array of available flights |
| `fare.adults` | Base fare per adult |
| `currencyCode` | Currency of the fare |
| `flightDetails.designator.departureTime` | Departure time in ISO 8601 (local time) |
| `flightDetails.designator.arrivalTime` | Arrival time in ISO 8601 (local time) |
| `flightDetails.segments[].airline` | Airline IATA code (`AK`=AirAsia MY, `D7`=AirAsia X, `TR`=Scoot) |
| `flightDetails.segments[].marketingFlightNo` | Flight number |
| `isSoldOut` | `true` if no seats available |
