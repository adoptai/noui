---
name: westjet-search
description: "Use this skill to search WestJet for available flights on a given route and date. Returns flight options with schedules, durations, stops, and fares. Requires a live Tabby browser session."
---

# WestJet Search

Search WestJet's flight booking API for available flights between two airports on a given date.

## Requirements

- Docker Desktop running
- Tabby started: `.venv/bin/python3 cli/main.py tabby start`
- Active browser session: `.venv/bin/python3 cli/main.py tabby session ensure --profile westjet-search`

## Operations

### `create_flight_search`

Search for available WestJet flights on a given route and date.

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Departure IATA airport code, e.g. `YYC`, `YVR`, `YYZ` |
| `--destination` | Yes | Arrival IATA airport code, e.g. `YYZ`, `YYC`, `YVR` |
| `--date` | Yes | Departure date in YYYY-MM-DD format |
| `--adults` | No | Number of adult travelers (default: 1) |
| `--children` | No | Number of child travelers (default: 0) |
| `--infants` | No | Number of infant travelers (default: 0) |
| `--currency` | No | Currency code (default: CAD) |

## Examples

```bash
# YYC → YYZ
.venv/bin/python3 skills/westjet-search/operations/create_flight_search.py \
  --origin YYC --destination YYZ --date 2026-08-20

# YVR → YYZ for 2 adults
.venv/bin/python3 skills/westjet-search/operations/create_flight_search.py \
  --origin YVR --destination YYZ --date 2026-09-01 --adults 2

# YYZ → YYC
.venv/bin/python3 skills/westjet-search/operations/create_flight_search.py \
  --origin YYZ --destination YYC --date 2026-08-15
```

## Notes

- Returns full flight listings with departure/arrival times, durations, stops, and pricing tiers
- WestJet operates primarily within Canada and to the US, UK, Caribbean, and Mexico
- Uses Kasada bot protection — Tabby browser session handles the `X-Lov30h0l-*` security headers automatically
- Prices are in CAD by default
