---
name: Cathay Pacific Flight Search
description: Search Cathay Pacific (CX) for available one-way flights on a given route and date. Returns all flight options with departure/arrival times, flight numbers, durations, and a 7-day fare calendar in HKD. Requires Tabby.
---

# Cathay Pacific Flight Search

Search Cathay Pacific for available flights on a one-way trip.

## Requirements

- Tabby session running with profile `cathay-pacific-search`
- `tabby session ensure --profile cathay-pacific-search`

## Operations

| Operation | Description |
|---|---|
| `search_flights` | Search for one-way flights on a given route and date |

## Examples

```bash
# Hong Kong to London, Economy, August 20 2026
.venv/bin/python3 skills/cathay-pacific-search/operations/search_flights.py \
  --origin HKG --destination LHR --date 2026-08-20

# Hong Kong to Sydney, September 10 2026
.venv/bin/python3 skills/cathay-pacific-search/operations/search_flights.py \
  --origin HKG --destination SYD --date 2026-09-10
```
