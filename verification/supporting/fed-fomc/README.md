# Federal Reserve FOMC statement supporting-source evidence

This directory verifies three date-stamped FOMC statement pages used by the target-range boundary
scenario. It is separate from the capped formal adapter inventory:

- the formal target remains exactly 30 adapters under `verification/live/`;
- this is a scenario-specific supporting official source, not a thirty-first counted adapter;
- statements dated 2023-02-01, 2023-03-22, and 2023-05-03 produce six validated target endpoints;
- the adapter validates the stated 2:00 p.m. EST/EDT label against `America/New_York` and converts
  it to UTC;
- each target range remains a versioned policy-release snapshot;
- full HTML remains only in ignored local content-addressed storage; committed locks retain minimal
  facts, source links, hashes, and timing metadata.

Rebuild the local source evidence with:

```bash
.venv/bin/python scripts/validate_fed_fomc.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/fed-fomc/live \
  --raw-store data/raw/supporting/fed-fomc \
  --output verification/supporting/fed-fomc/latest-summary.json
```

The receipt proves exact archived reporting and knowledge timing. It does not prove a forecast,
the causal effects or correctness of monetary policy, investment performance, external validation,
deployment, or user impact.
