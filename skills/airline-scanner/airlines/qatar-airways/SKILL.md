---
name: Qatar Airways Flight Search
description: Search Qatar Airways (QR) for available one-way flights on a given route and date. Returns flight offers with fares, durations, and stop information. Requires Tabby.
---

# Qatar Airways Flight Search

Search Qatar Airways for available flights on a one-way trip.

## Requirements

- Tabby session running with profile `qatar-airways-search`
- `tabby session ensure --profile qatar-airways-search`

## Operations

| Operation | Description |
|---|---|
| `search_flights` | Search for one-way flights on a given route and date |

## Examples

```bash
# Doha to London, Economy, August 20 2026
.venv/bin/python3 skills/qatar-airways-search/operations/search_flights.py \
  --origin DOH --destination LHR --date 2026-08-20

# Doha to New York, Business class
.venv/bin/python3 skills/qatar-airways-search/operations/search_flights.py \
  --origin DOH --destination JFK --date 2026-09-15 --cabin-class BUSINESS
```
