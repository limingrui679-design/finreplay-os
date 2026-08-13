# Federal Reserve BTFP early-growth boundary replay

This counted scenario places a historical decision boundary at 2023-03-25 12:00 UTC. Its four
inputs come from the Federal Reserve's archived March 16 and March 23, 2023 H.4.1 releases. A March
30 H.4.1 fact is locked separately as the post-decision event and is absent from every ReplayPack
source record.

The case concerns aggregate Bank Term Funding Program balances, not an institution filing or a GDP
revision. It does not claim that FinReplay existed at the historical decision time.

## Locked decision inputs

`scenarios/btfp-growth-2023/input-lock.json` contains two Table 1 measures from each release, in
millions of dollars:

- March 16 release, week ended March 15: weekly average `2,443` and Wednesday balance `11,943`;
- March 23 release, week ended March 22: weekly average `34,609` and Wednesday balance `53,669`.

The archived pages identify release dates, but the adapter does not depend on an intraday
publication timestamp. Each release becomes usable at 00:00 UTC two calendar days later. The March
23 facts therefore become conservatively knowable at 2023-03-25 00:00 UTC, twelve hours before the
decision boundary. Records preserve the release and week-ending dates, metric, source URL, complete
page hash, `versioned_snapshot` coverage, and `reported` label.

The weekly-average change is `32,166`; the Wednesday balance change is `41,726`. ShockCompiler uses
the latter as a transparent next-week growth envelope:

- naive growth baseline: `0`;
- growth endpoints: `0` and `41,726`;
- corresponding Wednesday balance endpoints: `53,669` and `95,395`;
- probability assigned: none.

Repeating one early weekly change is not a forecast, confidence interval, calibrated coverage
statement, or expected facility use.

## Disjoint post-decision event

`scenarios/btfp-growth-2023/event-lock.json` preserves only the March 30 release's March 29
Wednesday BTFP balance: `64,403` million dollars. It becomes conservatively knowable at 2023-04-01
00:00 UTC, after the decision boundary. Its increase from the prior Wednesday is `10,734`, which
falls inside the previously declared `[0, 41,726]` growth envelope.

This is a labelled post-event evaluation only. The event fact is excluded from the input lock,
engine artifacts, compiled source manifest, and bound construction. One contained balance does not
prove forecast skill or coverage calibration. Aggregate balances do not identify borrowers,
collateral, motives, stress causes, or program effectiveness.

## Four relevant engines

TimeVault reconstructs the two-release decision set; ShockCompiler builds two no-probability
endpoints; TrialCourt retains and rejects a retrospective one-week attempt; ReplayStudio exports a
deterministic human- and machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator are
absent because no network, security, order, venue, portfolio, allocation, or return is represented.

## Rebuild and counted proof

```bash
python scripts/build_facility_growth_replaypack.py \
  --input-lock scenarios/btfp-growth-2023/input-lock.json \
  --output verification/replaypacks/btfp-growth-2023

python scripts/verify_facility_growth_replaypack.py \
  --input-lock scenarios/btfp-growth-2023/input-lock.json \
  --event-lock scenarios/btfp-growth-2023/event-lock.json \
  --pack verification/replaypacks/btfp-growth-2023 \
  --receipt verification/evidence/btfp-growth-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 16 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/btfp-2023-early-growth-boundary-v1.json` binds the source
inventory, locks, scripts, pack, receipt, truth labels, naive baseline, no-probability marker,
TrialCourt rejection, event isolation, and limitations. This establishes internal reproducibility
only—not forecasting skill, calibrated coverage, causal systemic stress, policy effectiveness,
external validation, deployment, investment performance, or user impact.
