# Turkish Airlines Flight Search API

## Endpoint

```
POST https://www.turkishairlines.com/api/v1/availability
```

## Auth

- `X-bfp` — PerimeterX browser fingerprint, read from `localStorage["bfp"]`.
- `X-clientId` — read from `localStorage["clientId"]`.
- `X-conversationId` and `X-requestId` — fresh UUID v4 per call.
- PerimeterX bot protection cookies — set by browser on first visit.

## Request Body

```json
{
  "selectedBookerSearch": "O",
  "selectedCabinClass": "ECONOMY",
  "moduleType": "TICKETING",
  "passengerTypeList": [{"quantity": 1, "code": "ADULT"}],
  "originDestinationInformationList": [{
    "originAirportCode": "IST", "originCountryCode": "", "originMultiPort": false, "originDomestic": false,
    "destinationAirportCode": "LHR", "destinationCountryCode": "", "destinationMultiPort": false, "destinationDomestic": false,
    "departureDate": "20-08-2026",
    "originCityCode": "IST", "destinationCityCode": "LHR",
    "originCity": "Istanbul", "destinationCity": "London"
  }],
  "savedDate": "2026-08-20T10:00:00.000Z",
  "preselectedOptionDetails": []
}
```

Note: `departureDate` uses **DD-MM-YYYY** format.

## Key Response Fields

| Field | Description |
|---|---|
| `data.originDestinationInformationList[0].originDestinationOptionList[]` | List of flight options |
| `.segmentList[].departureAirportCode` | Departure airport |
| `.segmentList[].arrivalAirportCode` | Arrival airport |
| `.segmentList[].departureDateTime` | Departure time (DD-MM-YYYY HH:MM) |
| `.segmentList[].arrivalDateTime` | Arrival time |
| `.segmentList[].flightCode` | Flight number (e.g. TK001) |
| `.segmentList[].flightDuration` | Duration in minutes |
| `.fareList[].totalAmount` | Total fare |
| `.fareList[].currency` | Currency |
