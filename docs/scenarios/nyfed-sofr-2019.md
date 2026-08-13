# September 2019 New York Fed SOFR spike boundary replay

This counted scenario places a historical decision boundary at 2019-09-17 20:00 UTC. Its two
inputs are the final SOFR rates effective September 13 and 16 from the official New York Fed
Markets API. The September 17 rate is locked separately as a post-decision event and is absent
from every ReplayPack source record.

The source is the Federal Reserve Bank of New York. This case does not claim that FinReplay existed
at the historical decision time, identify transaction-level repo activity, or attribute the rate
movement to any cause.

## Locked rates and finality boundary

`scenarios/nyfed-sofr-2019/input-lock.json` contains:

- September 13 final SOFR: `2.20%`, normalized exactly to `220` basis points;
- September 16 final SOFR: `2.43%`, normalized exactly to `243` basis points.

The New York Fed states that SOFR is published at approximately 8:00 a.m. Eastern on the following
business day and that a qualifying revision, if any, is published at approximately 2:30 p.m. on
that same day. FinReplay therefore permits a final historical rate only from 3:00 p.m. in
`America/New_York`, after the same-day revision window. This is a conservative finality boundary,
not the exact publication instant. The September 16 effective rate is finality-eligible at
2019-09-17 19:00 UTC, one hour before the decision boundary.

Only the final aggregate rate is normalized. Lagged percentile statistics remain in the hashed
source response for validation but are excluded from the historical fact because they can differ
from same-day values.

ShockCompiler uses only the two already-known reported rates:

- latest-known persistence baseline: `243` basis points;
- stress endpoints: `220` and `243` basis points;
- range width: `23` basis points;
- probability assigned: none.

The range is not a forecast, confidence interval, calibrated coverage statement, funding-stress
probability, repo-market causal model, trading signal, or policy recommendation.

## Disjoint post-decision breach

`scenarios/nyfed-sofr-2019/event-lock.json` contains the final SOFR rate effective September 17:
`5.25%`, or `525` basis points. Under the same post-revision-window rule, it becomes finality-
eligible at 2019-09-18 19:00 UTC, after the decision boundary.

The reported event is `282` basis points above the declared upper endpoint of `243`. The verifier
requires this breach to remain visible. It does not widen the interval, relabel the outcome as a
success, or leak the event into the pack. A transparent miss is valid reproducibility evidence but
is negative evidence for coverage or forecast claims.

## Four relevant engines

TimeVault reconstructs the two-rate decision set; ShockCompiler compiles the no-probability
endpoints; TrialCourt retains and rejects a retrospective two-rate attempt; ReplayStudio exports a
deterministic human- and machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator are
absent because no exposure network, order, execution, portfolio, allocation, or return is
represented.

## Rebuild and counted proof

```bash
python scripts/build_sofr_boundary_replaypack.py \
  --input-lock scenarios/nyfed-sofr-2019/input-lock.json \
  --output verification/replaypacks/nyfed-sofr-2019

python scripts/verify_sofr_boundary_replaypack.py \
  --input-lock scenarios/nyfed-sofr-2019/input-lock.json \
  --event-lock scenarios/nyfed-sofr-2019/event-lock.json \
  --pack verification/replaypacks/nyfed-sofr-2019 \
  --receipt verification/evidence/nyfed-sofr-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 18 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/nyfed-sofr-2019-spike-boundary-v1.json` binds the supporting
inventory, locks, scripts, pack, receipt, truth labels, persistence baseline, no-probability marker,
TrialCourt rejection, exact event identity, isolation, and required 282-basis-point breach. This
establishes internal reproducibility only—not forecast skill, calibrated coverage, repo-market
causality, policy effectiveness, external validation, deployment, investment performance, or user
impact.
