# BEA archived Personal Income and Outlays supporting evidence

This directory proves live retrieval and strict paired-form validation of three archived U.S.
Bureau of Economic Analysis Personal Income and Outlays releases selected for a March 2020
personal-saving-rate boundary. It is separate from the capped formal adapter inventory:

- `bea.pio.archived_personal_saving_rate` is a scenario-specific supporting source, not a
  thirty-first counted adapter;
- the February 28 release reports a `7.9%` January personal saving rate and `$1.33 trillion` in
  personal saving;
- the March 27 release reports an `8.2%` February rate and `$1.38 trillion` in personal saving;
- the April 30 release reports a `13.1%` March rate and `$2.17 trillion` in personal saving, while
  revising February from `8.2%` to `8.0%`;
- each official HTML/PDF pair must match on release identity, reference month, 8:30 a.m.
  `America/New_York` embargo time, headline changes, saving rate, and personal-saving amount;
- the PDF page counts are locked at 11, 11, and 12, and each Table 1 snapshot is independently
  checked against the headline facts;
- the release-time zone changes from EST for January to EDT for February and March, producing
  exact UTC availability times of `13:30`, `12:30`, and `12:30` respectively;
- a later revised February value never overwrites the earlier March 27 release snapshot;
- full HTML/PDF pairs remain in ignored content-addressed storage; the committed receipt retains
  hashes, URLs, sizes, retrieval times, warnings, and normalized fact hashes.

Rebuild the live evidence and summary with:

```bash
python scripts/validate_bea_pio.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/bea-pio/live \
  --raw-store data/raw/supporting/bea-pio \
  --output verification/supporting/bea-pio/latest-summary.json
```

This evidence supports the fifteenth scenario through a separate immutable input lock, disjoint
post-decision event lock, four-engine ReplayPack, and clean-worktree double-rebuild receipt. It
establishes internal source retrieval, paired HTML/PDF identity, release-snapshot preservation,
conservative timing, hashing, local ingestion, and deterministic reproduction. It does not
establish a forecast, calibrated probability, pandemic or household-behavior causality, external
validation, deployment, investment performance, or user impact.
