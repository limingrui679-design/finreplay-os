# Census C30 archived-release supporting evidence

This directory proves live retrieval and strict paired-form validation of three archived U.S.
Census Bureau Monthly Construction Spending releases selected for a March 2020 construction-value
boundary. It is separate from the capped formal adapter inventory:

- `census.c30.archived_construction_spending` is a scenario-specific supporting source, not a
  thirty-first counted adapter;
- the March 2 release reports January total construction at a preliminary seasonally adjusted
  annual rate of `$1,369,223 million`;
- the April 1 release revises January to `$1,384,486 million` and reports February at a
  preliminary `$1,366,697 million`;
- the May 1 release revises January to `$1,382,963 million`, revises February to
  `$1,348,386 million`, and reports March at a preliminary `$1,360,512 million`;
- every logical reference month is versioned by release snapshot, so later revisions never
  overwrite what was knowable at the April 1 decision time;
- each six-page PDF must match exact page geometry and order, release identity and 10:00 a.m.
  EST/EDT time, headline, Tables 1–4 where present, complete total-row sampling statistics,
  methodology language, revision notices, and the May COVID-19 publication-standards statement;
- each XLSX must pass bounded ZIP, CRC, path, compression-ratio, relationship, content-type,
  sheet-order, dimension, cell-type, and duplicate-reference checks before fixed cells are read;
- PDF headline, table values, Excel cells, private/public components, month arithmetic, Table 2,
  Table 3, and available Table 4 facts must cross-check before any record is emitted;
- exact Table 1 million-dollar levels are retained; rounded billion-dollar headline values are
  corroboration only;
- values are nominal annual rates adjusted for seasonality but not price changes, so they are not
  real construction volume, investment returns, or transaction counts;
- Census 90-percent intervals describe sampling variability and are not FinReplay forecast
  ranges, probabilities, or causal evidence;
- full PDF/XLSX pairs remain in ignored content-addressed storage; committed receipts retain
  hashes, URLs, sizes, retrieval times, warnings, and versioned snapshot semantics.

Rebuild the live evidence and summary with:

```bash
.venv/bin/python scripts/validate_census_c30.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/census-c30/live \
  --raw-store data/raw/supporting/census-c30 \
  --output verification/supporting/census-c30/latest-summary.json
```

Two consecutive live runs are retained: the first inserts all six fact versions and the second
proves all six are idempotent. This evidence does not establish a forecast, calibrated
probability, construction or pandemic causality, inflation-adjusted activity, external
validation, deployment, investment performance, or user impact.
