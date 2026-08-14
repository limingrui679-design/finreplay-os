# Census/HUD NRS archived-release supporting evidence

This directory proves live retrieval and strict validation of three archived U.S. Census
Bureau/U.S. Department of Housing and Urban Development New Residential Sales releases selected
for a March 2020 new-house-sales boundary. It is separate from the capped formal adapter inventory:

- `census.hud.archived_new_residential_sales` is a scenario-specific supporting source, not a
  thirty-first counted adapter;
- the February 26 release initially reports January new single-family houses sold at a seasonally
  adjusted annual rate of `764,000` and gives revised December as `708,000`;
- the March 24 decision snapshot reports February at `765,000` and revises January to `800,000`;
  the earlier `764,000` January headline remains only as revision lineage;
- the April 23 event snapshot reports March at `627,000`, revises February to `741,000`, and does
  not overwrite the decision-time `765,000` value;
- every official PDF must have exactly five nonblank `612 x 792` pages, the exact page-title
  sequence, release identity and number, one 10:00 a.m. EST/EDT label, headline facts,
  explanatory notes, and matching Table 1a national value, revised prior, monthly change,
  sampling margin, and average RSE;
- official 90-percent sampling-confidence intervals remain reported source metadata and are never
  relabeled as a FinReplay forecast range, calibrated interval, or probability;
- the source defines a sale as a deposit taken or sales agreement signed, which may precede permit
  issuance; it is not necessarily a closing, mortgage, or completed transaction;
- the April COVID-19 wording states only that estimates met publication standards and does not
  prove causality, complete response, or unaffected measurement;
- full PDFs remain in ignored content-addressed storage; committed receipts retain hashes, URLs,
  sizes, retrieval times, warnings, and versioned-snapshot semantics.

Rebuild the live evidence and summary with:

```bash
.venv/bin/python scripts/validate_census_nrs.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/census-nrs/live \
  --raw-store data/raw/supporting/census-nrs \
  --output verification/supporting/census-nrs/latest-summary.json
```

Two consecutive live runs are retained: the first inserts all three fact versions and the second
proves all three are idempotent. This evidence does not establish a probability forecast,
calibrated coverage, property- or builder-level outcomes, housing-market or pandemic causality,
external validation, deployment, investment performance, or user impact.
