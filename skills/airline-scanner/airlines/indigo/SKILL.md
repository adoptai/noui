---
name: IndiGo Flight Search
description: Search IndiGo (6E) for available one-way flights on a given route and date. Returns all flight options with fares, timings, and availability. Requires Tabby.
---

# IndiGo Flight Search

Search IndiGo's flight inventory for a one-way trip on a specific date.

## Requirements

- Tabby session running with profile `indigo-search`
- `tabby session ensure --profile indigo-search`

## Operations

| Operation | Description |
|---|---|
| `search_flights` | Search for one-way flights on a given route and date |

## Examples

```bash
# Delhi to Mumbai, August 20 2026
.venv/bin/python3 skills/indigo-search/operations/search_flights.py \
  --origin DEL --destination BOM --date 2026-08-20

# Bangalore to Hyderabad, 2 adults
.venv/bin/python3 skills/indigo-search/operations/search_flights.py \
  --origin BLR --destination HYD --date 2026-09-15 --adults 2
```
