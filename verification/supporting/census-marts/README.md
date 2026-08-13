# Census MARTS archived-release supporting evidence

This directory proves live retrieval and strict paired-form validation of three archived U.S.
Census Bureau Advance Monthly Retail Trade Survey releases selected for a March 2020 retail-sales
boundary, the seventeenth scenario accepted by the eight-gate catalog verifier. It is separate
from the capped formal adapter inventory:

- `census.marts.archived_retail_sales` is a scenario-specific supporting source, not a
  thirty-first counted adapter;
- the February 14 release reports a `0.3%` January increase and `$529.8 billion` of adjusted
  retail-and-food-services sales;
- the March 17 release reports a `0.5%` February decrease and revises January from `0.3%` to
  `0.6%`;
- the April 15 release reports an `8.7%` March decrease and revises February from `-0.5%` to
  `-0.4%`;
- each official PDF/XLS pair must match on release identity, reference month, headline change,
  year-over-year change, adjusted dollar total, prior-month revision, sampling variability, and
  revision statistics;
- each PDF must contain its exact verified nonblank page count and page-dimension sequence and
  state one 8:30 a.m. EST/EDT release time checked under `America/New_York`;
- each XLS must be a legacy OLE workbook with exactly `Table 1.`, `Table 2.`, and `Table 3.` and
  exact verified dimensions before fixed total-series cells are read;
- official 90-percent sampling-error margins remain reported statistical metadata and are never
  relabeled as a FinReplay forecast range or probability;
- earlier January and February snapshots are never overwritten by later revisions;
- full PDF/XLS pairs remain in ignored content-addressed storage; committed receipts retain
  hashes, URLs, sizes, retrieval times, warnings, and snapshot semantics.

Rebuild the live evidence and summary with:

```bash
python scripts/validate_census_marts.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/census-marts/live \
  --raw-store data/raw/supporting/census-marts \
  --output verification/supporting/census-marts/latest-summary.json
```

Two consecutive live runs are retained: the first inserts the three normalized facts and the
second proves all three are idempotent. This evidence does not establish a forecast, calibrated
probability, retail or pandemic causality, external validation, deployment, investment
performance, or user impact.
