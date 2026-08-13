# Federal Reserve archived G.17 supporting evidence

This directory proves live retrieval and strict paired-form validation of three archived Board of
Governors Industrial Production and Capacity Utilization releases selected for a March 2020
industrial-production boundary. It is separate from the capped formal adapter inventory:

- `federalreserve.g17.archived_industrial_production` is a scenario-specific supporting source,
  not a thirty-first counted adapter;
- the February 14 release reports that total industrial production declined `0.3%` in January,
  with a total index of `109.2` and industrial capacity utilization of `76.8%`;
- the March 17 release reports a `0.6%` February increase and revises January from `-0.3%` to
  `-0.5%`;
- the April 15 release reports a `5.4%` March decline and revises February from `0.6%` to `0.5%`;
- each official HTML/PDF pair must match on release identity, reference month, headline change,
  total index, capacity utilization, manufacturing, mining, utilities, and the summary table;
- each PDF must contain exactly 19 nonblank US Letter pages and state one 9:15 a.m. EST/EDT
  release time, which is checked under `America/New_York`;
- earlier January and February snapshots are never overwritten by values in later releases;
- the official HTML shell contains changing Cloudflare tokens, so every raw response hash remains
  in its fetch receipt while a stable normalized fact hash and canonical PDF hash identify the
  economic release snapshot;
- full HTML/PDF pairs remain in ignored content-addressed storage; the committed receipts retain
  hashes, URLs, sizes, retrieval times, warnings, and normalized fact hashes.

Rebuild the live evidence and summary with:

```bash
python scripts/validate_fed_g17.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/fed-g17/live \
  --raw-store data/raw/supporting/fed-g17 \
  --output verification/supporting/fed-g17/latest-summary.json
```

Two consecutive live runs are retained: the first inserts the three normalized facts and the
second proves they are idempotent even though all three HTML response hashes change. This is the
supporting source for the sixteenth counted scenario. Its immutable locks, ReplayPack, clean
double-rebuild receipt, and eight-gate proof bind the same three source snapshots independently.
This evidence does not establish a forecast, calibrated probability, pandemic or industrial
causality, external validation, deployment, investment performance, or user impact.
