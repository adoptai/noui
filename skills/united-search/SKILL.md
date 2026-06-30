---
name: United Airlines Flight Search
description: Search United Airlines (UA) for available one-way flights on a given route and date. Navigates Tabby browser to the choose-flights URL and parses the DOM after SSE flight data renders. Requires Tabby.
---

# United Airlines Flight Search

## Requirements

- Tabby session running with profile `united-search`
- `tabby session ensure --profile united-search`

## Examples

```bash
.venv/bin/python3 skills/united-search/operations/search_flights.py \
  --origin ORD --destination SFO --date 2026-08-20

.venv/bin/python3 skills/united-search/operations/search_flights.py \
  --origin IAH --destination LAX --date 2026-09-10
```
