# Wizz Air Search API Reference

## Endpoint

```
POST https://be.wizzair.com/28.10.1/Api/search/FlightDatesMultiArrival
```

The version segment (`28.10.1`) changes with Wizzair deployments. Check current version via browser console: `fetch('/buildnumber').then(r => r.text())`

## Request

**Content-Type:** `application/json`

**Required header:** `X-RequestVerificationToken` — fetched dynamically from the `RequestVerificationToken` browser cookie.

**Body:**

```json
{
  "departureStation": "BUD",
  "arrivalStations": ["LON"],
  "from": "2026-07-01T00:00:00.000Z",
  "to": "2026-08-30T00:00:00.000Z",
  "isReturn": false
}
```

| Field | Type | Description |
|---|---|---|
| `departureStation` | string | Departure IATA airport code |
| `arrivalStations` | string[] | List of arrival codes (airport or city, e.g. `"LON"`) |
| `from` | string | Start of search window, ISO 8601 timestamp |
| `to` | string | End of search window, ISO 8601 timestamp |
| `isReturn` | boolean | Whether to include return flights |

## Response

Returns dates with available flights, grouped by route key (e.g. `"buD-LGW"` = Budapest → London Gatwick).

```json
{
  "arrivalStationResults": {
    "buD-LGW": [
      "2026-07-01T00:00:00",
      "2026-07-02T00:00:00",
      "2026-07-03T00:00:00",
      "2026-07-05T00:00:00",
      "2026-07-06T00:00:00"
    ]
  }
}
```

Each date in the array has at least one available Wizz Air flight on that route. Dates not listed have no available flights.

## Error Responses

| Status | Body | Cause |
|---|---|---|
| 400 | `{"validationCodes":["InvalidDepartureStationCode"]}` | Wrong body format (e.g. using `flightList` wrapper instead of top-level fields) |
| 400 | `{"validationCodes":["InvalidArrivalStationCode"]}` | Unknown arrival station code |
| 404 | HTML | API version in URL is outdated — check `/buildnumber` |
