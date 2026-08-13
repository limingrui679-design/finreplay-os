# ALFRED supporting-source evidence

This directory verifies the official ALFRED native-vintage GDP source used by the GDP revision
boundary scenario. It is deliberately separate from `verification/live/`:

- the formal official-adapter target remains capped at and verified as exactly 30 adapters;
- ALFRED is a scenario-specific supporting source, not a thirty-first counted adapter;
- four explicitly named historical vintages were retrieved and hashed in one schema-1.1 receipt;
- raw CSV responses remain only in the ignored local content-addressed store;
- committed scenario locks may preserve the six minimal reported facts, their source hashes, and
  their conservative knowledge times, but do not redistribute the raw downloads;
- a vintage date is date-granular. FinReplay uses `vintage date + 2 calendar days at 00:00 UTC`
  as a conservative knowledge bound and does not claim an intraday release timestamp.

Rebuild the local evidence with:

```bash
.venv/bin/python scripts/validate_alfred_gdp.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/alfred/live \
  --raw-store data/raw/supporting/alfred \
  --output verification/supporting/alfred/latest-summary.json
```

The receipt proves retrieval, parsing, hashing, and local TimeVault insertion. It does not prove
economic causality, forecasting validity, investment performance, external validation, or a right
to redistribute source data.
