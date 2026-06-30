# British Airways Flight Search API

## Endpoint

```
GET https://www.britishairways.com/nx/b/bff/offer-flight/v0/oneway/outbound
  ?from=LHR&to=JFK&departureDate=2026-08-20
  &adults=1&youngAdults=0&children=0&infants=0
  &page=1&maxResult=100&travelClass=economy
```

No auth, no bot protection bypass needed. Fresh UUIDs for correlation headers only.

## Key Headers (all static except UUIDs)

| Header | Value |
|---|---|
| `x-ba-application-name` | `airselect` |
| `x-ba-client-name` | `airselect` |
| `x-ba-market` | `us` |
| `x-ba-language` | `en` |
| `x-ba-channel` | `WEB` |
| `x-amzn-waf-ba-rule` | `EMPTY` |
| `x-ba-action-name` | `search-flights-get-flights-oneway-outbound` |
| `x-ba-interaction-id` etc. | Fresh UUID v4 per request |

## Key Response Fields

| Field | Description |
|---|---|
| `result.matchingBounds[]` | List of available flights |
| `.departureAirport.code` | Departure IATA code |
| `.arrivalAirport.code` | Arrival IATA code |
| `.departureTime` | Departure datetime (ISO 8601 with offset) |
| `.arrivalTime` | Arrival datetime |
| `.duration` | Duration in minutes |
| `.flightNumber` | BA flight number |
| `.stops` | Number of stops |
| `.cabin.cabinCode` | Cabin code (M=Economy, W=Premium Economy, J=Business, F=First) |
| `.lowestFare.amount` | Lowest fare amount |
| `.lowestFare.currency` | Currency code |
