---
name: hawaiian-airlines-search
description: "Use this skill to search Hawaiian Airlines for available flights on a given route and date. Returns a price calendar with lowest fares for dates around the search date. Requires a live Tabby browser session."
---

# Hawaiian Airlines Search

Search Hawaiian Airlines for lowest available fares between two airports around a given date. Hawaiian Airlines runs on the same booking platform as Alaska Airlines (Kasada bot protection), so all requests are executed inside Tabby's real Chrome browser via CDP.

## Requirements

- Docker Desktop running
- Tabby started: `.venv/bin/python3 cli/main.py tabby start`
- Active browser session: `.venv/bin/python3 cli/main.py tabby session ensure --profile hawaiian-airlines-search`

## Operations

### `create_flight_search`

Search for lowest fares on and around a given date (price calendar).

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Departure IATA airport code, e.g. `HNL`, `OGG`, `KOA` |
| `--destination` | Yes | Arrival IATA airport code, e.g. `OGG`, `HNL`, `LAX` |
| `--date` | Yes | Target departure date in YYYY-MM-DD format |
| `--adults` | No | Number of adult travelers (default: 1) |

## Examples

```bash
# HNL → OGG (Honolulu → Maui)
.venv/bin/python3 skills/hawaiian-airlines-search/operations/create_flight_search.py \
  --origin HNL --destination OGG --date 2026-08-20

# OGG → LAX for 2 adults
.venv/bin/python3 skills/hawaiian-airlines-search/operations/create_flight_search.py \
  --origin OGG --destination LAX --date 2026-09-01 --adults 2
```

## Notes

- Returns a price calendar (`shoulderDates`) with the cheapest fare per day for ~30 days around the target date
- Prices are in USD
- Hawaiian Airlines operates routes within Hawaii (inter-island) and between Hawaii and the US mainland, Japan, South Korea, Australia, and other Pacific destinations
- Uses Kasada bot protection — Tabby browser session handles this automatically via CDP
