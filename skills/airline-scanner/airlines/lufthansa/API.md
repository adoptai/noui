# Lufthansa Flight Search API

## Flow

1. `POST https://api.shop.lufthansa.com/one-booking/v2/auth/token` — OAuth2 client credentials with static keys → Bearer JWT.
2. `POST https://api.shop.lufthansa.com/one-booking/v2/search/air-bounds` — flight search with JWT.

Both behind Cloudflare; run inside Tabby's shop.lufthansa.com session.

## Auth Token

```
POST https://api.shop.lufthansa.com/one-booking/v2/auth/token
Content-Type: application/x-www-form-urlencoded

client_id=onebooking-ui-lh&client_secret=RNJg0okYPt9fkh8BMkmrbDvpMLh5A3sl&context={"country":"DE"}&grant_type=client_credentials
```

## Search Body

```json
{
  "commercialFareFamilies": ["DEMALLFPP"],
  "itineraries": [{"departureDateTime": "2026-08-20T00:00:00.000", "originLocationCode": "FRA", "destinationLocationCode": "NYC", "isRequestedBound": true}],
  "travelers": [{"discounts": [], "passengerTypeCode": "ADT"}],
  "searchPreferences": {"showSoldOut": false, "showMilesPrice": false}
}
```

## Key Response Fields

| Field | Description |
|---|---|
| `data.airBoundGroups[]` | Available flight options |
| `.boundDetails.originLocationCode` | Departure airport |
| `.boundDetails.destinationLocationCode` | Arrival airport |
| `.boundDetails.duration` | Duration in seconds |
| `.boundDetails.segments[].flightId` | Flight identifier (e.g. SEG-LH402-FRAEWR-2026-08-20-1320) |
| `.airBounds[].fareFamilyCode` | Fare family code |
| `.airBounds[].isCheapestOffer` | True if cheapest in group |
| `.airBounds[].availabilityDetails[].cabin` | Cabin class (eco/bus/fir) |
| `.airBounds[].availabilityDetails[].quota` | Seats available |
