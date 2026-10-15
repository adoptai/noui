---
name: ryanair-search
description: "Use this skill when the user wants to search Ryanair flights for a given route, date, and passenger count. Triggers on \"ryanair flight search\", \"ryanair availability\", \"search ryanair\", or any request that implies searching for Ryanair flights between two airports. No authentication required — uses the public Ryanair availability API directly."
---

# Ryanair Search

NoUI-generated skill for Ryanair Search. The single operation under `operations/` is a standalone CLI script that prints a JSON response to stdout.

## Environment

Operations run under `.venv/bin/python` — a virtualenv that lives **inside this skill's directory**, sibling to `manifest.json`. Provision it once after install by running the commands below **from this skill's own folder** (not from the workspace root):

```bash
cd path/to/this/skill                # wherever manifest.json lives
uv sync                              # preferred — lands .venv/ in this dir
# — or —
python -m venv .venv && .venv/bin/pip install -e .
```

After provisioning, the examples below use `.venv/bin/python`. This skill uses **stdlib only** (no third-party HTTP libraries) so the venv only needs to satisfy `requires-python = ">=3.11"`.

*Tip: `noui skill install ryanair-search <agent> --project` provisions the venv for you at install time.*

## Prerequisites

No authentication or Tabby session required. The operation calls the public Ryanair booking availability API directly using `urllib.request`.

<!-- custom:start:prerequisites -->
<!-- Add skill-specific prereqs here; this region survives `noui skill docs`. -->
<!-- custom:end:prerequisites -->

## Operations

### `get_v4_en_us_availability`

Search Ryanair flights for a given route, date, and passenger count. Returns available flights with fares.

**Command:**

```bash
.venv/bin/python operations/get_v4_en_us_availability.py \
  --origin <origin> \
  --destination <destination> \
  --date <date> \
  [--adults <adults>] \
  [--teens <teens>] \
  [--children <children>] \
  [--infants <infants>] \
  [--round-trip] \
  [--date-in <date-in>]
```

**Arguments:**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `--origin` | string | yes | — | Origin IATA airport code, e.g. `"DUB"`. |
| `--destination` | string | yes | — | Destination IATA airport code, e.g. `"STN"`. |
| `--date` | string | yes | — | Outbound date in YYYY-MM-DD format. |
| `--adults` | integer | no | `1` | Number of adult travelers. |
| `--teens` | integer | no | `0` | Number of teen travelers. |
| `--children` | integer | no | `0` | Number of child travelers. |
| `--infants` | integer | no | `0` | Number of infant travelers. |
| `--round-trip` | flag | no | `false` | Search for a return flight. |
| `--date-in` | string | no | `""` | Return date in YYYY-MM-DD format (used with `--round-trip`). |

**Example — one-way DUB → STN:**

```bash
.venv/bin/python operations/get_v4_en_us_availability.py \
  --origin DUB \
  --destination STN \
  --date 2026-07-15
```

**Example — round trip DUB → STN:**

```bash
.venv/bin/python operations/get_v4_en_us_availability.py \
  --origin DUB \
  --destination STN \
  --date 2026-07-15 \
  --round-trip \
  --date-in 2026-07-22 \
  --adults 2
```
