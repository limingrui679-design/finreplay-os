# Census/HUD NRC archived-release supporting evidence

This directory proves live retrieval and strict validation of three archived U.S. Census
Bureau/U.S. Department of Housing and Urban Development New Residential Construction releases
selected for a March 2020 housing-starts boundary. It is separate from the capped formal adapter
inventory and supports the eighteenth scenario accepted by the eight-gate catalog verifier:

- `census.hud.archived_new_residential_construction` is a scenario-specific supporting source,
  not a thirty-first counted adapter;
- the February 19 release reports the preliminary January total housing-starts seasonally
  adjusted annual rate as `1,567,000` units and revises December to `1,626,000`;
- the March 18 release reports the preliminary February rate as `1,599,000` and revises January
  to `1,624,000`; the earlier January headline remains a separate release snapshot;
- the April 16 release reports the preliminary March rate as `1,216,000` and revises February
  from its earlier `1,599,000` headline to `1,564,000`;
- each official PDF must have exactly seven nonblank `612 x 792` pages, the exact release
  identity, page-title sequence, release-number/time, headline facts, explanatory notes, and
  matching Table 3a total-series value, revised prior, monthly change, sampling margin, and RSE;
- the exact 8:30 a.m. EST/EDT time printed in each PDF is checked under `America/New_York`;
  current HTTP headers are never backdated into historical availability evidence;
- official 90-percent sampling-confidence intervals remain reported statistical metadata and
  are never relabeled as a FinReplay forecast range or probability;
- full PDFs remain in ignored content-addressed storage; committed receipts retain hashes,
  URLs, sizes, retrieval times, warnings, and snapshot semantics.

Rebuild the live evidence and summary with:

```bash
python scripts/validate_census_nrc.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/census-nrc/live \
  --raw-store data/raw/supporting/census-nrc \
  --output verification/supporting/census-nrc/latest-summary.json
```

Two consecutive live runs are retained: the first inserts the three normalized facts and the
second proves all three are idempotent. The counted proof at
`verification/scenarios/proofs/census-nrc-2020-housing-starts-boundary-v1.json` separately binds
the two decision inputs, post-decision event, four-engine pack, and clean double rebuild. This
evidence does not establish a forecast, calibrated
probability, housing-market or pandemic causality, external validation, deployment, investment
performance, or user impact.
