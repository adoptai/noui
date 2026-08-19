---
name: American Airlines Flight Search
description: Search American Airlines (AA) for available one-way flights on a given route and date. Navigates the Tabby browser to the search URL and parses the React DOM after hydration. Returns up to 40 flights with times, flight numbers, stops, and fare prices. Requires Tabby.
---

# American Airlines Flight Search

## Requirements

- Tabby session running with profile `american-airlines-search`
- `tabby session ensure --profile american-airlines-search`

## Examples

```bash
.venv/bin/python3 skills/american-airlines-search/operations/search_flights.py \
  --origin JFK --destination LAX --date 2026-08-20

.venv/bin/python3 skills/american-airlines-search/operations/search_flights.py \
  --origin ORD --destination MIA --date 2026-09-10
```
