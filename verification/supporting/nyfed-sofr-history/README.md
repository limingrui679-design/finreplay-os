# New York Fed final historical SOFR supporting evidence

This directory proves live retrieval and strict parsing of the three final SOFR rows selected for
the September 2019 SOFR boundary. It is separate from the capped formal adapter inventory:

- `nyfed.sofr.final_historical_rate` is a scenario-specific supporting source, not a thirty-first
  counted adapter;
- the effective dates are September 13, 16, and 17, 2019;
- the reported final rates are respectively `220`, `243`, and `525` basis points;
- the New York Fed publishes SOFR on the following business day at approximately 8:00 a.m. ET and
  permits qualifying revisions at approximately 2:30 p.m.; FinReplay uses 3:00 p.m. New York time
  as the conservative final knowledge time;
- the selected rows have empty revision indicators;
- percentile fields are validated for internal ordering but excluded from normalized facts because
  lagged ancillary summary statistics can change;
- raw JSON remains in ignored content-addressed storage and reuse requires the New York Fed's
  current attribution, permissions, modification, and non-endorsement notices.

Rebuild the live evidence and summary with:

```bash
python scripts/validate_nyfed_sofr_history.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/nyfed-sofr-history/live \
  --raw-store data/raw/supporting/nyfed-sofr-history \
  --output verification/supporting/nyfed-sofr-history/latest-summary.json
```

This evidence establishes internal source retrieval, exact rate normalization, revision-cutoff
timing, hashing, and local ingestion. It does not yet count an eleventh scenario and does not
establish a forecast, calibrated interval, repo-market causality, external validation, deployment,
or investment results.
