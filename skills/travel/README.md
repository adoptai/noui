# Travel plugin

Claude Code plugin bundling demo travel skills that run through Tabby's
`POST /execute/fetch` (and `/execute/browser` where needed).

## Skills

| Skill | Profile slug | Ops |
|---|---|---|
| `airbnb-search-places` | `airbnb` | `search_places`, `search_listings` |
| `expedia-stay-search` | `expedia` | `search_hotels` |
| `flydubai-pricing` | `flydubai` | `get_calendar`, `search_flights` |
| `google-flights-search` | `google-flights` | `search_airports`, `get_calendar`, `search_flights` |

## Install

From a clone of this repo:

```bash
claude plugin install ./skills/travel
```

(If your Claude Code build uses the older verb, try `claude plugins add ./skills/travel`.)

## Prerequisites

1. A reachable Tabby (`TABBY_API_URL` / `TABBY_API_HOST`).
2. Agent credentials: `TABBY_CLIENT_ID` and `TABBY_CLIENT_SECRET`.
3. ACTIVE Tabby profiles for the skills you use (see table above). Create them with the
   NoUI three-pillar flow — see [`../noui/references/tabby-setup.md`](../noui/references/tabby-setup.md).

Profiles are **not** bundled. The plugin assumes they already exist for your Tabby instance.
