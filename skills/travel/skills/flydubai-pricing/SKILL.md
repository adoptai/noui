---
name: flydubai-pricing
description: "Search flydubai route availability and fare pricing via Tabby. Use for flight calendar availability, fare and tax estimates, or one-way/round-trip pricing. Read-only — no booking. Requires ACTIVE Tabby profile `flydubai`."
---

# flydubai-pricing

Search flydubai route availability and fare pricing through Tabby's `POST /execute/fetch`.

## Prerequisites

1. Tabby is reachable (`TABBY_API_URL` / `TABBY_API_HOST`).
2. `TABBY_CLIENT_ID` and `TABBY_CLIENT_SECRET` are set.
3. An ACTIVE Tabby profile with slug `flydubai` (override with `--profile-slug` / `PROFILE_SLUG`).

## Operations

### get_calendar — Route calendar availability

```bash
python operations/get_calendar.py --origin DXB --destination CMB --from-date 2026-06-01
```

### search_flights — Fare pricing for a specific date

```bash
# One-way
python operations/search_flights.py --origin DXB --destination BOM --depart-date 2026-08-15

# Round-trip
python operations/search_flights.py --origin DXB --destination KHI --depart-date 2026-09-01 --return-date 2026-09-08

# Exact dates only
python operations/search_flights.py --origin DXB --destination CMB --depart-date 2026-08-20 --exact-dates-only
```

## Suggested workflow

1. Run `get_calendar` to inspect available dates for a route.
2. Run `search_flights` for specific travel dates.

## Notes

- IATA airport codes are required (`DXB`, `BOM`, `KHI`, `CMB`).
- Dates must use `YYYY-MM-DD`.
- No booking or checkout — read-only.
