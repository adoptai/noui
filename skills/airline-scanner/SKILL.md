---
name: airline-scanner
description: "Search flights across 25 airlines from a single entry point. Covers major carriers worldwide — European low-cost (Ryanair, EasyJet, Wizz Air), North American (Delta, United, American, Alaska, Southwest, JetBlue, Air Canada, WestJet), Gulf (Emirates, Etihad, Qatar, flydubai), Asian (Singapore Airlines, Cathay Pacific, AirAsia, IndiGo), and European full-service (Lufthansa, Swiss, Finnair, British Airways, KLM, Turkish, Volaris). Triggers on any request to search, compare, or find flights on a named airline. Read-only — no booking."
---

# Airline Scanner

Search flights across 25 airlines. Given a user request naming an airline, identify the airline, then follow the per-airline instructions to run its operation.

## How to use this plugin

1. **Identify the airline** from the table below.
2. **Check the Tabby column** — if yes, ensure a Tabby session is running for that profile first.
3. **Read `airlines/<slug>/SKILL.md`** for that airline's specific arguments and examples.
4. **Run** `airlines/<slug>/operations/<operation>.py` with the required arguments.

## Airline index

| Airline | IATA | Slug | Tabby | Operation |
|---|---|---|---|---|
| Air Canada | AC | `air-canada` | No | `create_flight_search.py` |
| AirAsia | AK | `airasia` | Yes | `create_flight_search.py` |
| Alaska Airlines | AS | `alaska` | Yes | `create_search_api_shoulderdates.py` |
| American Airlines | AA | `american-airlines` | Yes | `search_flights.py` |
| British Airways | BA | `british-airways` | No | `search_flights.py` |
| Cathay Pacific | CX | `cathay-pacific` | Yes | `search_flights.py` |
| Delta Air Lines | DL | `delta` | Yes | `search_flights.py` |
| EasyJet | U2 | `easyjet` | No | `get_homepage_api_availability.py` |
| Emirates | EK | `emirates` | Yes | `search_flights.py` |
| Finnair | AY | `finnair` | Yes | `create_current_api_airbounds.py` |
| Hawaiian Airlines | HA | `hawaiian-airlines` | Yes | `create_flight_search.py` |
| IndiGo | 6E | `indigo` | Yes | `search_flights.py` |
| JetBlue | B6 | `jetblue` | No | `create_v1_search_ngb.py` |
| KLM | KL | `klm` | Yes | `search_flights.py` |
| Lufthansa | LH | `lufthansa` | Yes | `search_flights.py` |
| Qatar Airways | QR | `qatar-airways` | Yes | `search_flights.py` |
| Ryanair | FR | `ryanair` | No | `get_v4_en_us_availability.py` |
| Singapore Airlines | SQ | `singapore-airlines` | Yes | `search_flights.py` |
| Southwest Airlines | WN | `southwest` | Yes | `create_air_booking_shopping.py` |
| Swiss International | LX | `swiss` | Yes | `search_flights.py` |
| Turkish Airlines | TK | `turkish-airlines` | Yes | `search_flights.py` |
| United Airlines | UA | `united` | Yes | `search_flights.py` |
| Volaris | Y4 | `volaris` | Yes | `create_flight_search.py` |
| WestJet | WS | `westjet` | Yes | `create_flight_search.py` |
| Wizz Air | W6 | `wizz-air` | Yes | `create_api_search_flightdatesmultiarrival.py` |

## Tabby sessions

For any airline marked **Tabby: Yes**, start a session before running the operation:

```bash
tabby session ensure --profile <slug>
```

For example:
```bash
tabby session ensure --profile emirates
tabby session ensure --profile delta
```

No-Tabby airlines (Air Canada, British Airways, EasyJet, JetBlue, Ryanair) call public APIs directly and need no session.

## Example — Emirates flight search

```bash
# 1. Start Tabby session
tabby session ensure --profile emirates

# 2. Read per-airline notes
# (read airlines/emirates/SKILL.md)

# 3. Run the operation
.venv/bin/python airlines/emirates/operations/search_flights.py \
  --origin DXB --destination LHR --date 2026-09-15
```

## Example — Ryanair flight search (no Tabby)

```bash
.venv/bin/python airlines/ryanair/operations/get_v4_en_us_availability.py \
  --origin DUB --destination STN --date 2026-09-15
```
