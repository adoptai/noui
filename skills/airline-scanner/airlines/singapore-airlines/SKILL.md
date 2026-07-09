---
name: Singapore Airlines Flight Search
description: Search Singapore Airlines (SQ) for fare availability on a given route and date. Returns a 15-day fare window with prices per departure date. Requires Tabby.
---

# Singapore Airlines Flight Search

Search Singapore Airlines for fare prices on a given route around a target date.

## Requirements

- Tabby session running with profile `singapore-airlines-search`
- `tabby session ensure --profile singapore-airlines-search`

## Operations

| Operation | Description |
|---|---|
| `search_flights` | Get fare prices for a 15-day window around the requested date |

## Notes

Singapore Airlines' booking form is protected by Akamai Bot Manager and uses
server-side session state, making programmatic form submission unreliable. This
skill calls the `getHistogram.form` API which returns fare prices per day for a
given route. Individual flight schedules (flight numbers, departure times) are
embedded in the SSR results page and require a human-initiated session.

## Examples

```bash
# Singapore to London, Economy, August 20 2026
.venv/bin/python3 skills/singapore-airlines-search/operations/search_flights.py \
  --origin SIN --destination LHR --date 2026-08-20

# London to Singapore, Business class
.venv/bin/python3 skills/singapore-airlines-search/operations/search_flights.py \
  --origin LHR --destination SIN --date 2026-09-10 --cabin-class BUSINESS
```
