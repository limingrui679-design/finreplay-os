# FOMC federal-funds target-range boundary replay

This counted scenario places a historical decision boundary at 2023-03-23 12:00 UTC. Its four
inputs come from the February 1 and March 22, 2023 archived FOMC statements. A May 3 upper target
endpoint is locked separately as the post-decision event and is absent from every ReplayPack source
record.

The case concerns official policy-release target ranges. It does not claim that FinReplay existed
at the historical decision time.

## Locked decision inputs

`scenarios/fomc-target-2023/input-lock.json` contains both endpoints from each statement:

- February 1: `450` to `475` basis points;
- March 22: `475` to `500` basis points.

The archived pages say “For release at 2:00 p.m.” and identify EST or EDT. The adapter validates
each abbreviation against `America/New_York`, producing 2023-02-01 19:00 UTC and 2023-03-22 18:00
UTC. Records preserve the policy name, endpoint, source display value, archive URL, complete page
hash, `versioned_snapshot` coverage, and `reported` label.

Both known target ranges are 25 basis points wide, and both endpoints rose by 25 basis points from
February to March. ShockCompiler uses that one known step only as a transparent next-upper-target
stress envelope:

- latest-upper persistence baseline: `500` basis points;
- next-upper endpoints: `500` and `525` basis points;
- probability assigned: none.

Repeating zero or one known step is not a forecast, confidence interval, calibrated coverage
statement, policy recommendation, or claim about policy correctness.

## Disjoint post-decision event

`scenarios/fomc-target-2023/event-lock.json` preserves only the May 3 statement's upper endpoint:
`525` basis points. It became available at 2023-05-03 18:00 UTC, after the decision boundary, and
lies at the upper endpoint of the previously declared `[500, 525]` range.

This is a labelled post-event evaluation only. The event fact is excluded from the input lock,
engine artifacts, compiled source manifest, and bound construction. One contained policy endpoint
does not prove forecast skill or coverage calibration. Target ranges do not establish market
expectations, policy effectiveness, or causal macroeconomic effects.

## Four relevant engines

TimeVault reconstructs the two-statement decision set; ShockCompiler builds two no-probability
endpoints; TrialCourt retains and rejects a retrospective policy-step attempt; ReplayStudio exports
a deterministic human- and machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator
are absent because no network, security, order, venue, portfolio, allocation, or return is
represented.

## Rebuild and counted proof

```bash
python scripts/build_fomc_target_replaypack.py \
  --input-lock scenarios/fomc-target-2023/input-lock.json \
  --output verification/replaypacks/fomc-target-2023

python scripts/verify_fomc_target_replaypack.py \
  --input-lock scenarios/fomc-target-2023/input-lock.json \
  --event-lock scenarios/fomc-target-2023/event-lock.json \
  --pack verification/replaypacks/fomc-target-2023 \
  --receipt verification/evidence/fomc-target-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 16 assertions over two fresh directory and ZIP rebuilds. The proof
at `verification/scenarios/proofs/fomc-2023-target-range-boundary-v1.json` binds the supporting
source inventory, locks, scripts, pack, receipt, truth labels, persistence baseline, no-probability
marker, TrialCourt rejection, event isolation, and limitations. This establishes internal
reproducibility only—not forecast skill, calibrated coverage, monetary-policy correctness or causal
effects, external validation, deployment, investment performance, or user impact.
