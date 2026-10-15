# API Reference: Finnair Search

## Endpoint

**POST** `https://api.finnair.com/d/fcom/offers-prod/current/api/airBounds`

Search Finnair flights for a route and date. Returns full flight options with schedules, aircraft, and fares.

## Authentication

Requires Akamai bot protection cookies from an active Tabby browser session on `www.finnair.com`.

## Request

**Content-Type:** `application/json`

**Required headers:** `X-Client-Id: FCOM`, `X-Session-Id` (UUID, generated per request)

**Body:**

```json
{
  "locale": "en_US",
  "cabin": "MIXED",
  "travelers": {
    "adults": 1,
    "children": 0,
    "c15s": 0,
    "infants": 0
  },
  "itineraries": [
    {
      "directFlights": false,
      "departureLocationCode": "HEL",
      "destinationLocationCode": "LON",
      "departureDate": "2026-08-21",
      "isRequestedBound": true
    }
  ]
}
```

## Response

```json
{
  "airlines": {
    "AY": { "name": "Finnair" }
  },
  "boundGroups": [
    {
      "cheapestPrice": "127.60",
      "details": {
        "arrival": { "dateTime": "2026-08-21T20:40:00+01:00", "locationCode": "LHR", "terminal": "3" },
        "departure": { "dateTime": "2026-08-21T19:30:00+03:00", "locationCode": "HEL" },
        "duration": { "hours": 3, "minutes": 10 },
        "flights": 1,
        "itinerary": [
          {
            "aircraft": { "code": "32B", "name": "Airbus A321 (Sharklets)" },
            "flightNumber": "AY1339"
          }
        ]
      },
      "fareSummaries": [...]
    }
  ]
}
```

| Field | Description |
|---|---|
| `boundGroups` | List of flight options (direct and connecting) |
| `cheapestPrice` | Lowest available fare for this flight option |
| `details.itinerary` | Segment-by-segment breakdown with flight numbers and aircraft |
| `fareSummaries` | Price breakdown by fare class (Economy, Business, etc.) |
