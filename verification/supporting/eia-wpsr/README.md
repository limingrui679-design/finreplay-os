# EIA archived Weekly Petroleum Status Report supporting evidence

This directory proves live retrieval and strict cross-validation of the three archived WPSR Table
4 releases selected for the April 2020 commercial-crude-stock boundary. It is separate from the
capped formal adapter inventory:

- `eia.wpsr.archived_commercial_crude_stocks` is a scenario-specific supporting source, not a
  thirty-first counted adapter;
- the release dates are April 8, 15, and 22, 2020, for weeks ending April 3, 10, and 17;
- archived CSV values for U.S. commercial crude stocks excluding SPR are respectively `484,370`,
  `503,618`, and `518,640` thousand barrels;
- every exact CSV value and reported weekly difference must reconcile arithmetically;
- each archived PDF must identify the same release date, contain the standard Wednesday release
  language, and show the corresponding Table 4 values rounded to one decimal million barrels;
- both official `Last-Modified` headers must fall on the PDF's stated release date;
- because “after 10:30 a.m.” is not an exact publication timestamp, each paired release becomes
  knowledge-eligible only at midnight `America/New_York` on the following local day;
- full CSV and PDF responses remain in ignored content-addressed storage; committed receipts retain
  their hashes, URLs, sizes, retrieval times, and normalized fact hashes.

Rebuild the live evidence and summary with:

```bash
python scripts/validate_eia_wpsr.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/eia-wpsr/live \
  --raw-store data/raw/supporting/eia-wpsr \
  --output verification/supporting/eia-wpsr/latest-summary.json
```

This evidence establishes internal source retrieval, paired CSV/PDF identity checks, exact unit
normalization, arithmetic reconciliation, conservative timing, hashing, and local ingestion. It
does not yet count a twelfth scenario and does not establish a forecast, calibrated range, oil-
market causality, external validation, deployment, or investment results.
