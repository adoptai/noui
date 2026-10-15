---
name: KLM Flight Search
description: Search KLM Royal Dutch Airlines (KL) for available one-way flights. Uses the same AFKL GraphQL platform as Air France (AFKL-TRAVEL-Host=KL). Navigates the Tabby browser, fills the KLM search form, and intercepts the SearchResultAvailableOffersQuery response. Requires Tabby.
---

# KLM Flight Search

## Requirements

- Tabby session running with profile `klm-search`
- `tabby session ensure --profile klm-search`

## Notes

KLM and Air France share the same AFKL booking platform. Akamai blocks direct API
calls, so this skill fills the homepage search form through CDP automation.
Origin/destination accept city names (e.g. "Amsterdam") or IATA codes (e.g. "AMS").

## Examples

```bash
.venv/bin/python3 skills/klm-search/operations/search_flights.py \
  --origin Amsterdam --destination "New York" --date 2026-08-20

.venv/bin/python3 skills/klm-search/operations/search_flights.py \
  --origin Amsterdam --destination London --date 2026-09-10
```
