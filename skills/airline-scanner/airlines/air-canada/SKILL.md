---
name: air-canada-search
description: "Use this skill to search Air Canada for available flights on a given route and date. Returns flight options with schedules, durations, stops, and fares across economy, premium economy, and business cabins. No Tabby required."
---

# Air Canada Search

Search Air Canada's flight booking API for available flights between two airports on a given date. Uses AWS AppSync (GraphQL) with Cognito unauthenticated credentials — no browser session required.

## Requirements

- No Tabby, no authentication
- Python 3.11+ (stdlib only)

## Operations

### `create_flight_search`

Search for available Air Canada flights on a given route and date.

| Argument | Required | Description |
|---|---|---|
| `--origin` | Yes | Departure IATA airport code, e.g. `YYZ`, `YVR`, `YYC` |
| `--destination` | Yes | Arrival IATA airport code, e.g. `YVR`, `YYZ`, `YYC` |
| `--date` | Yes | Departure date in YYYY-MM-DD format |
| `--adults` | No | Number of adult travelers (default: 1) |
| `--children` | No | Number of child travelers aged 2–11 (default: 0) |
| `--youth` | No | Number of youth travelers aged 12–17 (default: 0) |
| `--infants-on-lap` | No | Number of infants on lap (default: 0) |

## Examples

```bash
# YYZ → YVR
.venv/bin/python3 skills/air-canada-search/operations/create_flight_search.py \
  --origin YYZ --destination YVR --date 2026-08-20

# YYC → YYZ for 2 adults
.venv/bin/python3 skills/air-canada-search/operations/create_flight_search.py \
  --origin YYC --destination YYZ --date 2026-09-01 --adults 2

# YVR → YUL
.venv/bin/python3 skills/air-canada-search/operations/create_flight_search.py \
  --origin YVR --destination YUL --date 2026-08-15
```

## Notes

- Returns full flight listings with departure/arrival times (timezone-aware ISO 8601), durations, stops, and per-cabin pricing
- Cabin codes: `Y` = Economy, `O` = Premium Economy, `J` = Business
- Prices are in CAD
- Air Canada serves Canada, USA, Europe, Asia, Caribbean, and more
- No Tabby required — uses public Cognito Identity Pool to obtain temporary AWS credentials, then signs requests with AWS Signature V4
