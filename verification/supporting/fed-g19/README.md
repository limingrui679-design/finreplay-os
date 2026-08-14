# Federal Reserve G.19 archived-release supporting evidence

This directory proves live retrieval and strict validation of three archived Board of Governors
Consumer Credit releases selected for a March 2020 revolving-credit boundary. It is separate
from the capped formal adapter inventory:

- `federalreserve.g19.archived_consumer_credit` is a scenario-specific supporting source, not a
  thirty-first counted adapter;
- the March 6 release reports January revolving-credit growth at a preliminary `-3.3%` simple
  annual rate;
- the April 7 release revises January to `-2.7%` and reports February at a preliminary `4.6%`;
- the May 7 release revises January to `-3.7%`, revises February to `3.6%`, and reports March at
  a preliminary `-30.9%`;
- every logical month is versioned by its release snapshot, so the April decision view remains
  `January = -2.7%` and `February = 4.6%` after the May revisions arrive;
- every PDF must have exactly four nonblank `612 x 792` pages rotated 90 degrees, matching
  release identity, exact 3 p.m. Eastern Time, headline, all nine seasonally adjusted table
  rows, levels/flows page identities, the simple-annual-rate footnote, and the `r/p` legend;
- the adapter stores the table's one-decimal simple annual rates rather than substituting the
  rounded fractional wording used in the headline;
- G.19 revolving credit includes most credit-card loans but also other revolving plans; no
  card-spending, household-behavior, policy, or pandemic causality is inferred;
- full PDFs remain in ignored content-addressed storage; committed receipts retain hashes,
  URLs, sizes, retrieval times, warnings, and versioned snapshot semantics.

Rebuild the live evidence and summary with:

```bash
.venv/bin/python scripts/validate_fed_g19.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/fed-g19/live \
  --raw-store data/raw/supporting/fed-g19 \
  --output verification/supporting/fed-g19/latest-summary.json
```

Two consecutive live runs are retained: the first inserts all six fact versions and the second
proves they are idempotent. This evidence does not establish a forecast, calibrated probability,
consumer or pandemic causality, external validation, deployment, investment performance, or
user impact.
