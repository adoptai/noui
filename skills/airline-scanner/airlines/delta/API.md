# Delta Air Lines Flight Search API

## Endpoint

```
POST https://offer-api-prd.delta.com/prd/rm-offer-gql
Authorization: GUEST
applicationId: DC  |  x-app-type: dcom-shop  |  Airline: DL
channelId: DCOM   |  x-app-route: search
TransactionId: {uuid}_{timestamp_ms}
```

Akamai blocks direct calls — run from inside Tabby's delta.com browser session.

## Key Response Fields

| Field | Description |
|---|---|
| `data.gqlSearchOffers.gqlOffersSets[]` | Array of flight offer sets |
| `.trips[0].originAirportCode` | Departure airport |
| `.trips[0].destinationAirportCode` | Arrival airport |
| `.trips[0].scheduledDepartureLocalTs` | Departure datetime (ISO 8601) |
| `.trips[0].scheduledArrivalLocalTs` | Arrival datetime |
| `.trips[0].stopCnt` | Number of stops |
| `.trips[0].totalTripTime.hourCnt/minuteCnt` | Duration |
| `.trips[0].flightSegment[].marketingCarrier.carrierNum` | Flight number |
| `.offers[].offerItems[].retailItems[].retailItemMetaData.fareInformation.farePrice.totalFarePrice.currencyEquivalentPrice.roundedCurrencyAmt` | Price |
