# Federal Reserve H.4.1 supporting-source evidence

This directory verifies three date-stamped Federal Reserve H.4.1 releases used by the BTFP growth
boundary scenario. It is separate from the capped formal adapter inventory:

- the formal target remains exactly 30 adapters under `verification/live/`;
- this is a scenario-specific supporting official source, not a thirty-first counted adapter;
- release pages dated 2023-03-16, 2023-03-23, and 2023-03-30 produced six validated facts;
- full archived HTML remains only in the ignored local content-addressed store;
- committed locks may preserve minimal BTFP values, source hashes, links, and timing metadata;
- each date-only release becomes knowable at 00:00 UTC two calendar days later, a conservative
  deterministic bound rather than a claimed intraday publication timestamp.

Rebuild the local source evidence with:

```bash
.venv/bin/python scripts/validate_fed_h41_btfp.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/fed-h41/live \
  --raw-store data/raw/supporting/fed-h41 \
  --output verification/supporting/fed-h41/latest-summary.json
```

The receipt proves live retrieval, strict parsing, hashing, and local TimeVault insertion. It does
not prove systemic-stress causality, a forecast, investment or policy performance, external
validation, deployment, or user impact.
