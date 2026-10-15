---
name: finnair-search
description: "Use this skill when the user wants to search Finnair flights for a given route and date. Returns full flight details including departure/arrival times, flight numbers, aircraft type, and fares. Requires a live Tabby browser session (Akamai bot protection)."
---

# Finnair Search

Search Finnair's internal offers API for flights between two airports on a specific date. Returns full flight details with schedules and fares.

## Requirements

- Docker Desktop running
- Tabby started: `.venv/bin/python3 cli/main.py tabby start`
- Active browser session: `.venv/bin/python3 cli/main.py tabby session ensure --profile finnair-search`

## Operations

### `create_current_api_airbounds`

Search Finnair flights for a given route and date.

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Departure IATA airport or city code, e.g. `HEL`, `NYC` |
| `--destination` | Yes | Arrival IATA airport or city code, e.g. `LON`, `BKK` |
| `--date` | Yes | Departure date in YYYY-MM-DD format |
| `--adults` | No | Number of adult travelers (default: 1) |
| `--children` | No | Number of child travelers (default: 0) |
| `--infants` | No | Number of infant travelers (default: 0) |

## Examples

```bash
# HEL → London on Aug 21
.venv/bin/python3 skills/finnair-search/operations/create_current_api_airbounds.py \
  --origin HEL --destination LON --date 2026-08-21

# NYC → Helsinki for 2 adults
.venv/bin/python3 skills/finnair-search/operations/create_current_api_airbounds.py \
  --origin NYC --destination HEL --date 2026-09-10 --adults 2
```

## Notes

- Bot protection: Akamai. Requires a real browser session via Tabby.
- Accepts both airport codes (`LHR`) and city codes (`LON`).
- Response includes `boundGroups` with full itinerary, `cheapestPrice`, aircraft info, and `fareSummaries` with price breakdowns.
