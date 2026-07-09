---
name: wizz-air-search
description: Search Wizz Air for available flight dates on a route. Returns all dates with flights within a specified window. Requires a live Tabby browser session.
---

# Wizz Air Search

Search Wizz Air's internal availability API for flights between two airports over a date range. Returns all dates in the window that have available flights.

## Requirements

- Docker Desktop running
- Tabby started: `.venv/bin/python3 cli/main.py tabby start`
- Active browser session: `.venv/bin/python3 cli/main.py tabby session ensure --profile wizz-air-search`

## Operation

### `create_api_search_flightdatesmultiarrival`

Search for available flight dates on a route.

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Departure IATA code, e.g. `BUD`, `WMI` |
| `--destination` | Yes | Arrival IATA or city code, e.g. `LON`, `LTN`, `STN` |
| `--date` | Yes | Start of search window, YYYY-MM-DD |
| `--days` | No | Days to search ahead from `--date` (default: 60) |
| `--return` | No | Flag: search for return flights |

## Examples

```bash
# Find all BUD→London flights from July 1 over the next 60 days
.venv/bin/python3 skills/wizz-air-search/operations/create_api_search_flightdatesmultiarrival.py \
  --origin BUD --destination LON --date 2026-07-01

# Search a longer window
.venv/bin/python3 skills/wizz-air-search/operations/create_api_search_flightdatesmultiarrival.py \
  --origin WMI --destination LTN --date 2026-06-01 --days 90
```

## Notes

- `destination` accepts Wizz Air city codes (`LON` = all London airports) or specific airport codes (`LGW`, `LTN`, `STN`)
- The API version in the URL path (`28.10.1`) changes with Wizzair deployments. If requests fail with 404, check the current version by running in the browser console: `fetch('/buildnumber').then(r => r.text())`
- Bot protection: Kasada. Cannot bypass without a real browser session — Tabby is required.
