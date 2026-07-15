# Airbnb Search API

Two operations. No auth required.

## `search_places`

GET `https://www.airbnb.com/api/v2/autocompletes-personalized`

**Args**
- `query` (str, required) — free-text place name
- `num_results` (int, default 10)
- `locale` (str, default "en")
- `currency` (str, default "USD")

**Returns** parsed JSON. Key field per result:
`autocomplete_terms[i].location.google_place_id` and `.display_name`.

## `search_listings`

POST `https://www.airbnb.com/api/v3/StaysSearch/{sha256Hash}`

Persisted GraphQL query. Path + `extensions.persistedQuery.sha256Hash` is
hardcoded to the hash captured during recording.

**Args**
- `place_id` (str, required) — Google place_id from `search_places`
- `query` (str, required) — human-readable place name
- `checkin` (str, required, YYYY-MM-DD)
- `checkout` (str, required, YYYY-MM-DD)
- `adults` (int, default 1)
- `children` (int, default 0)
- `infants` (int, default 0)
- `pets` (int, default 0)
- `locale` (str, default "en")
- `currency` (str, default "USD")

**Returns** GraphQL response. Listings under
`data.presentation.staysSearch.results.searchResults[]`.

## Hardcoded values (from recording)

| Value | Where |
|---|---|
| `X-Airbnb-API-Key: d306zoyjsyarp7ifhu67rjxn52tv0t20` | both ops |
| Persisted query hash `753d97c7b19a…1641a` | `search_listings` |
| `treatmentFlags` (10 items) | `search_listings` GraphQL variables |
| `requestedPageType: STAYS_SEARCH`, `searchType: autocomplete_click` | `search_listings` |
| `refinementPaths: /homes`, `tabId: home_tab`, `screenSize: large` | `search_listings` |
| `version: 1.8.8` | `search_listings` |
| `acpId` | generated per call (random UUID) |

If Airbnb ships a new frontend release the persisted query hash may rotate,
causing `PersistedQueryNotFound`. Re-record the workflow to refresh.
