---
name: easyjet-search
description: "Use this skill when the user wants to search EasyJet flights for a given route, date, and currency. Triggers on \"easyjet flight search\", \"easyjet availability\", \"search easyjet\", or any request that implies searching for EasyJet flights between two airports. No authentication required — uses the public EasyJet availability API directly."
---

# EasyJet Search

Search EasyJet's availability API for flights between two airports. Returns lowest available price per date (a price calendar). No authentication or Tabby session required.

## Operations

### `get_homepage_api_availability`

Search EasyJet flights for a given route and date.

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Departure IATA airport code, e.g. `LGW`, `LTN`, `BRS` |
| `--destination` | Yes | Arrival IATA airport code, e.g. `AMS`, `BCN` |
| `--date` | Yes | Outbound date in YYYY-MM-DD format |
| `--currency` | No | Currency code for prices (default: `GBP`) |
| `--round-trip` | No | Flag: search for a return flight |
| `--date-in` | No | Return date in YYYY-MM-DD format (used with `--round-trip`) |

## Examples

```bash
# One-way LGW → AMS
.venv/bin/python3 skills/easyjet-search/operations/get_homepage_api_availability.py \
  --origin LGW --destination AMS --date 2026-07-15

# Round trip LGW → BCN in EUR
.venv/bin/python3 skills/easyjet-search/operations/get_homepage_api_availability.py \
  --origin LGW --destination BCN --date 2026-07-15 \
  --round-trip --date-in 2026-07-22 --currency EUR
```

## Notes

- Returns a price calendar (lowest fare per date), not a full flight list with departure times
- No Tabby or Docker required
