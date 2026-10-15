# Qatar Airways Flight Search API

## Endpoint

```
POST https://www.qatarairways.com/dapi/public/bff/web/flight-search/flight-offers
```

## Auth

- `nbx_fs_api_key: 74f2474702784207a9785eb3ef8ae4e4` — static per-app API key.
- `X-AssignedDeviceID` — read from `localStorage["booking-widget.device.id"]`, set by
  the booking widget on first visit. Persists across sessions in the browser.
- `session-id` — fresh UUID v4 per request (client-generated correlation ID).
- Akamai bot protection cookies — only bypassable inside Tabby's browser session.

## Request Body

```json
{
  "channel": "WEB_DESKTOP",
  "itineraries": [{"origin": "DOH", "destination": "LHR", "departureDate": "2026-08-20", "isRequested": true}],
  "cabinClass": "ECONOMY",
  "ignoreInvalidPromoCode": true,
  "passengers": [{"type": "ADT", "count": 1}]
}
```

## Response (excerpt)

```json
{
  "flightOffers": [{
    "origin": {"iataCode": "DOH"},
    "destination": {"iataCode": "LHR"},
    "duration": 26100,
    "stops": 0,
    "segments": [{
      "flightNumber": "QR003",
      "departureDateTime": "2026-08-20T08:00:00",
      "arrivalDateTime": "2026-08-20T15:15:00",
      "origin": {"iataCode": "DOH"},
      "destination": {"iataCode": "LHR"}
    }],
    "fareOffers": [{
      "cabinClass": "ECONOMY",
      "baseAmount": 450.00,
      "totalAmount": 650.00,
      "currency": "USD",
      "fareBasisCode": "YLOWUS"
    }]
  }]
}
```

## Key Response Fields

| Field | Description |
|---|---|
| `flightOffers[]` | List of available flights |
| `.duration` | Flight duration in seconds |
| `.stops` | Number of stops (0 = nonstop) |
| `.segments[].flightNumber` | Flight number (e.g. `QR003`) |
| `.segments[].departureDateTime` | Departure datetime (ISO 8601) |
| `.segments[].arrivalDateTime` | Arrival datetime (ISO 8601) |
| `.fareOffers[].cabinClass` | Cabin class |
| `.fareOffers[].totalAmount` | Total fare including taxes |
| `.fareOffers[].currency` | Currency code |
| `alertMessages[]` | Travel advisories/warnings |
