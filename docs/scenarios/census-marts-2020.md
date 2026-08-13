# March 2020 U.S. Census MARTS retail-sales boundary replay

This counted scenario places a historical decision boundary at 2020-03-18 12:00 UTC. Its two
inputs are the January and February total retail-and-food-services monthly changes reported in
archived U.S. Census Bureau Advance Monthly Retail Trade Survey releases. The March change is
locked separately as a post-decision event and is absent from every ReplayPack source record.

The source reports aggregate estimates. This case does not claim that FinReplay existed at the
historical decision time, observe retailers, households, categories, transactions, positions, or
orders, identify a causal pandemic, policy, or retail mechanism, or predict a future monthly
change.

## Paired official releases and knowledge boundary

`scenarios/census-marts-2020/input-lock.json` contains:

- February 14 release for January: monthly change `0.3%`, or `30` basis points; adjusted sales
  `$529.8 billion`; year-over-year change `4.4%`;
- March 17 release for February: monthly change `-0.5%`, or `-50` basis points; adjusted sales
  `$528.1 billion`; year-over-year change `4.3%`.

For each release, the supporting adapter retrieves the official archived PDF and legacy XLS
workbook. It requires the PDF release number, reference month, date, exact release time, expected
nonblank page count and page-dimension sequence. The workbook must be an OLE file containing
exactly `Table 1.`, `Table 2.`, and `Table 3.` with the locked dimensions. Fixed total-series cells
must match the PDF on the headline change, adjusted sales, year-over-year change, prior-month
revision bridge, sampling margins, and revision statistics.

The January release states 8:30 a.m. EST, making its exact availability time 2020-02-14 13:30 UTC.
The February release states 8:30 a.m. EDT, making its exact availability time
2020-03-17 12:30 UTC. Both precede the decision boundary. The timezone abbreviation is validated
under `America/New_York`; current retrieval headers are not backdated.

ShockCompiler uses only the two decision-time release snapshots:

- latest-known persistence baseline: `-50` basis points;
- one known January-to-February decrease: `80` basis points;
- stress endpoints: one repeat of the decrease at `-130`, or persistence at `-50`;
- range width: `80` basis points;
- probability assigned: none.

Census's published 90-percent sampling-error margins are retained as reported release metadata.
They are not used as these endpoints and are not relabeled as a FinReplay confidence interval.
The stress range is not a forecast, calibrated coverage statement, retailer or household model,
macroeconomic causal model, trading signal, or policy recommendation.

## Disjoint post-decision breach and revision

`scenarios/census-marts-2020/event-lock.json` contains the April 15 release for March: monthly
change `-8.7%`, or `-870` basis points; adjusted sales `$483.1 billion`; year-over-year change
`-6.2%`. Its archived PDF states an 8:30 a.m. EDT release time, making the fact available at
2020-04-15 12:30 UTC, after the decision boundary. The paired workbook independently reports
`483,066` million dollars of adjusted sales.

The event release also revises February from the earlier `-0.5%` snapshot to `-0.4%`, a change of
`10` basis points. The event record retains both values; the input lock remains `-50` basis points
and is never overwritten. The reported March change is `740` basis points below the previously
declared lower endpoint of `-130`. The verifier requires that miss to remain visible. It does not
widen the range, relabel the outcome as a success, or leak the outcome or revision into the pack.
A transparent miss is valid reproducibility evidence but strongly negative evidence for coverage
or forecast claims.

The April release says Census monitored response and data quality during COVID-19 and that the
estimates met publication standards. FinReplay preserves only the presence of that reported
statement; it does not infer pandemic causality, unaffected measurement, or source correctness.

## Four relevant engines

TimeVault reconstructs the two-release decision set; ShockCompiler compiles the no-probability
persistence-or-one-known-decrease endpoints; TrialCourt retains and rejects a retrospective
one-decrease attempt; ReplayStudio exports a deterministic human- and machine-readable pack.
MarketTwin, ExecutionLab, and CapitalAllocator are absent because no retailer network, position,
order, execution, portfolio, allocation, or return is represented.

## Rebuild and counted proof

```bash
python scripts/build_retail_sales_boundary_replaypack.py \
  --input-lock scenarios/census-marts-2020/input-lock.json \
  --output verification/replaypacks/census-marts-2020

python scripts/verify_retail_sales_boundary_replaypack.py \
  --input-lock scenarios/census-marts-2020/input-lock.json \
  --event-lock scenarios/census-marts-2020/event-lock.json \
  --pack verification/replaypacks/census-marts-2020 \
  --receipt verification/evidence/census-marts-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 21 assertions over two fresh directory and ZIP rebuilds. The
proof at
`verification/scenarios/proofs/census-marts-2020-retail-sales-boundary-v1.json` binds the
supporting inventory, PDF/XLS hashes, locks, scripts, pack, receipt, truth labels, persistence
baseline, no-probability marker, TrialCourt rejection, exact event identity, later revision
isolation, and required `740`-basis-point breach. This establishes internal reproducibility
only—not forecast skill, calibrated coverage, retailer, household, category, pandemic, policy, or
retail causality, external validation, deployment, investment performance, or user impact.
