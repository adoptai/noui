---
name: airbnb-search-places
description: "Search Airbnb for places and stay listings via Tabby. Use when the user wants to find Airbnb rentals — e.g. \"find Airbnbs in San Francisco May 12-22 for 2 adults\". Two operations: search_places (autocomplete → place_id) and search_listings (place_id + dates + guests → listings). Requires an ACTIVE Tabby profile `airbnb`."
---

# Airbnb Search

Airbnb place autocomplete + stay search through Tabby's `POST /execute/fetch` (browser TLS + session cookies). Two operations; chain them.

## Prerequisites

1. Tabby is reachable (`TABBY_API_URL` / `TABBY_API_HOST`).
2. `TABBY_CLIENT_ID` and `TABBY_CLIENT_SECRET` are set.
3. An ACTIVE Tabby profile with slug `airbnb` (override with `--profile-slug` / `PROFILE_SLUG`).

## Usage

1. Resolve the place to a Google place_id:
   ```bash
   python operations/search_places.py --query "San Francisco"
   ```
   Pick the first result's `location.google_place_id` and `display_name`.
2. Search listings:
   ```bash
   python operations/search_listings.py \
     --place-id "ChIJIQBpAG2ahYAR_6128GcTUEo" \
     --query "San Francisco, California, United States" \
     --checkin 2026-05-12 --checkout 2026-05-22 \
     --adults 2
   ```

Responses are printed as JSON on stdout.

## Operations

### `search_places`

| Flag | Type | Required | Default | Description |
|---|---|---|---|---|
| `--query` | string | yes | — | Free-text place name. |
| `--num-results` | int | no | `10` | Max suggestions. |
| `--locale` | string | no | `en` | |
| `--currency` | string | no | `USD` | |
| `--profile-slug` | string | no | `airbnb` | Tabby profile slug. |

### `search_listings`

| Flag | Type | Required | Default | Description |
|---|---|---|---|---|
| `--place-id` | string | yes | — | Google place_id from `search_places`. |
| `--query` | string | yes | — | Human-readable place name. |
| `--checkin` / `--checkout` | string | yes | — | `YYYY-MM-DD`. |
| `--adults` / `--children` / `--infants` / `--pets` | int | no | `1`/`0`/`0`/`0` | |
| `--profile-slug` | string | no | `airbnb` | Tabby profile slug. |

## Notes

- Do not call Airbnb with direct `httpx` from the agent host — use these operations so traffic goes through Tabby.
- `search_listings` uses a persisted GraphQL query hash. If Airbnb rotates it, re-record the workflow with the NoUI capture flow.
