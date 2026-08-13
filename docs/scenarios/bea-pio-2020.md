# March 2020 BEA personal-saving-rate boundary replay

This counted scenario places a historical decision boundary at 2020-04-01 12:00 UTC. Its two
inputs are the January and February personal saving rates reported in archived U.S. Bureau of
Economic Analysis Personal Income and Outlays releases. The March value is locked separately as
a post-decision event and is absent from every ReplayPack source record.

The source is the U.S. Bureau of Economic Analysis. This case does not claim that FinReplay
existed at the historical decision time, observe household-level behavior or transactions,
identify a causal pandemic or policy mechanism, or predict a future saving rate.

## Paired official releases and knowledge boundary

`scenarios/bea-pio-2020/input-lock.json` contains:

- February 28 release for January: personal saving rate `7.9%` or `790` basis points, personal
  saving `$1.33 trillion`, and release number `BEA 20-08`;
- March 27 release for February: personal saving rate `8.2%` or `820` basis points, personal
  saving `$1.38 trillion`, and release number `BEA 20-14`.

For each release, the supporting adapter retrieves the official archived HTML and PDF. It
requires both forms to match on release identity, reference month, 8:30 a.m. Eastern embargo,
headline income and spending changes, personal saving rate, and personal-saving amount. The two
PDFs must each contain exactly 11 pages, and their Table 1 snapshots are checked independently
against the headline facts.

The January release states 8:30 a.m. EST, making its exact availability time
2020-02-28 13:30 UTC. The February release states 8:30 a.m. EDT, making its exact availability
time 2020-03-27 12:30 UTC. Both precede the decision boundary. The time-zone conversion is checked
under `America/New_York`; the repository does not infer publication from a current server header.

ShockCompiler uses only the two decision-time release snapshots:

- latest-known persistence baseline: `820` basis points;
- one known January-to-February increase: `30` basis points;
- stress endpoints: persistence at `820`, or one repeat of the known increase at `850`;
- range width: `30` basis points;
- probability assigned: none.

The range is not a forecast, confidence interval, calibrated coverage statement, household model,
macroeconomic causal model, trading signal, or policy recommendation.

## Disjoint post-decision breach and revision

`scenarios/bea-pio-2020/event-lock.json` contains the April 30 release for March: personal saving
rate `13.1%` or `1,310` basis points, personal saving `$2.17 trillion`, and release number
`BEA 20-20`. Its paired HTML and 12-page PDF state an 8:30 a.m. EDT embargo, making the fact
available at 2020-04-30 12:30 UTC, after the decision boundary.

The event release also revises February from the earlier `8.2%` snapshot to `8.0%`, a change of
`-20` basis points. The event record retains both values; the input lock remains `820` basis
points and is never overwritten. The reported March rate is `460` basis points above the
previously declared upper endpoint of `850`. The verifier requires that miss to remain visible.
It does not widen the range, relabel the outcome as a success, or leak the outcome or revision
into the pack. A transparent miss is valid reproducibility evidence but strongly negative
evidence for coverage or forecast claims.

## Four relevant engines

TimeVault reconstructs the two-release decision set; ShockCompiler compiles the no-probability
persistence-or-one-known-increase endpoints; TrialCourt retains and rejects a retrospective
one-increase attempt; ReplayStudio exports a deterministic human- and machine-readable pack.
MarketTwin, ExecutionLab, and CapitalAllocator are absent because no household network, position,
order, execution, portfolio, allocation, or return is represented.

## Rebuild and counted proof

```bash
python scripts/build_bea_saving_rate_boundary_replaypack.py \
  --input-lock scenarios/bea-pio-2020/input-lock.json \
  --output verification/replaypacks/bea-pio-2020

python scripts/verify_bea_saving_rate_boundary_replaypack.py \
  --input-lock scenarios/bea-pio-2020/input-lock.json \
  --event-lock scenarios/bea-pio-2020/event-lock.json \
  --pack verification/replaypacks/bea-pio-2020 \
  --receipt verification/evidence/bea-pio-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 21 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/bea-pio-2020-saving-rate-boundary-v1.json` binds the
supporting inventory, source hashes, locks, scripts, pack, receipt, truth labels, persistence
baseline, no-probability marker, TrialCourt rejection, exact event identity, later revision
isolation, and required `460`-basis-point breach. This establishes internal reproducibility
only—not forecast skill, calibrated coverage, household behavior, pandemic or policy causality,
external validation, deployment, investment performance, or user impact.
