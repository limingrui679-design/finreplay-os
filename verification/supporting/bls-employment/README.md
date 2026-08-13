# BLS Employment Situation supporting-source evidence

This directory verifies three date-stamped BLS Employment Situation archive pages used by the
payroll-release boundary scenario. It is separate from the capped formal adapter inventory:

- the formal target remains exactly 30 adapters under `verification/live/`;
- this is a scenario-specific supporting official source, not a thirty-first counted adapter;
- releases dated 2023-01-06, 2023-02-03, and 2023-03-10 produce six validated headline facts;
- the adapter parses the stated 8:30 a.m. Eastern Time embargo end and converts it to UTC;
- archived values remain release-snapshot facts and are not replaced by later revised series;
- full HTML remains in ignored local content-addressed storage; committed locks retain minimal
  facts, archive links, hashes, and timing metadata.

Rebuild the local source evidence with:

```bash
.venv/bin/python scripts/validate_bls_employment.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/bls-employment/live \
  --raw-store data/raw/supporting/bls-employment \
  --output verification/supporting/bls-employment/latest-summary.json
```

The January 2023 release documents annual establishment-survey benchmarking and updated seasonal
adjustment factors. The receipt therefore proves exact archived reporting and knowledge timing,
not that adjacent headline changes form a stationary sample. It does not prove forecast skill,
calibrated coverage, causal labor-market attribution, investment or policy performance, external
validation, deployment, or user impact.
