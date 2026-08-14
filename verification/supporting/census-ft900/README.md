# Census/BEA FT-900 supporting evidence

This directory proves live retrieval and strict validation of three archived joint Census/BEA
U.S. International Trade in Goods and Services release pairs selected for a March 2020 trade-
deficit boundary. It is separate from the capped formal adapter inventory:

- `census.bea.ft900.archived_trade_balance` is a scenario-specific supporting source, not a
  thirty-first counted adapter;
- the March 6 release initially reports the January goods-and-services deficit as `$45,338
  million`;
- the April 2 release revises January to `$45,482 million` and initially reports February as
  `$39,932 million`;
- the May 5 release retains January at `$45,482 million`, revises February to `$39,810 million`,
  and initially reports March as `$44,415 million`;
- every statistical month is versioned by dated release snapshot, so monthly and annual
  revisions never overwrite earlier decision-time facts;
- each pair must match the exact 62- or 63-page PDF structure, dimension multiset, rotations,
  nonblank text layers, identifying metadata, release header, headline, revision language,
  methodology markers, 31-member ZIP inventory, and Exhibit 1 labels and exact million-dollar
  rows;
- the rounded one-decimal-billion PDF headline is checked against, but never substituted for,
  exact Exhibit 1 values;
- New York calendar rules convert each stated `8:30 a.m. EST/EDT` release time into an exact UTC
  availability boundary; current HTTP dates remain retrieval metadata and are never backdated;
- the PDF says headline statistical significance is not applicable or measurable. That statement
  is not converted into a probability, confidence interval, or predictive claim;
- figures are seasonally adjusted but not adjusted for price changes;
- goods statistics are a complete enumeration of collected CBP documents and are not subject to
  sampling error, but nonsampling errors and services-estimation limitations remain material;
- the March report's COVID-19 language says the estimates met publication standards. It does not
  establish causality, complete response, unaffected measurement, forecast validity, or impact;
- full PDFs and XLS ZIPs remain in ignored content-addressed storage. Committed receipts retain
  hashes, URLs, sizes, retrieval times, warnings, and release-snapshot semantics.

Rebuild the live evidence and summary with:

```bash
.venv/bin/python scripts/validate_census_ft900.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/census-ft900/live \
  --raw-store data/raw/supporting/census-ft900 \
  --output verification/supporting/census-ft900/latest-summary.json
```

Two consecutive live runs are retained: the first inserts all three fact versions and the second
proves all three are idempotent. This evidence does not establish causal attribution, universal
trade measurement, price-adjusted volume, calibrated forecast coverage, external validation,
deployment, investment performance, or user impact.
