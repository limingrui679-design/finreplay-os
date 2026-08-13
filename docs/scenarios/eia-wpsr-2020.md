# April 2020 EIA commercial-crude-stock boundary replay

This counted scenario places a historical decision boundary at 2020-04-16 12:00 UTC. Its two
inputs are archived U.S. commercial crude oil stocks excluding the Strategic Petroleum Reserve
for the weeks ending April 3 and 10, reported in the U.S. Energy Information Administration's
Weekly Petroleum Status Report. The April 17 stock is locked separately as a post-decision event
and is absent from every ReplayPack source record.

The source is the U.S. Energy Information Administration. This case does not claim that FinReplay
existed at the historical decision time, measure individual tanks or transactions, establish
storage-capacity constraints, or attribute inventory changes to the pandemic or any market cause.

## Paired archives and knowledge boundary

`scenarios/eia-wpsr-2020/input-lock.json` binds both the official Table 4 CSV and the full archived
WPSR PDF for each pre-decision release:

- April 8 release, week ending April 3: `484,370` thousand barrels, up `15,177` from `469,193`;
- April 15 release, week ending April 10: `503,618` thousand barrels, up `19,248` from `484,370`.

The CSV preserves the exact thousand-barrel values. The paired PDF independently binds the release
identity, publication schedule, and corresponding Table 4 values rounded to one decimal million
barrels. The source adapter rejects a pair unless the release dates, official URLs and
`Last-Modified` dates agree, the CSV arithmetic reconciles exactly, and the PDF rounds to the same
values.

The archived report says Tables 1–14 are released after 10:30 a.m. Eastern on Wednesdays, with
possible holiday delays. That language does not establish an exact publication instant. FinReplay
therefore waits until the next local midnight in `America/New_York`: the two inputs become eligible
at 2020-04-09 04:00 UTC and 2020-04-16 04:00 UTC. Both precede the decision boundary. This is a
conservative availability rule, not a claim about the actual posting second.

ShockCompiler uses only the two already-known reported stocks:

- latest-known persistence baseline: `503,618` thousand barrels;
- stress endpoints: `484,370` and `503,618` thousand barrels;
- range width: `19,248` thousand barrels;
- probability assigned: none.

The range is not a forecast, confidence interval, calibrated coverage statement, storage-capacity
estimate, oil-market causal model, trading signal, or policy recommendation.

## Disjoint post-decision breach

`scenarios/eia-wpsr-2020/event-lock.json` contains the April 22 release for the week ending April
17: `518,640` thousand barrels, up `15,022` from `503,618`. Under the same conservative rule it
becomes eligible at 2020-04-23 04:00 UTC, after the decision boundary.

The reported event is `15,022` thousand barrels above the declared upper endpoint of `503,618`.
The verifier requires this breach to remain visible. It does not widen the range, relabel the
outcome as a success, or leak the event into the pack. A transparent miss is valid reproducibility
evidence but negative evidence for coverage or forecast claims.

## Four relevant engines

TimeVault reconstructs the two-release decision set; ShockCompiler compiles the no-probability
endpoints; TrialCourt retains and rejects a retrospective two-release attempt; ReplayStudio
exports a deterministic human- and machine-readable pack. MarketTwin, ExecutionLab, and
CapitalAllocator are absent because no exposure network, order, execution, portfolio, allocation,
or return is represented.

## Rebuild and counted proof

```bash
python scripts/build_eia_crude_stock_boundary_replaypack.py \
  --input-lock scenarios/eia-wpsr-2020/input-lock.json \
  --output verification/replaypacks/eia-wpsr-2020

python scripts/verify_eia_crude_stock_boundary_replaypack.py \
  --input-lock scenarios/eia-wpsr-2020/input-lock.json \
  --event-lock scenarios/eia-wpsr-2020/event-lock.json \
  --pack verification/replaypacks/eia-wpsr-2020 \
  --receipt verification/evidence/eia-wpsr-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 18 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/eia-wpsr-2020-crude-stock-boundary-v1.json` binds the
supporting inventory, paired source hashes, locks, scripts, pack, receipt, truth labels,
persistence baseline, no-probability marker, TrialCourt rejection, exact event identity,
isolation, and required `15,022`-thousand-barrel breach. This establishes internal reproducibility
only—not forecast skill, calibrated coverage, storage capacity, oil-market causality, policy
effectiveness, external validation, deployment, investment performance, or user impact.
