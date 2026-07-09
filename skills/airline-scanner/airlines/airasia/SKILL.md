---
name: airasia-search
description: "Use this skill to search AirAsia for available flights on a given route and date. Returns flight options with schedules, durations, stops, and fares. Requires a live Tabby browser session."
---

# AirAsia Search

Search AirAsia's flight booking API for available flights between two airports on a given date. AirAsia's API is protected by Cloudflare Bot Management, so all requests are executed inside Tabby's real Chrome browser via CDP.

## Requirements

- Docker Desktop running
- Tabby started: `.venv/bin/python3 cli/main.py tabby start`
- Active browser session: `.venv/bin/python3 cli/main.py tabby session ensure --profile airasia-search`

## Operations

### `create_flight_search`

Search for available AirAsia flights on a given route and date.

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Departure IATA airport code, e.g. `KUL`, `SIN`, `BKK` |
| `--destination` | Yes | Arrival IATA airport code, e.g. `SIN`, `KUL`, `BKK` |
| `--date` | Yes | Departure date in YYYY-MM-DD format |
| `--adults` | No | Number of adult travelers (default: 1) |
| `--children` | No | Number of child travelers (default: 0) |
| `--infants` | No | Number of infant travelers (default: 0) |
| `--currency` | No | Currency code for displayed prices (default: USD) |

## Examples

```bash
# KUL → SIN
.venv/bin/python3 skills/airasia-search/operations/create_flight_search.py \
  --origin KUL --destination SIN --date 2026-08-21

# BKK → KUL for 2 adults
.venv/bin/python3 skills/airasia-search/operations/create_flight_search.py \
  --origin BKK --destination KUL --date 2026-09-01 --adults 2

# KUL → CGK in MYR
.venv/bin/python3 skills/airasia-search/operations/create_flight_search.py \
  --origin KUL --destination CGK --date 2026-08-15 --currency MYR
```

## Notes

- Returns full flight listings with departure/arrival times, segment details, airline codes, and pricing
- Airline codes: `AK` = AirAsia Malaysia, `D7` = AirAsia X, `FD` = Thai AirAsia, `QZ` = Indonesia AirAsia, `TR` = Scoot (partner)
- Uses Cloudflare Bot Management — Tabby browser session handles this automatically via CDP fetch
- The skill obtains a short-lived JWT via AirAsia's anonymous web-client credentials before each search
- Prices are in USD by default; pass `--currency MYR` for Malaysian Ringgit
