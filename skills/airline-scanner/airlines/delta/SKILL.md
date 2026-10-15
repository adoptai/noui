---
name: Delta Air Lines Flight Search
description: Search Delta Air Lines (DL) for available one-way flights via GraphQL. Uses Authorization GUEST with Akamai browser cookies from Tabby. Returns offer sets with departure times, flight numbers, stops, and pricing.
---

# Delta Air Lines Flight Search

## Requirements

- Tabby session running with profile `delta-search`
- `tabby session ensure --profile delta-search`

## Examples

```bash
.venv/bin/python3 skills/delta-search/operations/search_flights.py \
  --origin ATL --destination JFK --date 2026-08-20

.venv/bin/python3 skills/delta-search/operations/search_flights.py \
  --origin LAX --destination SEA --date 2026-09-10
```
