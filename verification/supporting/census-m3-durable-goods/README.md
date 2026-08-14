# Census M3 advance durable-goods supporting evidence

This directory proves live retrieval and strict validation of three archived Census M3 Advance
Report PDFs selected for a March 2020 durable-goods new-orders boundary. It is separate from the
capped formal adapter inventory:

- `census.m3.archived_advance_durable_goods` is a scenario-specific supporting source, not a
  thirty-first counted adapter;
- the February 27 report initially records January total durable-goods new orders as `-0.2%` and
  `$246,199 million`;
- the March 25 report revises January to `+0.1%` and initially records February as `+1.2%` and
  `$249,409 million`;
- the April 24 report retains January at `+0.1%`, revises February to `+1.1%`, and initially
  records March as `-14.4%` and `$213,184 million`;
- every reference month is versioned by report snapshot, so later advance reports and benchmarks
  never overwrite the earlier facts;
- each PDF must match its exact seven-page structure, geometry, rotations, nonblank text layers,
  identifying metadata, release header, headline, future-release schedule, explanatory notes,
  benchmark notice, and exact Table 1 and Table 2 cross-check values;
- New York calendar rules turn the stated `8:30 a.m. EST/EDT` times into exact UTC availability
  boundaries. The January report forecasts the February report with `EST`, while the actual
  March 25 report states `EDT`; both source statements are preserved rather than harmonized;
- current HTTP headers are retrieval metadata only and are never backdated;
- all three archived PDFs have modification metadata later than their stated release, so the
  reported values are treated as official archived-report facts, not proof that the current bytes
  are unchanged from release time;
- M3 is not a probability sample, so sampling error, confidence intervals, and headline
  statistical significance cannot be computed from these reports;
- figures are seasonally adjusted but not adjusted for inflation or price changes;
- the March report's COVID-19 language states that the estimates met publication standards. It
  does not establish causality, full response, unaffected measurement, or forecast validity;
- full report PDFs remain in ignored content-addressed storage. Committed receipts retain hashes,
  URLs, sizes, retrieval times, warnings, and release-snapshot semantics.

Rebuild the live evidence and summary with:

```bash
.venv/bin/python scripts/validate_census_durable_goods.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/census-m3-durable-goods/live \
  --raw-store data/raw/supporting/census-m3-durable-goods \
  --output verification/supporting/census-m3-durable-goods/latest-summary.json
```

Two consecutive live runs are retained: the first inserts all three fact versions and the second
proves all three are idempotent. This evidence does not establish causal attribution, a universal
manufacturing measure, inflation-adjusted changes, calibrated forecast coverage, external
validation, deployment, investment performance, or user impact.
