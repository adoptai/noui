---
name: volaris-search
description: "Use this skill to search Volaris for available flights on a given route and date. Returns flight options with schedules, stops, and fares. Requires a live Tabby browser session."
---

# Volaris Search

Search Volaris's flight booking API for available flights between two airports on a given date. Volaris's API is protected by AWS WAF bot detection, so all requests are executed inside Tabby's real Chrome browser via CDP.

## Requirements

- Docker Desktop running
- Tabby started: `.venv/bin/python3 cli/main.py tabby start`
- Active browser session: `.venv/bin/python3 cli/main.py tabby session ensure --profile volaris-search`

## Operations

### `create_flight_search`

Search for available Volaris flights on a given route and date.

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Departure IATA airport code, e.g. `MEX`, `GDL`, `CUN` |
| `--destination` | Yes | Arrival IATA airport code, e.g. `GDL`, `MEX`, `CUN` |
| `--date` | Yes | Departure date in YYYY-MM-DD format |
| `--adults` | No | Number of adult travelers (default: 1) |
| `--children` | No | Number of child travelers ages 2-11 (default: 0) |
| `--infants` | No | Number of infant travelers under 2 (default: 0) |
| `--currency` | No | Currency code for displayed prices (default: MXN) |

## Examples

```bash
# MEX → GDL
.venv/bin/python3 skills/volaris-search/operations/create_flight_search.py \
  --origin MEX --destination GDL --date 2026-08-20

# CUN → MEX for 2 adults
.venv/bin/python3 skills/volaris-search/operations/create_flight_search.py \
  --origin CUN --destination MEX --date 2026-09-01 --adults 2

# MEX → TIJ in USD
.venv/bin/python3 skills/volaris-search/operations/create_flight_search.py \
  --origin MEX --destination TIJ --date 2026-08-15 --currency USD
```

## Notes

- Returns flight listings with departure/arrival times, stops, segments, and fare totals
- Fares are in MXN by default; pass `--currency USD` for US dollars
- Uses AWS WAF bot detection — Tabby browser session handles this automatically via CDP fetch
- The skill reads the DotRez anonymous JWT from the browser's sessionStorage and refreshes it if near expiry
- Volaris operates routes primarily within Mexico, plus US cities (LAX, LAS, SNA, SJC, DAL, IAH, ORD, MDW, PHX)
