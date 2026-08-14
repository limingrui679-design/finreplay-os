# EIA Weekly Natural Gas Storage Report supporting evidence

This directory proves live retrieval and strict cross-validation of the three March 2020 Lower
48 working-gas records selected for the WNGSR storage boundary:

- `eia.wngsr.revision_safe_working_gas` retrieves the official `revisions.xls`,
  `ngshistory.xls`, and fixed 2020–22 WNGSR performance evaluation;
- the March 12, 19, and 26 releases cover weeks ending March 6, 13, and 20, with reported Lower
  48 stocks of `2,043`, `2,034`, and `2,005` Bcf;
- `revisions.xls` must retain the original estimate semantics, every selected row must have no
  published revision or reclassification note, and all selected values must equal the current
  history workbook;
- the performance evaluation must state the standard Thursday 10:30 a.m. Eastern release rule,
  confirm that every 2020–22 release met the established schedule, and retain the selected
  coefficient-of-variation and weekly-net-change standard-error rows;
- the March 19 record is independently identified as the first WNGSR under the remote telework
  posture, which EIA reports did not disrupt publication;
- regional rounding differences of at most 2 Bcf are retained explicitly and never forced to
  reconcile by altering a reported value;
- all three complete responses remain in ignored content-addressed storage; committed receipts
  retain exact hashes, canonical URLs, sizes, retrieval times, and normalized fact hashes.

Rebuild the live evidence and summary with:

```bash
python scripts/validate_eia_wngsr.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/eia-wngsr/live \
  --raw-store data/raw/supporting/eia-wngsr \
  --output verification/supporting/eia-wngsr/latest-summary.json
```

This evidence establishes official retrieval, original-value recovery, current-history
cross-checking, exact schedule timing, statistical-source metadata, hashing, and local ingestion.
It does not establish a forecast, calibrated range, causal effect, storage constraint, external
validation, deployment, trading result, or user impact.
