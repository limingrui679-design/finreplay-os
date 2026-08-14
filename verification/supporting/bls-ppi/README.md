# BLS Producer Price Index supporting evidence

This directory proves live retrieval and strict paired validation of the three archived BLS PPI
releases selected for the final-demand price-change boundary. It is separate from the capped
formal 30-adapter inventory and is not represented as a thirty-first counted adapter:

- `bls.ppi.archived_final_demand` retrieves both the official HTML and complete PDF for each
  explicitly approved release date;
- the March 12, April 9, and May 13, 2020 releases report seasonally adjusted final-demand monthly
  changes of `-0.6%`, `-0.2%`, and `-1.3%` for February, March, and April;
- both formats must agree on the 8:30 a.m. EDT embargo end, weekday, release number, report month,
  headline, two prior monthly changes, 12-month change, Table 1 row, technical definition, and
  four-month revision rule;
- every PDF must have the approved 31- or 32-page structure, `612 x 792` point unrotated pages,
  nonblank text layers, and the expected Technical Note and Tables 1, 7, and 8 locations;
- adjacent releases preserve February at `-0.6%` and March at `-0.2%`; no later snapshot silently
  overwrites either first-reported decision input;
- BLS defines PPI from the domestic-producer seller perspective. It is not CPI, a quantity,
  revenue, profit, transaction, household-cost, causal, forecast, or investment measure;
- March and April COVID-19 text about the pricing date, response rates, and estimation procedures
  is retained as source methodology, not converted into a causality or unaffected-measurement
  claim;
- full HTML and PDF responses remain in ignored content-addressed storage; committed receipts
  retain exact URLs, hashes, sizes, retrieval times, record counts, and normalized fact hashes.

Rebuild the live evidence and deterministic summary with:

```bash
python scripts/validate_bls_ppi.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/bls-ppi/live \
  --raw-store data/raw/supporting/bls-ppi \
  --output verification/supporting/bls-ppi/latest-summary.json
```

This evidence establishes current official retrieval, paired-format semantic agreement, exact
release timing, revision-snapshot preservation, hashing, and local ingestion. It does not establish
forecast skill, calibrated coverage, causal effects, external validation, deployment, investment
performance, or user impact.
