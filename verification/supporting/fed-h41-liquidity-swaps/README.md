# Federal Reserve H.4.1 liquidity-swap supporting evidence

This directory verifies three paired archived Federal Reserve H.4.1 HTML and ASCII releases used
by the central-bank-liquidity-swaps boundary scenario. It is a scenario-specific source outside
the capped formal 30-adapter inventory:

- release dates are fixed to 2020-03-19, 2020-03-26, and 2020-04-02; the adapter never crawls or
  enumerates the H.4.1 archive;
- each complete official HTML/ASCII pair must agree on the Table 1 weekly average, weekly and
  year-over-year average changes, and Wednesday outstanding central-bank-liquidity-swap balance;
- the selected Wednesday balances are `45`, `206,051`, and `348,544` million dollars;
- March 19 and 26 explicitly state 4:30 p.m. EDT in the archived HTML, which is validated against
  `America/New_York`; the April 2 pair states only its release date, so that event becomes eligible
  at the following New York midnight rather than inheriting an unproved exact time;
- the official stated time is not represented as an independently measured server-publication
  log, and current retrieval hashes are never backdated to 2020;
- H.4.1's footnote says the dollar value uses the exchange rate used when the foreign currency was
  acquired and to be used when it is returned. The scenario does not convert the balance into a
  current-market exposure, counterparty loss, P&L, transaction, institution, or causal estimate;
- dynamic current HTML wrapper bytes remain in raw receipts and local content-addressed storage.
  Financial records bind normalized cross-format release semantics, so changing injected assets
  cannot mutate the historical fact.

Rebuild twice and verify the newest live receipt with:

```bash
.venv/bin/python scripts/validate_fed_h41_liquidity_swaps.py
.venv/bin/python scripts/validate_fed_h41_liquidity_swaps.py
.venv/bin/python scripts/verify_live_receipts.py \
  --directory verification/supporting/fed-h41-liquidity-swaps/live \
  --raw-store data/raw/supporting/fed-h41-liquidity-swaps \
  --output verification/supporting/fed-h41-liquidity-swaps/latest-summary.json
```

The second live run must report three idempotent records. This establishes current official
retrieval, paired-format agreement, content addressing, conservative knowledge timing, and local
idempotence. It does not establish forecast skill, calibrated coverage, systemic-stress causality,
policy effectiveness, external validation, deployment, investment performance, or user impact.
