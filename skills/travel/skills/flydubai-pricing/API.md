# Flydubai Pricing API

Two operations. No auth required.

## `get_calendar`

GET `https://www.flydubai.com/api/Calendar/{origin}/{destination}`

**Args**

- `origin` (str, required) — origin airport code, e.g. `HYD`
- `destination` (str, required) — destination airport code, e.g. `DXB`
- `from_date` (str, optional) — starting date, e.g. `2026-06-01`
- `is_origin_metro` (str, default `false`) — origin metro flag
- `is_dest_metro` (str, default `false`) — destination metro flag

**Returns**

- `routes[].origin`
- `routes[].dest`
- `routes[].flightSchedules[]`
- `dateRestrictions`

## `search_flights`

POST `https://flights2.flydubai.com/api/flights/7`

**Args**

- `origin` (str, required)
- `destination` (str, required)
- `depart_date` (str, required)
- `return_date` (str, optional)
- `adults` (int, default `1`)
- `children` (int, default `0`)
- `infants` (int, default `0`)
- `cabin_class` (str, default `Economy`)
- `include_nearby` controls fare-window filtering. In the CLI, use `--exact-dates-only` to disable nearby dates.

**Returns**

| Field | Description |
|---|---|
| `route` | Route key, e.g. `HYD_DXB`. |
| `direction` | `outBound` or `inBound`. |
| `departureDate` | Segment departure date. |
| `fare` | Adult fare amount. |
| `tax` | Adult fare tax amount. |
| `currency` | Currency code. |
| `soldOut` | Sold-out status. |

## Hardcoded values from recording

| Value | Where |
|---|---|
| `appID: DESKTOP` | `search_flights` |
| Desktop browser User-Agent | both ops |
| `variant: 1` | `search_flights` payload |
| `cabinClass: Economy` | default cabin class |

## Out of scope

- Booking
- Checkout
- Payment
- Passenger details
- Baggage/seat purchase

## Recording provenance

- Workflow session: `0ff88622-3931-4349-ace6-4aabeb5b82e8`
- Generated skill id: `flydubai-pricing`
- Auto-export produced third-party telemetry operations; those were pruned.
- Final skill exposes only stable Flydubai schedule/pricing endpoints.
