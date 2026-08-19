# API Reference: Hawaiian Airlines Search

## Endpoint

**POST** `https://www.hawaiianairlines.com/search/api/shoulderDates`

Returns lowest available fares for a route across ~30 days centered on the requested date (price calendar). Same booking platform as Alaska Airlines.

## Authentication

**Kasada bot protection** — direct Python HTTP calls are blocked. All requests must be made from inside a real Chrome browser via Tabby CDP (`cdp_fetch`).

## Request

**Content-Type:** `application/json`

**Body:**

```json
{
  "origins": ["HNL"],
  "destinations": ["OGG"],
  "dates": ["2026-08-20"],
  "onba": false,
  "dnba": false,
  "numADTs": 1,
  "numCHDs": 0,
  "isAddingToAdultRes": false,
  "sliceToSearch": 0,
  "sliceSelections": [],
  "selectedSegments": [],
  "fareView": "None",
  "discount": {"code": "", "status": 0, "searchContainsDiscountedFare": false},
  "isAlaska": false,
  "isWholeTripPricing": true,
  "businessRequest": {
    "TravelerId": "",
    "BusinessRequestType": 0,
    "CountryCode": "",
    "StateCode": "",
    "ShowOnlySpecialFares": true
  }
}
```

**Note:** `isAlaska: false` distinguishes Hawaiian from Alaska Airlines on the shared platform.

## Response

```json
{
  "shoulderDates": [
    {
      "date": "2026-08-05",
      "price": 74.89,
      "awardPoints": null,
      "isDiscounted": false,
      "flightSegments": [],
      "solutionId": "K1a6YIDXylHTlJbKH9ZAwB001"
    },
    {
      "date": "2026-08-20",
      "price": 84.9,
      "awardPoints": null,
      "isDiscounted": false,
      "flightSegments": [],
      "solutionId": "K1a6YIDXylHTlJbKH9ZAwB010"
    }
  ],
  "calendarDates": [...],
  "sessionId": "abc123",
  "solutionSetId": "xyz789"
}
```

| Field | Description |
|---|---|
| `shoulderDates` | Array of dates with lowest available fare for each day |
| `shoulderDates[].date` | Date in YYYY-MM-DD format |
| `shoulderDates[].price` | Lowest fare in USD for that date |
| `shoulderDates[].isDiscounted` | Whether a discount applies |
| `shoulderDates[].solutionId` | Internal ID to retrieve full itinerary details |
| `calendarDates` | Broader calendar view (similar structure) |
| `sessionId` | Session identifier for follow-up requests |
