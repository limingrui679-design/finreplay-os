# TreasuryDirect archived 91-day bill auction-result evidence

This directory proves live retrieval and strict paired-form validation of three March 2020 U.S.
Treasury 91-day bill auction results selected for a zero-rate boundary. It is separate from the
capped formal adapter inventory:

- `treasury.auctions.archived_91_day_bill_results` is a scenario-specific supporting source, not
  a thirty-first counted adapter;
- the March 9 result reports CUSIP `912796TZ2`, a `0.390%` high discount rate, `99.901417`
  price, `2.74` bid-to-cover ratio, and XML release time `11:32`;
- the March 16 result reports CUSIP `912796SV2`, a `0.290%` high discount rate, `99.926694`
  price, `2.58` bid-to-cover ratio, and XML release time `11:32`;
- the March 23 result reports CUSIP `912796UA5`, a `0.000%` high discount rate, `100.000000`
  price, `3.11` bid-to-cover ratio, and XML release time `11:31`;
- each official XML must identify one 13-week/91-day single-price bill auction, its fixed calendar,
  CUSIP, closing times, result filename, release time, rates, price, amounts, and bidder classes;
- bidder-category amounts, subtotal and total amounts, bid-to-cover ratio, and bill-price formula
  must reconcile exactly;
- each paired one-page PDF must independently match the XML on identity, rates, price, tender and
  award amounts, bid-to-cover arithmetic, and TreasuryDirect award;
- Treasury's auction timeline says that since 2003 the recorded XML delivery time is the official
  auction release time; FinReplay retains it but waits until the following
  `America/New_York` midnight before knowledge eligibility;
- current `Last-Modified` headers are ignored because later site migration can change them;
- full XML/PDF pairs remain in ignored content-addressed storage; the committed receipt retains
  hashes, URLs, sizes, retrieval times, warnings, and normalized fact hashes.

Rebuild the live evidence and summary with:

```bash
python scripts/validate_treasury_auction_results.py
python scripts/verify_live_receipts.py \
  --directory verification/supporting/treasury-auction-results/live \
  --raw-store data/raw/supporting/treasury-auction-results \
  --output verification/supporting/treasury-auction-results/latest-summary.json
```

This evidence now supports the fourteenth counted scenario through a separate immutable input
lock, disjoint post-decision event lock, four-engine ReplayPack, clean-worktree double-rebuild
receipt, and eight-gate scenario proof. That count establishes internal source retrieval, paired
XML/PDF identity, arithmetic, conservative timing, hashing, local ingestion, and deterministic
reproduction. It does not establish a forecast, calibrated range, auction-demand causality,
policy effectiveness, external validation, deployment, investment performance, or user impact.
