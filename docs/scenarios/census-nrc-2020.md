# March 2020 Census/HUD housing-starts boundary replay

This counted scenario places a historical decision boundary at 2020-03-19 12:00 UTC. Its two
inputs are preliminary total privately owned housing-starts seasonally adjusted annual-rate
headlines from archived U.S. Census Bureau/U.S. Department of Housing and Urban Development New
Residential Construction releases. The March headline is locked separately as a post-decision
event and is absent from every ReplayPack source record.

The source reports aggregate survey estimates. This case does not claim that FinReplay existed at
the historical decision time, observe projects, builders, properties, local markets, transactions,
positions, or orders, identify a causal pandemic, policy, housing, or regional mechanism, or
predict a future headline level.

## Official releases and knowledge boundary

`scenarios/census-nrc-2020/input-lock.json` contains:

- February 19 release for January: preliminary total starts `1,567,000` SAAR units; official
  monthly change `-3.6%` with a `±13.3%` 90-percent sampling margin; revised December level
  `1,626,000`;
- March 18 release for February: preliminary total starts `1,599,000` SAAR units; official
  monthly change `-1.5%` with a `±12.4%` 90-percent sampling margin; revised January level
  `1,624,000`, versus the earlier January headline of `1,567,000`.

The adapter requires each response to be an official PDF with exactly seven nonblank `612 x 792`
pages, the verified page-title sequence, release identity, page-stated time, headline values,
explanatory notes, and a matching Table 3a total-series row, revised prior, monthly change,
sampling margin, and average RSE. It also validates the single-family and five-or-more-unit
headline facts retained in each normalized record.

The January release states 8:30 a.m. EST, making its exact availability time
2020-02-19 13:30 UTC. The February release states 8:30 a.m. EDT, making its exact availability
time 2020-03-18 12:30 UTC. Both precede the decision boundary. The timezone abbreviation is
validated under `America/New_York`; current HTTP headers are never used as historical release-time
evidence.

## Headline-level range and statistical separation

ShockCompiler uses only the two release-time preliminary headline levels:

- latest-known headline persistence baseline: `1,599,000` SAAR units;
- one known release-headline increase: `32,000` units;
- stress endpoints: persistence at `1,599,000`, or one repeat of that increase at `1,631,000`;
- range width: `32,000` units;
- probability assigned: none.

The `32,000`-unit step is the arithmetic difference between two different releases' preliminary
headline levels, `1,599,000 - 1,567,000`. It is not Census/HUD's official February monthly change.
The official `-1.5%` change uses the revised January estimate of `1,624,000`. The pack records this
distinction explicitly and never presents the two-headline difference as an official growth rate.

Census/HUD's published 90-percent confidence intervals account for sampling variability. They are
retained as reported source metadata but are not used to set the FinReplay endpoints, assigned a
probability, or relabeled as forecast coverage. The source also warns that total-start trends may
take six months to establish and that sampling and nonsampling errors both apply.

## Disjoint post-decision breach and revision

`scenarios/census-nrc-2020/event-lock.json` contains the April 16 release for March: preliminary
total starts `1,216,000` SAAR units, an official `-22.3%` monthly change with a `±12.2%`
90-percent sampling margin. Its 8:30 a.m. EDT release time is 2020-04-16 12:30 UTC, after the
decision boundary.

The event release revises February from its earlier `1,599,000` headline to `1,564,000`, a
`-35,000`-unit revision. The event record retains both values; the input lock remains `1,599,000`
and is never overwritten. The March headline is `383,000` units below the previously declared
lower endpoint. The verifier requires that miss to remain visible; it does not widen the range,
relabel the result as success, or leak the outcome or revision into the pack.

The April release states that Census determined the estimates met publication standards during
COVID-19. FinReplay preserves only the presence of that reported statement; it does not infer
pandemic causality, unaffected measurement, or independent source correctness.

## Four relevant engines

TimeVault reconstructs the two-release decision set; ShockCompiler compiles the no-probability
persistence-or-one-known-headline-increase endpoints; TrialCourt retains and rejects a
retrospective one-increase attempt; ReplayStudio exports a deterministic human- and
machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator are absent because no
project network, security, order, execution, portfolio, allocation, or return is represented.

## Rebuild and counted proof

```bash
python scripts/build_housing_starts_boundary_replaypack.py \
  --input-lock scenarios/census-nrc-2020/input-lock.json \
  --output verification/replaypacks/census-nrc-2020 \
  --code-commit a98465eb4b74177c0b3c3658bf0599554c9180fe

python scripts/verify_housing_starts_boundary_replaypack.py \
  --input-lock scenarios/census-nrc-2020/input-lock.json \
  --event-lock scenarios/census-nrc-2020/event-lock.json \
  --pack verification/replaypacks/census-nrc-2020 \
  --receipt verification/scenarios/rebuilds/census-nrc-2020.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 23 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/census-nrc-2020-housing-starts-boundary-v1.json` binds the
supporting inventory, PDF hashes, locks, scripts, pack, receipt, truth labels, persistence
baseline, no-probability and no-official-confidence-range markers, TrialCourt rejection, exact
event identity, later revision isolation, and required `383,000`-unit breach. This establishes
internal reproducibility only—not forecast skill, calibrated coverage, project, builder, regional,
pandemic, policy, or housing causality, external validation, deployment, investment performance,
or user impact.
