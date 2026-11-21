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

The `vintage-revisions` CLI compares one source and series at two knowledge cutoffs, keeping the observation period fixed. On the packaged GDP lock, 2022 Q3 was 25,663.289 billion dollars as of November 1, 2022 and 25,723.941 as of February 1, 2023. The revision of 60.652 crossed an illustrative 25,700 threshold. Q4 was newly visible at the second cutoff, so its revision and crossing are both null. Each comparable observation links both fact IDs. Reproduce offline with the real-lock regression in `tests/unit/test_timevault.py`.
