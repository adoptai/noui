# Cathay Pacific Flight Search API

## Flow

1. Navigate browser to `https://www.cathaypacific.com/cx/en_US.html`
2. Fill hidden form fields in `#book-trip-flight` and call `form.submit()`
3. Server processes POST to `/wdsibe/IBEFacade`, creates a session, redirects
4. Browser lands on `https://book.cathaypacific.com/CathayPacificV3/dyn/air/booking/owdAvail`
5. Angular renders flight cards; parse DOM text for flight data

## Form Endpoint

```
POST https://www.cathaypacific.com/wdsibe/IBEFacade
Content-Type: application/x-www-form-urlencoded
```

## Form Fields

| Field | Value | Notes |
|---|---|---|
| `ACTION` | `SINGLECITY_SEARCH` | Pre-filled, do not change |
| `ORIGIN` | `HKG` | Departure IATA code |
| `DESTINATION` | `LHR` | Arrival IATA code |
| `DEPARTUREDATE` | `20260820` | YYYYMMDD format |
| `TRIPTYPE` | `O` | One-way |
| `CABINCLASS` | `Y` | Y=Economy, W=Prem Eco, J=Business, F=First |
| `ADULT` | `1` | Number of adults |
| `YOUNGADULT` | `0` | |
| `CHILD` | `0` | |

## Response Structure (parsed from DOM)

```json
{
  "origin": "HKG",
  "destination": "LHR",
  "date": "2026-08-20",
  "cabinClass": "ECONOMY",
  "flightCount": 9,
  "flights": [{
    "departure": "08:05",
    "origin": "HKG",
    "duration": "14h 10m",
    "arrival": "15:15",
    "destination": "LHR",
    "flightNumber": "CX257",
    "connectingFlight": null
  }],
  "datePrices": [{
    "date": "Thu 20 Aug",
    "priceHKD": 7307
  }]
}
```

## Key Response Fields

| Field | Description |
|---|---|
| `flightCount` | Total flights found |
| `flights[].departure` | Departure time (HH:MM) |
| `flights[].arrival` | Arrival time (HH:MM) |
| `flights[].duration` | Total journey duration |
| `flights[].flightNumber` | CX flight number |
| `flights[].connectingFlight` | Codeshare or connecting flight number (null if direct) |
| `datePrices[]` | 7-day fare calendar around the requested date |
| `datePrices[].priceHKD` | Lowest economy fare in HKD including taxes |
