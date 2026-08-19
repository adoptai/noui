# API Reference: Volaris Search

## Endpoint

**POST** `https://apigw.volaris.com/prod/api/v3/availability/search`

Returns available flights for a given route and date with pricing, schedules, and fare details.

## Authentication

Two-step via CDP browser session:
1. **DotRez Session JWT** — Automatically obtained by the Angular app on page load via `GET /prod/api/v1/session`. Stored in `sessionStorage.UserToken`. Expires after 15 minutes idle; the skill refreshes it if near expiry.
2. **AWS WAF** — Direct Python HTTP calls return 406. All requests must be made from inside a real Chrome browser via CDP fetch (`mode: "cors"`, no credentials).

## Request

**Content-Type:** `application/json`

**Headers:**
- `Authorization: <DotRez JWT>` (from sessionStorage)
- `Flow: MBS`
- `Frontend: WEB`

**Body:**

```json
{
  "passengers": {"types": [{"type": "ADT", "count": 1}]},
  "criteria": [
    {
      "stations": {
        "originStationCodes": ["MEX"],
        "destinationStationCodes": ["GDL"]
      },
      "dates": {"beginDate": "Thu, Aug 20, 2026"},
      "filters": {
        "fareTypes": ["R"],
        "maxConnections": 20,
        "bundleControlFilter": 2
      }
    }
  ],
  "codes": {"currencyCode": "MXN", "promotionCode": ""},
  "taxesAndFees": 2,
  "shouldIncludeSoldOut": true,
  "shouldIncludeTua": true
}
```

**Note:** `beginDate` uses `"Weekday, Mon DD, YYYY"` format (e.g. `"Thu, Aug 20, 2026"`).

## Response

```json
{
  "results": [
    {
      "trips": [
        {
          "journeysAvailableByMarket": {
            "MEX|GDL": [
              {
                "flightType": 1,
                "stops": 0,
                "designator": {
                  "destination": "GDL",
                  "origin": "MEX",
                  "arrival": "2026-08-20T09:28:00",
                  "departure": "2026-08-20T08:10:00"
                },
                "journeyKey": "WTR_IDE0OH4g...",
                "segments": [
                  {
                    "flightDesignator": {
                      "carrierCode": "Y4",
                      "flightIdentifier": {"flightNumber": 202}
                    },
                    "designator": {
                      "origin": "MEX",
                      "destination": "GDL",
                      "departure": "2026-08-20T08:10:00",
                      "arrival": "2026-08-20T09:28:00"
                    }
                  }
                ],
                "fareAvailabilityKeys": ["MH5Ofn5ZNH5OTFNTMn4..."]
              }
            ]
          }
        }
      ]
    }
  ],
  "faresAvailable": {
    "MH5Ofn5ZNH5OTFNTMn4...": {
      "totals": {
        "fareTotal": 934.0,
        "revenueTotal": 607.0,
        "publishedTotal": 608.0,
        "discountedTotal": 607.0
      },
      "fareAvailabilityKey": "MH5Ofn5ZNH5OTFNTMn4...",
      "fares": [
        {
          "fareBasisCode": "NLSS2",
          "classOfService": "N",
          "fareClass": "R"
        }
      ]
    }
  },
  "currencyCode": "MXN",
  "includeTaxesAndFees": true
}
```

| Field | Description |
|---|---|
| `results[0].trips[0].journeysAvailableByMarket` | Map from route key (e.g. `"MEX\|GDL"`) to list of flights |
| `designator.departure` / `.arrival` | Local departure/arrival times (ISO 8601, no timezone) |
| `designator.origin` / `.destination` | IATA airport codes |
| `stops` | Number of stops (0 = nonstop) |
| `segments[].flightDesignator.carrierCode` | Airline IATA code (`Y4` = Volaris) |
| `segments[].flightDesignator.flightIdentifier.flightNumber` | Flight number |
| `fareAvailabilityKeys` | Links this flight to fare details in `faresAvailable` |
| `faresAvailable[key].totals.discountedTotal` | Total fare including taxes in requested currency |
| `currencyCode` | Currency of all fare amounts |
