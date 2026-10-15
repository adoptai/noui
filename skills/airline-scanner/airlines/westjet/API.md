# API Reference: WestJet Search

## Endpoint

**POST** `https://apiw.westjet.com/ecomm/booktrip/flight-search-api/v1`

Returns available flights for a given route and date with pricing and schedule details.

## Authentication

Requires Kasada bot protection headers (`X-Lov30h0l-*`) added automatically by the Tabby browser session on `www.westjet.com`.

## Request

**Content-Type:** `application/json`

**Body:**

```json
{
  "appSource": "widgetOW",
  "bookId": "59acbb42-dbe3-4112-b9e7-09426313ec1a",
  "isBereavement": false,
  "isCompanion": false,
  "currency": "CAD",
  "currentFlightIndex": 1,
  "guests": [
    {"type": "adult", "count": "1"},
    {"type": "child", "count": "0"},
    {"type": "infant", "count": "0"}
  ],
  "showMemberExclusives": false,
  "showTravelPrivileges": false,
  "trips": [
    {
      "arrival": "YYZ",
      "calLowestPrice": "",
      "departure": "YYC",
      "departureDate": "2026-08-20",
      "order": 1
    }
  ],
  "isCommissionable": false,
  "promoCode": ""
}
```

## Response

```json
{
  "flights": [
    {
      "currency": "CAD",
      "lowestFromPrice": "214.68",
      "flightDate": "2026-08-20",
      "departAirportCode": "YYC",
      "arrivalAirportCode": "YYZ",
      "flightOptions": [
        {
          "flightDetails": {
            "totalTravelDuration": {"mins": 55, "hrs": 3},
            "flightSegments": [
              {
                "numberOfStops": 0,
                "flightNumber": "622",
                "operatingAirline": "WS",
                "arrivalTime": "6:55 AM",
                "departureTime": "1:00 AM",
                "arrivalDate": "Thu Aug 20, 2026"
              }
            ]
          }
        }
      ]
    }
  ]
}
```

| Field | Description |
|---|---|
| `lowestFromPrice` | Lowest available fare for the date |
| `flightOptions` | All available flight itineraries |
| `totalTravelDuration` | Total hours and minutes |
| `numberOfStops` | 0 = nonstop |
| `operatingAirline` | `WS` = WestJet, `WR` = WestJet Encore |
