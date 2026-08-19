# United Airlines Flight Search

## Approach

United uses SSE (Server-Sent Events) with a session-specific `X-Authorization-api` 
bearer token — not practical to call directly. The skill navigates the Tabby browser 
to the choose-flights URL and parses the rendered DOM after the SSE data loads.

## Search URL

```
https://www.united.com/en/us/fsr/choose-flights
  ?f=ORD&t=SFO&d=2026-08-20&tt=1&sc=7&px=1&taxng=1&newHP=True&clm=7&st=best&fareFamily=ECONOMY
```

After page load + SSE render (~12s), DOM text contains flights in format:
`NONSTOP 6:00 AM  8:38 AM  ORD  4H, 38M  SFO  UA 246 Boeing 757-300`

## Key Response Fields

| Field | Description |
|---|---|
| `flightCount` | Parsed flight count |
| `flights[].flightNumber` | UA flight number |
| `flights[].departure` | Departure time (12h format) |
| `flights[].arrival` | Arrival time |
| `flights[].duration` | Journey duration |
| `flights[].stops` | NONSTOP or N STOP(S) |
| `flights[].aircraft` | Aircraft type |
| `lowestPricesUSD[]` | Lowest Economy fares in USD |
