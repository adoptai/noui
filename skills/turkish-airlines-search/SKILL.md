---
name: Turkish Airlines Flight Search
description: Search Turkish Airlines (TK) for available one-way flights on a given route and date. Returns all flight options with departure/arrival times, stops, and pricing. Requires Tabby.
---

# Turkish Airlines Flight Search

## Requirements

- Tabby session running with profile `turkish-airlines-search`
- `tabby session ensure --profile turkish-airlines-search`

## Examples

```bash
.venv/bin/python3 skills/turkish-airlines-search/operations/search_flights.py \
  --origin IST --destination LHR --date 2026-08-20

.venv/bin/python3 skills/turkish-airlines-search/operations/search_flights.py \
  --origin IST --destination JFK --date 2026-09-10 --cabin-class BUSINESS
```
