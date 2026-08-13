# U.S. GDP 2022 Q4 revision-vintage boundary replay

This counted scenario sets a historical decision boundary at 2023-02-01 12:00 UTC. Its decision
inputs are four reported GDP estimates from three native ALFRED vintages. A fourth ALFRED vintage,
dated 2023-02-23, is locked separately as a post-decision event and cannot appear in any ReplayPack
source record.

The case is deliberately different from the first three regional-bank cases. It tests revision
history and future-vintage exclusion rather than a bank balance-sheet stress mechanism. FinReplay
does not claim that it existed at the historical decision time.

## Locked decision inputs

`scenarios/gdp-revision-2022q4/input-lock.json` contains exactly four facts, all in billions of
dollars at seasonally adjusted annual rates:

- 2022 Q3 at the 2022-10-27 advance vintage: `25663.289`;
- 2022 Q3 at the 2022-11-30 second-estimate vintage: `25698.960`;
- 2022 Q3 at the 2023-01-26 pre-decision vintage: `25723.941`; and
- 2022 Q4 at the 2023-01-26 advance vintage: `26132.458`.

ALFRED vintage dates are calendar dates, not exact intraday availability timestamps. The adapter
therefore permits use only at 00:00 UTC two calendar days after each selected vintage. This makes
all four inputs conservatively knowable before the decision boundary without overstating timing
precision. Each fact retains its record ID, requested vintage, observation date, source URL,
response hash, `vintage_native` coverage, and `reported` label.

The known Q3 path changed by `35.671` from advance to second estimate and by another `24.981` by
the pre-decision vintage, for a cumulative `60.652`. ShockCompiler uses the absolute cumulative
magnitude as a transparent symmetric Q4 revision boundary around a naive zero-revision baseline:

- revision endpoints: `-60.652` and `+60.652`;
- corresponding Q4 GDP endpoints: `26071.806` and `26193.110`;
- probability assigned: none.

This one-prior-quarter heuristic is not a forecast, confidence interval, calibrated coverage
statement, or expected revision.

## Disjoint post-decision event

`scenarios/gdp-revision-2022q4/event-lock.json` contains only the 2022 Q4 value at the 2023-02-23
ALFRED vintage: `26144.956`. Under the same conservative rule, it becomes eligible at 2023-02-25
00:00 UTC, strictly after the historical decision boundary. The reported change from the advance
estimate is `+12.498`, which falls inside the previously declared envelope.

That containment is labelled as post-event evaluation only. The event record is absent from the
input lock, all four engine artifacts, the compiled source manifest, and the boundary derivation.
One contained observation does not prove forecasting skill or calibrated coverage.

## Four relevant engines

The scenario runs only the engines its question requires:

1. TimeVault reconstructs the four-fact decision-time vintage set.
2. ShockCompiler produces the two no-probability revision endpoints.
3. TrialCourt retains and rejects a retrospective single-quarter attempt, including all six attack
   findings and visibly simulated schema sentinels.
4. ReplayStudio produces the deterministic human- and machine-readable pack.

MarketTwin, ExecutionLab, and CapitalAllocator are intentionally absent because this scenario has
no institution network, order, venue, portfolio, allocation, or investment claim. Engine count is
not padded with fabricated inputs.

## Rebuild and counted proof

```bash
python scripts/build_macro_revision_replaypack.py \
  --input-lock scenarios/gdp-revision-2022q4/input-lock.json \
  --output verification/replaypacks/gdp-revision-2022q4

python scripts/verify_macro_revision_replaypack.py \
  --input-lock scenarios/gdp-revision-2022q4/input-lock.json \
  --event-lock scenarios/gdp-revision-2022q4/event-lock.json \
  --pack verification/replaypacks/gdp-revision-2022q4 \
  --receipt verification/evidence/gdp-revision-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The committed clean-worktree receipt passes 16 assertions over two fresh directory and ZIP
rebuilds. The proof at
`verification/scenarios/proofs/gdp-2022q4-revision-boundary-v1.json` binds the source inventory,
locks, scripts, pack, receipt, truth labels, naive baseline, no-probability marker, TrialCourt
rejection, event isolation, and limitations. This establishes internal reproducibility only—not
domain-method correctness, calibrated coverage, external validation, deployment, policy impact,
investment performance, or user impact.
