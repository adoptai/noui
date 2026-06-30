# API Reference: Southwest Search

## Endpoint

**POST** `https://www.southwest.com/api/air-booking/v1/air-booking/page/air/booking/shopping`

Search Southwest flights for a route and date. Returns available flights with fare breakdowns.

## Authentication

Requires Akamai bot protection cookies + sensor headers from an active Tabby session. Akamai headers (`EE30zvQLWf-*`) are added automatically when running `fetch()` inside the browser.

**Static API key:** `X-API-Key: l7xx944d175ea25f4b9c903a583ea82a1c4c`

## Request

**Content-Type:** `application/json`

**Body:**

```json
{
  "adultPassengersCount": "1",
  "adultsCount": "1",
  "departureDate": "2026-08-21",
  "departureTimeOfDay": "ALL_DAY",
  "destinationAirportCode": "LGA",
  "fareType": "USD",
  "int": "HOMEQBOMAIR",
  "originationAirportCode": "LAX",
  "passengerType": "ADULT",
  "promoCode": "",
  "returnDate": "",
  "returnTimeOfDay": "ALL_DAY",
  "tripType": "oneway",
  "application": "air-booking",
  "site": "southwest"
}
```

## Response

```json
{
  "data": {
    "searchResults": {
      "fareSummary": [
        { "fareFamily": "WGA", "minimumFare": { "currencyCode": "USD", "value": "201.80" } },
        { "fareFamily": "PLU", "minimumFare": { "currencyCode": "USD", "value": "256.80" } },
        { "fareFamily": "ANY", "minimumFare": { "currencyCode": "USD", "value": "336.80" } },
        { "fareFamily": "BUS", "minimumFare": { "currencyCode": "USD", "value": "396.81" } }
      ],
      "airProducts": [...]
    }
  }
}
```

| Field | Description |
|---|---|
| `fareSummary` | Minimum price per fare class across all flights |
| `airProducts` | Individual flight options with stops, duration, and per-flight fares |
| Fare families | `WGA` = Wanna Get Away, `PLU` = Plus, `ANY` = Anytime, `BUS` = Business Select |
