# American Airlines Flight Search

## Approach

PerimeterX + Akamai block direct requests. The skill navigates the Tabby browser
to the booking search URL and reads the rendered DOM after React hydration.

## Search URL

```
GET https://www.aa.com/booking/search
  ?locale=en_US&fareType=Lowest&pax=1&adult=1&type=OneWay
  &searchType=Revenue&cabin=&carriers=ALL&travelType=personal
  &slices=[{"orig":"JFK","origNearby":false,"dest":"LAX","destNearby":false,"date":"2026-08-20"}]
```

After page load + React hydration (~8s), DOM text contains:
- `N results` — total flight count
- Carousel with prices per date
- Individual flights: `JFK 6:00 AM LAX 8:59 AM 5h 59m Nonstop AA 171 32Q-Airbus`

## Key Response Fields

| Field | Description |
|---|---|
| `flightCount` | Total flights found |
| `flights[].flightNumber` | AA flight number |
| `flights[].departure` | Departure time (12h format) |
| `flights[].arrival` | Arrival time (12h format) |
| `flights[].duration` | Journey duration |
| `flights[].stops` | Nonstop or N stop(s) |
| `flights[].aircraft` | Aircraft type |
| `datePrices[].date` | Date label (e.g. "Thu, Aug 20") |
| `datePrices[].priceUSD` | Lowest fare in USD |
