# API Reference: EasyJet Search

## Endpoint

**GET** `https://www.easyjet.com/homepage/api/availability`

Search EasyJet flights for a given route and date. Returns lowest available price per date.

## Authentication

None required. The endpoint is publicly accessible. A homepage visit is made first to establish session cookies.

## Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `origin` | string | yes | Departure IATA airport code (e.g. `LGW`) |
| `destination` | string | yes | Arrival IATA airport code (e.g. `AMS`) |
| `currency` | string | no | Currency code for returned prices (default: `GBP`) |
| `isReturn` | string | no | `"true"` for round-trip search (default: `"false"`) |
| `startDate` | string | yes | Outbound date in `YYYY-MM-DD` format |
| `endDate` | string | no | Return date in `YYYY-MM-DD` format (round-trip only) |

## Response

```json
{
  "startDate": "2026-07-15",
  "endDate": "2026-07-15",
  "departureFlights": [
    {
      "date": "2026-07-15",
      "price": 43,
      "lowFare": false
    }
  ],
  "returnFlights": null
}
```

| Field | Description |
|---|---|
| `departureFlights` | Array of available outbound dates with lowest price |
| `returnFlights` | Array of available return dates (null for one-way searches) |
| `price` | Lowest available fare for that date in the requested currency |
| `lowFare` | Whether this date is flagged as a low-fare day |
