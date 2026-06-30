---
name: jetblue-search
description: "Use this skill to search JetBlue flights for a given route and date. Returns available flights with pricing, schedules, and flight details. No Tabby required."
---

# JetBlue Search

Search JetBlue's flight booking API for available flights between two airports on a given date.

## Requirements

No Tabby or browser session needed — uses a static public API subscription key.

## Operations

### `create_v1_search_ngb`

Search for available JetBlue flights on a given route and date.

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Origin metro or airport code, e.g. `JFK`, `NYC`, `BOS` |
| `--destination` | Yes | Destination metro or airport code, e.g. `BOS`, `LAX`, `LON` |
| `--date` | Yes | Departure date in YYYY-MM-DD format |
| `--adults` | No | Number of adult travelers (default: 1) |
| `--children` | No | Number of child travelers (default: 0) |
| `--infants` | No | Number of infant travelers (default: 0) |

## Examples

```bash
# JFK → LAX
.venv/bin/python3 skills/jetblue-search/operations/create_v1_search_ngb.py \
  --origin JFK --destination LAX --date 2026-08-21

# NYC → LON for 2 adults
.venv/bin/python3 skills/jetblue-search/operations/create_v1_search_ngb.py \
  --origin NYC --destination LON --date 2026-08-15 --adults 2

# BOS → FLL
.venv/bin/python3 skills/jetblue-search/operations/create_v1_search_ngb.py \
  --origin BOS --destination FLL --date 2026-09-01
```

## Notes

- JetBlue uses metro codes (`NYC`, `LON`) as well as airport codes (`JFK`, `LHR`)
- Returns full flight listings (not a price calendar) — includes flight number, departure/arrival times, pricing tiers, and stops
- JetBlue operates primarily in the US, Caribbean, and select transatlantic routes (London, Amsterdam, Paris)
- No bot protection — the static `ocp-apim-subscription-key` is sufficient
