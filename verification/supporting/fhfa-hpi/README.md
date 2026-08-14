# FHFA HPI archived-release supporting evidence

This directory proves live retrieval and strict validation of the preannounced 2020 FHFA House
Price Index schedule and three official report PDFs selected for a March 2020 national
purchase-only HPI boundary. It is separate from the capped formal adapter inventory:

- `fhfa.hpi.archived_purchase_only_monthly_change` is a scenario-specific supporting source,
  not a thirty-first counted adapter;
- the official August 20, 2019 schedule says the selected 2020 reports are released at 9 a.m. ET;
- the March 25 report initially records January's seasonally adjusted U.S. purchase-only monthly
  change as `0.3%`;
- the April 22 report revises January to `0.5%` and initially records February as `0.7%`;
- the May 26 report retains January at `0.5%`, revises February to `0.8%`, and initially records
  March as `0.1%`;
- every reference month is versioned by report snapshot, so later revisions never overwrite the
  initial-release values;
- every report must match its exact page count, `612 x 792` geometry, page rotations, nonblank
  text layers, identifying PDF metadata, cover, release, methodology, monthly table, revision
  rows, index values, and schedule footer;
- the 2019 schedule HTML must match its publication date, 9 a.m. ET statement, and selected dates;
  a canonical semantic hash prevents changing site wrappers from becoming new schedule facts,
  while each raw response hash remains in its fetch receipt;
- the January report footer says `9AM EST`, whereas the preannounced schedule and later reports
  say `9AM ET`; the adapter preserves that discrepancy and uses the dated ET schedule with
  `America/New_York`, making all three selected reports available at 13:00 UTC;
- current HTTP headers are retrieval metadata only and are never backdated;
- the May report PDF metadata records a June 15 modification, so its March value is treated as an
  official archived report fact, not proof that the present bytes are unchanged from May 26;
- the purchase-only HPI is a repeat-transaction index using Enterprise data. It is not a universal
  home-price level, transaction count, appraisal series, causal estimate, or investment result;
- full schedule HTML and report PDFs remain in ignored content-addressed storage. Committed
  receipts retain hashes, URLs, sizes, retrieval times, warnings, and snapshot semantics.

Rebuild the live evidence and summary with:

```bash
.venv/bin/python scripts/validate_fhfa_hpi.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/fhfa-hpi/live \
  --raw-store data/raw/supporting/fhfa-hpi \
  --output verification/supporting/fhfa-hpi/latest-summary.json
```

Two consecutive live runs are retained: the first inserts all three fact versions and the second
proves all three are idempotent. This evidence does not establish forecast skill, calibrated
coverage, universal housing-market measurement, COVID-19 or policy causality, external
validation, deployment, investment performance, or user impact.
