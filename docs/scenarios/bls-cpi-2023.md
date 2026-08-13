# BLS CPI-U release boundary replay

This counted scenario places a historical decision boundary at 2023-02-15 12:00 UTC. Its four
inputs come from the January 12 and February 14, 2023 archived BLS Consumer Price Index releases.
A March 14 headline fact is locked separately as the post-decision event and is absent from every
ReplayPack source record.

The case concerns aggregate release-snapshot CPI-U changes. It does not claim that FinReplay
existed at the historical decision time.

## Locked decision inputs

`scenarios/bls-cpi-2023/input-lock.json` contains two CPI-U all-items headline measures from each
release:

- January 12 release for December 2022: seasonally adjusted monthly change `-0.1` percent and
  not-seasonally-adjusted 12-month change `6.5` percent;
- February 14 release for January 2023: seasonally adjusted monthly change `+0.5` percent and
  not-seasonally-adjusted 12-month change `6.4` percent.

Both pages state that transmission was embargoed until 8:30 a.m. Eastern Time. The adapter parses
those statements with `America/New_York`, producing an exact availability time of 13:30 UTC for
both winter releases. Records preserve the report period, metric, source URL, full page hash,
`versioned_snapshot` coverage, and `reported` label.

The February release documents annual weight updates and recalculation of the previous five years
of seasonally adjusted indexes. It describes the preceding December change as `+0.1` percent,
whereas the January release snapshot reported `-0.1` percent. FinReplay preserves both pages and
does not silently replace the earlier vintage. The two input headlines therefore do not form a
calibrated stationary sample. ShockCompiler uses only their minimum and maximum as transparent
next-release stress endpoints:

- latest-known persistence baseline: `+0.5` percent;
- monthly-change endpoints: `-0.1` and `+0.5` percent;
- range width: `0.6` percentage point;
- probability assigned: none.

This two-point release-snapshot range is not a forecast, confidence interval, calibrated coverage
statement, or expected CPI change.

## Disjoint post-decision event

`scenarios/bls-cpi-2023/event-lock.json` preserves only the March 14 release's February 2023
seasonally adjusted monthly change: `+0.4` percent. It became available at 2023-03-14 12:30 UTC
after the daylight-saving transition and after the decision boundary. It lies inside the
previously declared `[-0.1, +0.5]` percent range.

This is a labelled post-event evaluation only. The event fact is excluded from the input lock,
engine artifacts, compiled source manifest, and bound construction. One contained headline value
does not prove forecast skill or coverage calibration. Aggregate CPI headlines do not identify
individual price quotes, item-level mechanisms, or causal inflation drivers.

## Four relevant engines

TimeVault reconstructs the two-release decision set; ShockCompiler builds two no-probability
endpoints; TrialCourt retains and rejects a retrospective two-release attempt; ReplayStudio
exports a deterministic human- and machine-readable pack. MarketTwin, ExecutionLab, and
CapitalAllocator are absent because no network, security, order, venue, portfolio, allocation, or
return is represented.

## Rebuild and counted proof

```bash
python scripts/build_cpi_boundary_replaypack.py \
  --input-lock scenarios/bls-cpi-2023/input-lock.json \
  --output verification/replaypacks/bls-cpi-2023

python scripts/verify_cpi_boundary_replaypack.py \
  --input-lock scenarios/bls-cpi-2023/input-lock.json \
  --event-lock scenarios/bls-cpi-2023/event-lock.json \
  --pack verification/replaypacks/bls-cpi-2023 \
  --receipt verification/evidence/bls-cpi-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 17 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/bls-2023-cpi-release-boundary-v1.json` binds the supporting
source inventory, locks, scripts, pack, receipt, truth labels, persistence baseline,
no-probability marker, TrialCourt rejection, exact event identity and isolation, and limitations.
This establishes internal reproducibility only—not forecast skill, calibrated coverage,
stationarity across annual weight updates and seasonal recalculation, causal inflation
attribution, policy effectiveness, external validation, deployment, investment performance, or
user impact.
