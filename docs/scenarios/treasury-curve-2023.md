# U.S. Treasury 2-year/10-year inversion boundary replay

This counted scenario places a historical decision boundary at 2023-03-16 12:00 UTC. Its four
inputs are DGS2 and DGS10 observations for March 8 and March 13 from explicitly selected native
ALFRED vintages. The March 15 DGS2/DGS10 pair is locked separately as a post-decision event and is
absent from every ReplayPack source record.

The underlying series are Board of Governors H.15 constant-maturity market yields distributed
through FRED/ALFRED. This case does not claim that FinReplay existed at the historical decision
time.

## Locked decision inputs and derived spreads

`scenarios/treasury-curve-2023/input-lock.json` contains:

- March 8: DGS2 `505` basis points and DGS10 `398` basis points;
- March 13: DGS2 `403` basis points and DGS10 `355` basis points.

The reported yields remain `reported`. FinReplay derives DGS10 minus DGS2 as `inferred`:

- March 8 spread: `398 - 505 = -107` basis points;
- March 13 spread: `355 - 403 = -48` basis points.

ALFRED supplies calendar vintage dates rather than intraday publication timestamps. Each fact is
therefore usable only from 00:00 UTC two calendar days after its vintage date. The March 9 vintage
is conservatively knowable on March 11; the March 14 vintage is conservatively knowable on March
16, twelve hours before the decision boundary. This deterministic rule is intentionally later
than an unproven intraday assumption.

ShockCompiler uses only the two already-known derived spreads:

- latest-known persistence baseline: `-48` basis points;
- stress endpoints: `-107` and `-48` basis points;
- range width: `59` basis points;
- probability assigned: none.

The range is not a forecast, confidence interval, calibrated coverage statement, recession
probability, banking-stress model, or policy-effect estimate.

## Disjoint post-decision breach

`scenarios/treasury-curve-2023/event-lock.json` contains the March 15 observations from their March
16 vintages: DGS2 `393` and DGS10 `351` basis points. Under the same conservative rule they become
knowable at 2023-03-18 00:00 UTC, after the decision boundary. Their derived spread is `-42` basis
points.

`-42` is 6 basis points above the declared upper endpoint of `-48`. The verifier requires this
breach to remain visible. It does not widen the interval, relabel the outcome as a success, or leak
either event fact into the pack. A transparent miss is valid reproducibility evidence but is
negative evidence for coverage or forecast claims.

## Four relevant engines

TimeVault reconstructs the two-date decision set; ShockCompiler derives two spreads and compiles
the no-probability endpoints; TrialCourt retains and rejects a retrospective two-date attempt;
ReplayStudio exports a deterministic human- and machine-readable pack. MarketTwin, ExecutionLab,
and CapitalAllocator are absent because no exposure network, order, execution, portfolio,
allocation, or return is represented.

## Rebuild and counted proof

```bash
python scripts/build_treasury_curve_boundary_replaypack.py \
  --input-lock scenarios/treasury-curve-2023/input-lock.json \
  --output verification/replaypacks/treasury-curve-2023

python scripts/verify_treasury_curve_boundary_replaypack.py \
  --input-lock scenarios/treasury-curve-2023/input-lock.json \
  --event-lock scenarios/treasury-curve-2023/event-lock.json \
  --pack verification/replaypacks/treasury-curve-2023 \
  --receipt verification/evidence/treasury-curve-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 18 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/treasury-curve-2023-inversion-boundary-v1.json` binds the
supporting inventory, locks, scripts, pack, receipt, truth labels, persistence baseline,
no-probability marker, TrialCourt rejection, exact event-pair identity, isolation, and required
6-basis-point breach. This establishes internal reproducibility only—not forecast skill,
calibrated coverage, stationary behavior, causal banking, recession, or policy attribution,
external validation, deployment, investment performance, or user impact.
