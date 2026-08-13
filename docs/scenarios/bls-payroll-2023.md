# BLS Employment Situation payroll-release boundary replay

This counted scenario places a historical decision boundary at 2023-02-04 12:00 UTC. Its four
inputs come from the January 6 and February 3, 2023 archived BLS Employment Situation releases. A
March 10 headline fact is locked separately as the post-decision event and is absent from every
ReplayPack source record.

The case concerns aggregate release-snapshot payroll changes and unemployment rates. It does not
claim that FinReplay existed at the historical decision time.

## Locked decision inputs

`scenarios/bls-payroll-2023/input-lock.json` contains two headline measures from each release:

- January 6 release for December 2022: nonfarm payroll change `223` thousand and unemployment rate
  `3.5` percent;
- February 3 release for January 2023: nonfarm payroll change `517` thousand and unemployment rate
  `3.4` percent.

Both archived pages state that transmission was embargoed until 8:30 a.m. Eastern Time. The
adapter parses those page statements with `America/New_York`, producing an exact availability time
of 13:30 UTC for both releases. Records preserve the report period, metric, source URL, complete page
hash, `versioned_snapshot` coverage, and `reported` label.

The February 3 release documents annual establishment-survey benchmarking and updated seasonal
adjustment factors. The two payroll headlines therefore do not form a calibrated stationary sample.
ShockCompiler uses only their minimum and maximum as transparent next-release stress endpoints:

- latest-known persistence baseline: `517` thousand;
- payroll-change endpoints: `223` and `517` thousand;
- range width: `294` thousand;
- probability assigned: none.

This two-point range is not a forecast, confidence interval, calibrated coverage statement, or
expected payroll change.

## Disjoint post-decision event

`scenarios/bls-payroll-2023/event-lock.json` preserves only the March 10 release's February 2023
headline payroll change: `311` thousand. It became available at 2023-03-10 13:30 UTC, after the
decision boundary, and lies inside the previously declared `[223, 517]` thousand range.

This is a labelled post-event evaluation only. The event fact is excluded from the input lock,
engine artifacts, compiled source manifest, and bound construction. One contained headline value
does not prove forecast skill or coverage calibration. Aggregate headlines do not identify workers,
employers, or causal labor-market mechanisms.

## Four relevant engines

TimeVault reconstructs the two-release decision set; ShockCompiler builds two no-probability
endpoints; TrialCourt retains and rejects a retrospective two-release attempt; ReplayStudio exports
a deterministic human- and machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator
are absent because no network, security, order, venue, portfolio, allocation, or return is
represented.

## Rebuild and counted proof

```bash
python scripts/build_employment_boundary_replaypack.py \
  --input-lock scenarios/bls-payroll-2023/input-lock.json \
  --output verification/replaypacks/bls-payroll-2023

python scripts/verify_employment_boundary_replaypack.py \
  --input-lock scenarios/bls-payroll-2023/input-lock.json \
  --event-lock scenarios/bls-payroll-2023/event-lock.json \
  --pack verification/replaypacks/bls-payroll-2023 \
  --receipt verification/evidence/bls-payroll-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 16 assertions over two fresh directory and ZIP rebuilds. The proof
at `verification/scenarios/proofs/bls-2023-payroll-release-boundary-v1.json` binds the supporting
source inventory, locks, scripts, pack, receipt, truth labels, persistence baseline, no-probability
marker, TrialCourt rejection, event isolation, and limitations. This establishes internal
reproducibility only—not forecast skill, calibrated coverage, stationarity across annual
benchmarking, causal labor-market attribution, policy effectiveness, external validation,
deployment, investment performance, or user impact.
