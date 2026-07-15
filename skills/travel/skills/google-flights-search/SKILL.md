---
name: google-flights-search
description: "Search Google Flights for airports, price calendars, and flight options via Tabby. Use when the user wants to find flights, compare dates, or look up airport entity IDs. Requires ACTIVE Tabby profile `google-flights`."
---

# Google Flights Search

Airport autocomplete, price calendar, and shopping results through Tabby's `POST /execute/fetch`.

## Prerequisites

1. Tabby is reachable (`TABBY_API_URL` / `TABBY_API_HOST`).
2. `TABBY_CLIENT_ID` and `TABBY_CLIENT_SECRET` are set.
3. An ACTIVE Tabby profile with slug `google-flights`.

## Suggested workflow

1. Resolve cities/airports to Google entity IDs:
   ```bash
   python operations/search_airports.py --query "São Paulo"
   ```
2. Optional — price calendar across a date range:
   ```bash
   python operations/get_calendar.py \
     --origin-entity-id "/m/022pfm" \
     --destination-entity-id "/m/06gmr" \
     --calendar-start 2026-08-01 \
     --calendar-end 2026-08-31
   ```
3. Search flights for specific dates:
   ```bash
   python operations/search_flights.py \
     --origin-entity-id "/m/022pfm" \
     --destination-entity-id "/m/06gmr" \
     --departure-date 2026-08-15 \
     --return-date 2026-08-22 \
     --adults 1
   ```

## Notes

- Responses are Google's batchexecute wire format (often text/JSON hybrids). Parse carefully.
- Infrastructure query params (`f.sid`, `bl`) are baked from a recording and may need refresh if Google rotates them — re-record via NoUI capture.
- Do not call google.com with direct `httpx` from the agent host.
