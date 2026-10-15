# IndiGo Flight Search API

## Endpoint

```
POST https://api-prod-flight-skyplus6e.goindigo.in/v2/flight/search
```

## Auth

- `Authorization: <JWT>` — DotRez anonymous session JWT, captured via CDP Fetch
  interception of `PUT https://api-prod-session-skyplus6e.goindigo.in/v1/token/refresh`.
  JWT payload: `{"sub":"ibe","jti":"<uuid>","iss":"dotREZ API"}` — no exp claim.
- `User_key: 31e90be8fff2f5e2eea242c225f21b1a` — static per-app API key.
- Akamai bot protection (`bm_mi`, `AKA_A2` cookies) — only bypassable from inside
  Tabby's browser session (CloakBrowser).

## Request Body

```json
{
  "codes": {"currency": "INR", "promotionCode": ""},
  "criteria": [{
    "dates": {"beginDate": "2026-08-20"},
    "flightFilters": {"type": "All"},
    "stations": {
      "originStationCodes": ["DEL"],
      "destinationStationCodes": ["BOM"]
    }
  }],
  "passengers": {
    "residentCountry": "IN",
    "types": [{"count": 1, "discountCode": "", "type": "ADT"}]
  },
  "taxesAndFees": "TaxesAndFees",
  "tripCriteria": "oneWay",
  "isRedeemTransaction": false
}
```

## Response (excerpt)

```json
{
  "data": {
    "trips": [{
      "origin": "DEL",
      "destination": "BOM",
      "journeysAvailable": [{
        "segKey": "HDO5096NMI",
        "flightType": "NonStop",
        "journeyDetails": [{
          "departureStation": "DEL",
          "arrivalStation": "BOM",
          "std": "2026-08-20T06:00:00",
          "sta": "2026-08-20T08:10:00",
          "flightNumber": "6E 5096"
        }],
        "fareOptions": [{
          "fareType": "Saver",
          "totalFare": 3499.0,
          "currency": "INR"
        }]
      }]
    }]
  }
}
```

## Key Response Fields

| Field | Description |
|---|---|
| `data.trips[].journeysAvailable[]` | List of available flights |
| `.flightType` | `NonStop` or `Connecting` |
| `.journeyDetails[].std` | Scheduled departure time (ISO 8601) |
| `.journeyDetails[].sta` | Scheduled arrival time (ISO 8601) |
| `.journeyDetails[].flightNumber` | Flight number (e.g. `6E 5096`) |
| `.fareOptions[].fareType` | Fare class (Saver, Flexi, Super 6E) |
| `.fareOptions[].totalFare` | Total fare including taxes |
| `.fareOptions[].currency` | Currency code |
