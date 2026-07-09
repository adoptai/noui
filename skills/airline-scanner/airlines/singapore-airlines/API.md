# Singapore Airlines Flight Search API

## Endpoint

```
POST https://www.singaporeair.com/home/getHistogram.form
```

## Auth

- Akamai Bot Manager cookies (`_abck`, `bm_sz`, `bm_sc`, `bm_so`) — set by the
  Akamai bot protection script on first page load. Only bypassable from inside
  Tabby's CloakBrowser session.
- No Authorization header or API key required.

## Request Body (application/json)

```json
{
  "request": {
    "itineraryDetails": {
      "originAirportCode": "SIN",
      "destinationAirportCode": "LHR",
      "departureDate": "2026-08-20"
    },
    "cabinClass": "Y"
  }
}
```

Cabin class codes: `Y` = Economy, `W` = Premium Economy, `J` = Business, `F` = First.

## Response

```json
{
  "histogramResponse": {
    "fares": [
      {
        "tax": "65.20",
        "departureDate": "2026-08-05",
        "totalAmount": "2225.20",
        "fare": "2160.00"
      },
      {
        "tax": "65.20",
        "departureDate": "2026-08-20",
        "totalAmount": "2225.20",
        "fare": "2160.00"
      }
    ]
  }
}
```

Returns a 15-day window of fares centred around the requested date. Currency
is determined by the browser's locale (SGD for SIN-origin, GBP for LHR-origin, etc.).

## Key Response Fields

| Field | Description |
|---|---|
| `fares[].departureDate` | Departure date (YYYY-MM-DD) |
| `fares[].fare` | Base airfare (excludes tax) |
| `fares[].tax` | Tax amount |
| `fares[].totalAmount` | Total price (fare + tax) |

## Limitation

This endpoint returns fare prices only — not individual flight schedules
(flight numbers, departure/arrival times). For schedules, the SIA booking SPA
at `/flightsearch/searchFlight.form` must be loaded via a human-initiated search
session (the form is Akamai-protected and session-based).
