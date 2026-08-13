# ALFRED Treasury-yield supporting-source evidence

This directory verifies six explicitly selected native-vintage ALFRED observations used by the
Treasury-curve boundary scenario. It is separate from the capped formal adapter inventory:

- the formal target remains exactly 30 adapters under `verification/live/`;
- this is a scenario-specific supporting source, not a thirty-first counted adapter;
- DGS2 and DGS10 observations dated 2023-03-08, 2023-03-13, and 2023-03-15 produce six facts;
- each request fixes one series, observation date, and ALFRED vintage date;
- date-only vintages become usable only at 00:00 UTC two calendar days later;
- raw CSV remains in ignored local download-only storage; committed locks retain minimal facts,
  URLs, hashes, vintage dates, and timing metadata.

Rebuild the local evidence with:

```bash
.venv/bin/python scripts/validate_alfred_treasury_yields.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/alfred-treasury-yields/live \
  --raw-store data/raw/supporting/alfred-treasury-yields \
  --output verification/supporting/alfred-treasury-yields/latest-summary.json
```

The receipt proves retrieval, parsing, hashing, and local ingestion of ALFRED vintage facts. The
two-day rule is a conservative deterministic bound, not a claimed intraday H.15 release time. The
10-year-minus-2-year spread is a later FinReplay derivation, not an upstream reported series. This
evidence does not prove forecast skill, a calibrated range, causal banking or rate attribution,
trading performance, external validation, deployment, or user impact.
