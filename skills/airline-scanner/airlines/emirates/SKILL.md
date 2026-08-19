---
name: Emirates Flight Search
description: Search Emirates (EK) for available one-way flights on a given route and date. Returns all flight options with fares, timings, aircraft type, and cabin availability. Requires Tabby.
---

# Emirates Flight Search

Search Emirates for available flights on a one-way trip.

## Requirements

- Tabby session running with profile `emirates-search`
- `tabby session ensure --profile emirates-search`

## Operations

| Operation | Description |
|---|---|
| `search_flights` | Search for one-way flights on a given route and date |

## Examples

```bash
# Dubai to London, Economy, August 20 2026
.venv/bin/python3 skills/emirates-search/operations/search_flights.py \
  --origin DXB --destination LHR --date 2026-08-20

# New York to Dubai, Business class
.venv/bin/python3 skills/emirates-search/operations/search_flights.py \
  --origin JFK --destination DXB --date 2026-09-10 --cabin-class BUSINESS
```
