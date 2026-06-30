---
name: Lufthansa Flight Search
description: Search Lufthansa (LH) for available one-way flights on a given route and date. Returns air bounds with fare families, segment details, and availability. Requires Tabby.
---

# Lufthansa Flight Search

## Requirements

- Tabby session running with profile `lufthansa-search`
- `tabby session ensure --profile lufthansa-search`

## Examples

```bash
.venv/bin/python3 skills/lufthansa-search/operations/search_flights.py \
  --origin FRA --destination NYC --date 2026-08-20

.venv/bin/python3 skills/lufthansa-search/operations/search_flights.py \
  --origin MUC --destination LHR --date 2026-09-10 --cabin-class BUSINESS
```
