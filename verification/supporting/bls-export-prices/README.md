# BLS Export Price Index supporting evidence

This directory contains the committed live receipts used by the March 2020 all-export-price
boundary scenario. The adapter retrieves only three explicitly approved archived BLS release
pairs: February 14, March 13, and April 14, 2020. Each pair contains one complete HTML release and
one complete 18-page PDF.

The adapter independently validates both formats, exact 8:30 a.m. EST/EDT embargo timing, release
identity, headline all-export monthly change, prior-month revision lineage, Table 2 values, the
modified-Laspeyres and non-seasonal-adjustment technical note, the three-month revision policy,
Schedule B and f.a.s./f.o.b. export-price scope, and the March COVID-19 survey-methodology
statement. Later revisions remain in later snapshots and never replace the first-reported January
or February decision inputs.

Raw HTML/PDF bytes are stored only in the ignored local content-addressed store. The repository
keeps response hashes, source versions, normalized facts, timestamps, warnings, and self-hashed
receipts. BLS material is public domain except identified third-party material; attribution and
source links are retained, and the BLS emblem is not used.

Reproduce the live evidence and deterministic summary with:

```bash
PYTHONPATH=src .venv/bin/python scripts/validate_bls_export_prices.py
PYTHONPATH=src .venv/bin/python scripts/validate_bls_export_prices.py
PYTHONPATH=src .venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/bls-export-prices/live \
  --raw-store data/raw/supporting/bls-export-prices \
  --output verification/supporting/bls-export-prices/latest-summary.json
```

The second validation must report three idempotent records. The summary reopens every selected raw
artifact by SHA-256. This establishes current reproducibility of archived official evidence, not
historical retrieval in 2020, forecast skill, calibrated coverage, trade-volume measurement,
firm-level behavior, causality, external validation, deployment, or user impact.
