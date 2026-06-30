---
name: Swiss International Flight Search
description: Search Swiss International Air Lines (LX) for available one-way flights on a given route and date. Uses the same LH Group one-booking API as Lufthansa. Requires Tabby.
---

# Swiss International Flight Search

## Requirements

- Tabby session running with profile `swiss-search`
- `tabby session ensure --profile swiss-search`

## Examples

```bash
.venv/bin/python3 skills/swiss-search/operations/search_flights.py \
  --origin ZRH --destination JFK --date 2026-08-20

.venv/bin/python3 skills/swiss-search/operations/search_flights.py \
  --origin GVA --destination LHR --date 2026-09-10 --cabin-class BUSINESS
```
