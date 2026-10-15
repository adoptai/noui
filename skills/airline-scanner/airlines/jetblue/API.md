# API Reference: JetBlue Search

## Endpoint

**POST** `https://cb-api.jetblue.com/cb-flight-search/v1/search/NGB`

Returns available flights for a given route and date with pricing and schedule details.

## Authentication

Static subscription key in request header — no session cookies required.

```
ocp-apim-subscription-key: a5ee654e981b4577a58264fed9b1669c
```

## Request

**Content-Type:** `application/json`

**Body:**

```json
{
  "awardBooking": false,
  "travelerTypes": [{"type": "ADULT", "quantity": 1}],
  "searchComponents": [
    {
      "from": "JFK",
      "to": "LAX",
      "date": "2026-08-21"
    }
  ]
}
```

## Response

```json
{
  "status": {"transactionStatus": "success"},
  "data": {
    "searchResults": [
      {
        "productOffers": [
          {
            "originAndDestination": [
              {
                "originDestinationType": "NONSTOP",
                "departure": {"date": "2026-08-21T06:00:00", "airport": "JFK", "terminal": "5"},
                "arrival": {"date": "2026-08-21T08:48:00", "airport": "LAX", "terminal": "1"},
                "stops": 0,
                "totalDuration": 348,
                "flightSegments": [...]
              }
            ],
            "pricingDetail": [
              {
                "fareFamily": "Blue",
                "price": {"totalPrice": 159.00, "currency": "USD"},
                "fareAvailability": "AVAILABLE"
              }
            ]
          }
        ]
      }
    ]
  }
}
```

| Field | Description |
|---|---|
| `originDestinationType` | `NONSTOP`, `ONE_STOP`, etc. |
| `totalDuration` | Flight duration in minutes |
| `fareFamily` | Fare class: `Blue`, `Blue Plus`, `Blue Extra`, `Mint` |
| `price.totalPrice` | Total price per person |
| `fareAvailability` | `AVAILABLE` or `SOLD_OUT` |
