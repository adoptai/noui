---
name: southwest-search
description: "Use this skill to search Southwest Airlines flights for a given route and date. Returns available flights with fares broken down by fare class (Wanna Get Away, Plus, Anytime, Business Select). Requires a live Tabby browser session (Akamai bot protection)."
---

# Southwest Search

Search Southwest's internal air-booking API for flights between two airports on a specific date.

## Requirements

- Docker Desktop running
- Tabby started: `.venv/bin/python3 cli/main.py tabby start`
- Active browser session: `.venv/bin/python3 cli/main.py tabby session ensure --profile southwest-search`

## Operations

### `create_air_booking_shopping`

Search Southwest flights for a given route and date.

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Departure IATA airport code, e.g. `LAX`, `DAL`, `HOU` |
| `--destination` | Yes | Arrival IATA airport code, e.g. `LGA`, `MDW`, `BWI` |
| `--date` | Yes | Departure date in YYYY-MM-DD format |
| `--adults` | No | Number of adult travelers (default: 1) |
| `--round-trip` | No | Flag: search for a return flight |
| `--date-in` | No | Return date in YYYY-MM-DD format (used with `--round-trip`) |

## Examples

```bash
# One-way LAX → LGA
.venv/bin/python3 skills/southwest-search/operations/create_air_booking_shopping.py \
  --origin LAX --destination LGA --date 2026-08-21

# Round trip DAL → MDW
.venv/bin/python3 skills/southwest-search/operations/create_air_booking_shopping.py \
  --origin DAL --destination MDW --date 2026-08-21 \
  --round-trip --date-in 2026-08-28
```

## Notes

- Southwest only flies within the US, Mexico, Caribbean, and Central America — no transatlantic routes
- Bot protection: Akamai. Akamai sensor headers (`EE30zvQLWf-*`) are added automatically by the browser's JS
- Response contains `airProducts` with individual flights and `fareSummary` with minimum prices by fare class
