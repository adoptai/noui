# KLM Flight Search API

## Platform

Same AFKL GraphQL platform as Air France, with `AFKL-TRAVEL-Host: KL`.
Endpoint: `POST https://www.klm.com/gql/v1?bookingFlow=LEISURE&operationName=SearchResultAvailableOffersQuery`

## Akamai Note

Akamai blocks direct Python requests with 403. The skill fills the KLM homepage
search form through CDP automation — this passes Akamai because the browser has
valid session cookies from visiting the site.

## Response Structure

```json
{
  "data": {
    "availableOffers": {
      "offerItineraries": [{
        "activeConnection": {
          "flightAmenityNumber": "KL_0641_2026-08-20",
          "segments": [{
            "equipmentName": "Boeing 787-10",
            "marketingFlight": {"operatingFlight": {"carrier": {"code": "KL"}}}
          }]
        }
      }]
    }
  }
}
```

## Key Fields

| Field | Description |
|---|---|
| `offerItineraries[]` | Available flight options |
| `.activeConnection.flightAmenityNumber` | Flight identifier (e.g. KL_0641_2026-08-20) |
| `.activeConnection.segments[].equipmentName` | Aircraft type |
| `.activeConnection.segments[].marketingFlight` | Flight number details |
