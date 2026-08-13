# BLS CPI supporting-source evidence

This directory verifies three date-stamped BLS Consumer Price Index archive pages used by the
CPI-release boundary scenario. It is separate from the capped formal adapter inventory:

- the formal target remains exactly 30 adapters under `verification/live/`;
- this is a scenario-specific supporting official source, not a thirty-first counted adapter;
- releases dated 2023-01-12, 2023-02-14, and 2023-03-14 produce six validated CPI-U facts;
- the adapter parses the stated 8:30 a.m. Eastern Time embargo end and converts it to UTC;
- archived values remain release-snapshot facts and are not replaced by later revised series;
- full HTML remains in ignored local content-addressed storage; committed locks retain minimal
  facts, archive links, hashes, and timing metadata.

Rebuild the local source evidence with:

```bash
.venv/bin/python scripts/validate_bls_cpi_release.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/bls-cpi/live \
  --raw-store data/raw/supporting/bls-cpi \
  --output verification/supporting/bls-cpi/latest-summary.json
```

The February 2023 release says the CPI weights were updated and the previous five years of
seasonally adjusted indexes were recalculated. The receipt therefore proves exact archived
reporting and knowledge timing, not that adjacent headline changes form a stationary sample. It
does not prove forecast skill, calibrated coverage, causal inflation attribution, investment or
policy performance, external validation, deployment, or user impact.
