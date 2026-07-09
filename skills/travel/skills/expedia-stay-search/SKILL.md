---
name: expedia-stay-search
description: Use this skill when the user wants to search for hotels on Expedia by destination and dates, find available stays, check hotel pricing, or get a Hotel-Search URL. Triggers on "find a hotel", "search Expedia", "look up hotels in <city>", "book a stay", "hotel availability", or any request that implies Expedia stay search. Requires an authenticated Tabby browser session for the `expedia` profile; not usable as a generic hotel search tool for sites other than Expedia.
---

# Expedia Stay Search

Search Expedia for available hotels by destination and dates using an authenticated Tabby browser session. Returns a Hotel-Search URL and extracted listings (name, nightly price, total price, rating, reviews, refundable flag, link).

This skill uses Tabby's `POST /execute/fetch` and `POST /execute/browser` endpoints to drive the real Expedia site inside a Tabby-managed browser. That is intentional: Expedia sits behind Akamai, which blocks non-browser TLS fingerprints. Do not try to port the operations to `httpx` or `requests` — you will get 429 Too Many Requests.

## Prerequisites

Before invoking any operation:

1. **Tabby is running with an ACTIVE `expedia` profile.** Create or refresh the profile with the NoUI capture flow (`capture_record` → `capture_import`). A STAGING profile returns 409 from the execute endpoints.
2. **`PROFILE_SLUG=expedia` is exported** (or pass `--profile-slug`). This is the slug, not the DB UUID.
3. **Agent credentials are configured.** `TABBY_CLIENT_ID` and `TABBY_CLIENT_SECRET` must be set, plus `TABBY_API_URL` / `TABBY_API_HOST`. See `skills/noui/references/tabby-setup.md`.

## Operations

### `search_hotels`

Search Expedia for hotels in a destination, on given check-in / check-out dates, for a given number of guests and rooms. Returns up to 25 listings extracted from the first page of results.

**Command:**

```bash
python operations/search_hotels.py \
  --destination "Paris" \
  --check-in 2026-05-01 \
  --check-out 2026-05-04 \
  --guests 2 \
  --rooms 1
```

Prints a JSON object to stdout on success. Exits non-zero on failure with a diagnostic on stderr.

**Arguments:**

| Name | Type | Required | Default | Notes |
|---|---|---|---|---|
| `--destination` | string | yes | — | City or place name. Expedia typeahead resolves it to a region ID. |
| `--check-in` | string | yes | — | `YYYY-MM-DD`. |
| `--check-out` | string | yes | — | `YYYY-MM-DD`. Must be after `--check-in`. |
| `--guests` | integer | no | `2` | Adult guests. |
| `--rooms` | integer | no | `1` | Number of rooms. |
| `--profile-slug` | string | no | `$PROFILE_SLUG` or `expedia` | Tabby profile slug. |

**Output shape:**

```json
{
  "search_url": "https://www.expedia.com/Hotel-Search?...",
  "destination": "Paris, France",
  "region_id": "178279",
  "check_in": "2026-05-01",
  "check_out": "2026-05-04",
  "guests": 2,
  "rooms": 1,
  "listings_count": 25,
  "listings": [
    {
      "name": "Hotel Le Example",
      "price_per_night": "$245",
      "price_total": "$735",
      "rating": "8.4/10",
      "rating_label": "Very Good",
      "reviews_count": "1,234",
      "refundable": true,
      "url": "https://www.expedia.com/..."
    }
  ]
}
```

## Troubleshooting

- **`No healthy Tabby session for profile "expedia"`** — ensure the profile is ACTIVE and has a live session.
- **`Tabby execute/fetch failed (429)`** — Akamai rate-limiting; rotate the session or re-record the profile.
- **Empty `listings`** — HAR capture did not find structured search results; Expedia may have changed its API shape.
- **`TABBY_CLIENT_ID and TABBY_CLIENT_SECRET must be set`** — create an agent client in the Tabby admin UI.

## Notes

- Uses **HAR capture** during page navigation to intercept Expedia's API responses, with a page-summary fallback.
- A single invocation can take 10–20 seconds (typeahead + navigation + SPA render).
