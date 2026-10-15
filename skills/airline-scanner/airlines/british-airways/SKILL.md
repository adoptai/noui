---
name: British Airways Flight Search
description: Search British Airways (BA) for available one-way flights on a given route and date. No Tabby required — uses the public BFF GET API. Returns flights with departure/arrival times, cabin availability, and fare families.
---

# British Airways Flight Search

**No Tabby required** — stdlib urllib only.

## Examples

```bash
.venv/bin/python3 skills/british-airways-search/operations/search_flights.py \
  --origin LHR --destination JFK --date 2026-08-20

.venv/bin/python3 skills/british-airways-search/operations/search_flights.py \
  --origin JFK --destination LHR --date 2026-09-10 --cabin-class BUSINESS
```
