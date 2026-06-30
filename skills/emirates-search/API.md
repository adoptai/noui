# Emirates Flight Search API

## Architecture

Emirates' search results page is an SSR Next.js app with ESI fragments. Flight data
is not in a single JSON API endpoint — it's rendered server-side and hydrated into
the browser's Redux store (`window.__NEXT_REDUX_STORE__`).

## Flow

1. **POST** search form to `/booking/search-results/?pageurl=/IBE&pub=/us/english&j=f&section=IBE`
   — form-encoded body (see below). Returns HTML containing a `ttid` session token.
2. Navigate browser to `/booking/search-results/?pub=%2Fus%2Fenglish&refreshId=<uuid>&ttid=<ttid>`
3. Page hydrates and fetches secondary APIs with `x-ek-srp-ttid` header.
4. Flight data populates in `window.__NEXT_REDUX_STORE__.getState().api.brandedFares.data['1-1']`.

## Search Form Body (application/x-www-form-urlencoded)

```
TID=OW&chkFlexibleDates=false&depShortDate=200826&departDate=20082026
&gacabinclass=0&j=t&selacity1=LHR&seladults=1&selcabinclass=0
&selchildren=0&seldcity1=DXB&selddate1=20-Aug-26
&selinfants=0&selofw=0&showOFW=false&showTeenager=false&showsearch=false
```

Key params: `seldcity1`=origin, `selacity1`=destination, `departDate`=DDMMYYYY,
`selcabinclass`=0 (Economy) / 1 (Business) / 2 (First).

## Response Structure (from Redux store)

```json
{
  "currency": {"sale": {"code": "AED"}, "priced": {"code": "AED"}},
  "lowestFare": {"total": [{"amount": 2285, "type": "CASH"}]},
  "totalOptionsCount": 10,
  "bounds": [{
    "origin": "DXB",
    "destination": "LHR",
    "count": 10,
    "options": [{
      "id": "DXB_LHR_EK003_1643752174",
      "numberOfConnections": 0,
      "ondDuration": "7H25M",
      "airSegments": [{
        "flightNumber": "003",
        "carrierCode": "EK",
        "departure": "DXB",
        "arrival": "LHR",
        "departureDateTime": "2026-08-20T14:15:00",
        "arrivalDateTime": "2026-08-20T18:40:00",
        "aircraftType": "388"
      }],
      "cabins": [{
        "cabinClass": "Y",
        "status": "AVAILABLE",
        "brandInformation": [{
          "fareBrand": "FLEX",
          "seatsAvailable": 9,
          "fareBasisCode": "KLSOSAE1/EOL4"
        }]
      }]
    }]
  }]
}
```

## Key Response Fields

| Field | Description |
|---|---|
| `totalOptionsCount` | Total number of available flights |
| `lowestFare.total[0].amount` | Lowest total fare in `currency.sale.code` |
| `bounds[0].options[]` | List of available flights |
| `.ondDuration` | Total journey duration (e.g. "7H25M") |
| `.numberOfConnections` | Stops (0 = nonstop) |
| `.airSegments[].flightNumber` | Flight number (e.g. "003" = EK003) |
| `.airSegments[].departureDateTime` | Departure datetime (ISO 8601) |
| `.airSegments[].arrivalDateTime` | Arrival datetime (ISO 8601) |
| `.airSegments[].aircraftType` | Aircraft IATA code (e.g. "388" = A380-800) |
| `.cabins[].cabinClass` | Cabin: Y=Economy, W=Premium Economy, J=Business, F=First |
| `.cabins[].brandInformation[].fareBrand` | Fare brand (FLEX, FLEXPLUS, SAVER) |
| `.cabins[].brandInformation[].seatsAvailable` | Seats remaining |
