# U.S. Treasury General Account cash-balance boundary replay

This counted scenario places a historical decision boundary at 2023-06-02 21:00 UTC. Its two
inputs are TGA closing balances from the May 31 and June 1, 2023 Daily Treasury Statement PDFs. The
June 2 report is locked separately as a post-decision event and is absent from every ReplayPack
source record.

The source is the U.S. Department of the Treasury, Bureau of the Fiscal Service. This case does not
claim that FinReplay existed at the historical decision time, and it does not attribute the balance
path to debt-limit negotiations or any other cause.

## Locked reports and publication boundary

`scenarios/treasury-tga-2023/input-lock.json` contains:

- May 31 TGA closing balance: `48,512` million dollars;
- June 1 TGA closing balance: `22,892` million dollars.

For each PDF, the supporting adapter also extracts Table I opening balance, deposits, and
withdrawals, and requires:

`opening balance + deposits - withdrawals = closing balance`.

Treasury states that the Daily Treasury Statement is available by 4:00 p.m. on the following
business day. FinReplay uses that deadline in `America/New_York` as a conservative knowledge time,
not the exact publication instant. The May 31 report is therefore eligible at 2023-06-01 20:00 UTC,
and the June 1 report at 2023-06-02 20:00 UTC, one hour before the decision boundary.

ShockCompiler uses only the two already-known reported balances:

- latest-known persistence baseline: `22,892` million dollars;
- stress endpoints: `22,892` and `48,512` million dollars;
- range width: `25,620` million dollars;
- probability assigned: none.

The range is not a forecast, confidence interval, calibrated coverage statement, debt-default
probability, fiscal-solvency measure, debt-limit effect estimate, or policy recommendation.

## Disjoint post-decision event

`scenarios/treasury-tga-2023/event-lock.json` contains the June 2 closing balance of `23,368`
million dollars. Because Friday's statement is available by 4:00 p.m. on the following business
day, the verified publication-calendar bound makes it knowable at 2023-06-05 20:00 UTC, after the
decision boundary.

The reported event lies inside the declared `22,892`–`48,512` range and is `476` million dollars
above the persistence baseline. The verifier checks both statements separately. An inside-range
result does not establish forecast skill, and the difference from persistence remains visible.

## Four relevant engines

TimeVault reconstructs the two-report decision set; ShockCompiler compiles the no-probability
endpoints; TrialCourt retains and rejects a retrospective two-report attempt; ReplayStudio exports
a deterministic human- and machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator
are absent because no exposure network, order, execution, portfolio, allocation, or return is
represented.

## Rebuild and counted proof

```bash
python scripts/build_tga_cash_boundary_replaypack.py \
  --input-lock scenarios/treasury-tga-2023/input-lock.json \
  --output verification/replaypacks/treasury-tga-2023

python scripts/verify_tga_cash_boundary_replaypack.py \
  --input-lock scenarios/treasury-tga-2023/input-lock.json \
  --event-lock scenarios/treasury-tga-2023/event-lock.json \
  --pack verification/replaypacks/treasury-tga-2023 \
  --receipt verification/evidence/treasury-tga-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 18 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/treasury-tga-2023-cash-boundary-v1.json` binds the supporting
inventory, locks, scripts, pack, receipt, truth labels, persistence baseline, no-probability marker,
TrialCourt rejection, exact event identity, isolation, inside-range check, and required 476-million
dollar persistence difference. This establishes internal reproducibility only—not forecast skill,
calibrated coverage, debt-limit causality, fiscal solvency, policy effectiveness, external
validation, deployment, investment performance, or user impact.
