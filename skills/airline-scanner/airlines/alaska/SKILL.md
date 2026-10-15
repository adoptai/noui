---
name: alaska-search
description: "Use this skill to search Alaska Airlines for lowest fares on and around a given date. Returns a price calendar of 31 dates centered on the target date. Requires a live Tabby browser session."
---

# Alaska Airlines Search

Search Alaska Airlines' internal API for flight prices between two airports around a target date.

## Requirements

- Docker Desktop running
- Tabby started: `.venv/bin/python3 cli/main.py tabby start`
- Active browser session: `.venv/bin/python3 cli/main.py tabby session ensure --profile alaska-search`

## Operations

### `create_search_api_shoulderdates`

Search for lowest fares on and around a given date.

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Departure IATA airport code, e.g. `SEA`, `PDX`, `ANC` |
| `--destination` | Yes | Arrival IATA airport code, e.g. `LAX`, `JFK`, `ORD` |
| `--date` | Yes | Target departure date in YYYY-MM-DD format |
| `--adults` | No | Number of adult travelers (default: 1) |

## Examples

```bash
# SEA → LAX around Sep 1
.venv/bin/python3 skills/alaska-search/operations/create_search_api_shoulderdates.py \
  --origin SEA --destination LAX --date 2026-09-01

# PDX → JFK for 2 adults
.venv/bin/python3 skills/alaska-search/operations/create_search_api_shoulderdates.py \
  --origin PDX --destination JFK --date 2026-08-15 --adults 2
```

## Notes

- Returns 31 dates (price calendar) centered on `--date`, not a full flight listing
- Alaska Airlines operates primarily in the US, Canada, Mexico, and some Central America routes
- No bot protection headers needed — session cookies from Tabby are sufficient
