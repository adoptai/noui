# API Reference: Alaska Airlines Search

## Endpoint

**POST** `https://www.alaskaair.com/search/api/shoulderDates`

Returns lowest fares for 31 dates centered on the target date for a given route.

## Authentication

Requires session cookies from an active Tabby browser session on `www.alaskaair.com`.

## Request

**Content-Type:** `application/json`

**Body:**

```json
{
  "origins": ["SEA"],
  "destinations": ["LAX"],
  "dates": ["2026-08-21"],
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
  "isAlaska": true,
  "isWholeTripPricing": true,
  "businessRequest": {"TravelerId": "", "BusinessRequestType": 0, "CountryCode": "", "StateCode": "", "ShowOnlySpecialFares": true}
}
```

## Response

```json
{
  "shoulderDates": [
    {
      "date": "2026-08-06",
      "price": 128.40,
      "awardPoints": null,
      "isDiscounted": false,
      "flightSegments": [],
      "solutionId": "8d5B8fdWs19ToaGtCSAJNJ001"
    },
    {
      "date": "2026-08-07",
      "price": 128.40,
      ...
    }
  ]
}
```

| Field | Description |
|---|---|
| `shoulderDates` | 31 dates centered on the requested date |
| `price` | Lowest available fare for that date |
| `isDiscounted` | Whether a discount applies |
| `solutionId` | Internal ID that can be used in subsequent booking requests |
