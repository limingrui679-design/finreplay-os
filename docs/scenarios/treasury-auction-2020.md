# March 2020 Treasury 91-day bill zero-rate boundary replay

This counted scenario places a historical decision boundary at 2020-03-18 12:00 UTC. Its two
inputs are the high discount rates reported for the March 9 and March 16 U.S. Treasury 91-day bill
auctions. The March 23 zero-rate result is locked separately as a post-decision event and is absent
from every ReplayPack source record.

The source is the U.S. Department of the Treasury, Bureau of the Fiscal Service. This case does
not claim that FinReplay existed at the historical decision time, observe individual bids or
bidder motivations, identify a demand curve, or attribute auction results to a policy, liquidity,
market, or causal mechanism.

## Paired official results and knowledge boundary

`scenarios/treasury-auction-2020/input-lock.json` contains:

- March 9: CUSIP `912796TZ2`, high discount rate `0.390%` or `39` basis points, price
  `99.901417`, and bid-to-cover ratio `2.74`;
- March 16: CUSIP `912796SV2`, high discount rate `0.290%` or `29` basis points, price
  `99.926694`, and bid-to-cover ratio `2.58`.

For each auction, the supporting adapter retrieves an official result XML and its paired one-page
PDF. It requires the same CUSIP, auction calendar, security term, rates, price, tender and award
amounts, bidder-category totals, bid-to-cover arithmetic, and result filename across both forms.
The bill-price formula and subtotal/total arithmetic must also reconcile exactly.

Treasury's auction timeline says that since 2003 the recorded delivery time of the result XML is
the official auction release time. The selected XML files record `11:32` local time on March 9 and
March 16. FinReplay preserves those times under `America/New_York` but delays eligibility until the
following local midnight: 2020-03-10 04:00 UTC and 2020-03-17 04:00 UTC. Both precede the decision
boundary. Current `Last-Modified` headers are ignored because a later site migration can change
them; they are not backdated as historical evidence.

ShockCompiler uses only the two decision-time facts:

- latest-known persistence baseline: `29` basis points;
- one known March 9-to-March 16 decline: `10` basis points;
- stress endpoints: persistence at `29`, or one repeat of the known decline at `19`;
- range width: `10` basis points;
- probability assigned: none.

The zero floor is explicit but does not bind these two endpoints. The range is not a forecast,
confidence interval, calibrated coverage statement, bidder-demand model, monetary-policy model,
trading signal, or investment recommendation.

## Disjoint post-decision breach

`scenarios/treasury-auction-2020/event-lock.json` contains the March 23 result for CUSIP
`912796UA5`: high discount rate `0.000%` or `0` basis points, price `100.000000`, and bid-to-cover
ratio `3.11`. Its XML records an `11:31` local release time, and the same conservative rule makes
the pair eligible at 2020-03-24 04:00 UTC, after the decision boundary.

The reported event is `19` basis points below the previously declared lower endpoint. The verifier
requires this miss to remain visible. It does not widen the range, relabel the outcome as a
success, or leak the event into the pack. A transparent miss is valid reproducibility evidence but
strongly negative evidence for coverage or forecast claims.

## Four relevant engines

TimeVault reconstructs the two-auction decision set; ShockCompiler compiles the no-probability
persistence-or-one-known-decline endpoints; TrialCourt retains and rejects a retrospective
one-decline attempt; ReplayStudio exports a deterministic human- and machine-readable pack.
MarketTwin, ExecutionLab, and CapitalAllocator are absent because no bidder network, position,
order, execution, portfolio, allocation, or return is represented.

## Rebuild and counted proof

```bash
python scripts/build_treasury_auction_boundary_replaypack.py \
  --input-lock scenarios/treasury-auction-2020/input-lock.json \
  --output verification/replaypacks/treasury-auction-2020

python scripts/verify_treasury_auction_boundary_replaypack.py \
  --input-lock scenarios/treasury-auction-2020/input-lock.json \
  --event-lock scenarios/treasury-auction-2020/event-lock.json \
  --pack verification/replaypacks/treasury-auction-2020 \
  --receipt verification/evidence/treasury-auction-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 20 assertions over two fresh directory and ZIP rebuilds. The
proof at
`verification/scenarios/proofs/treasury-auction-2020-zero-rate-boundary-v1.json` binds the
supporting inventory, six source-file hashes, locks, scripts, pack, receipt, truth labels,
persistence baseline, no-probability marker, TrialCourt rejection, exact event identity, and the
required `19`-basis-point lower-bound breach. This establishes internal reproducibility only—not
forecast skill, calibrated coverage, bidder demand or policy causality, external validation,
deployment, investment performance, or user impact.
