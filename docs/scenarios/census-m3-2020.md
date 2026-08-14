# March 2020 Census M3 durable-goods change boundary replay

This counted scenario places a historical decision boundary at 2020-03-25 12:30 UTC, the exact
8:30 a.m. EDT time stated in the February 2020 Census M3 Advance Durable Goods report. Its two
inputs are total durable-goods new-orders monthly changes for January and February 2020 from their
first verified advance reports. The April 24 March change and that report's revision snapshot are
locked separately as post-decision evidence and are absent from every ReplayPack source record.

M3 is an aggregate nonprobability-sample survey. This case does not claim that FinReplay existed at
the historical boundary, observe every manufacturer, product, order, shipment, contract, firm, or
transaction, measure price-adjusted output, isolate a COVID-19 effect, or predict a future release.

## Official releases and knowledge boundary

`scenarios/census-m3-2020/input-lock.json` contains exactly two first-report changes:

- January 2020: `-20` basis points (`-0.2%`) and `$246,199 million` from the February 27 report;
- February 2020: `120` basis points (`1.2%`) and `$249,409 million` from the March 25 report.

Each report states an 8:30 a.m. EST/EDT release time. The adapter validates the abbreviation with
`America/New_York`, producing 13:30 UTC on February 27 and 12:30 UTC on March 25. It requires the
exact release number and code, seven-page structure, page dimensions, rotations, nonblank text
layers, identifying metadata, headline, future-release schedule, methodology, benchmark notice,
and exact Table 1 and Table 2 values before emitting one record.

The two decision-input PDF SHA-256 digests are:

- January report: `b58f95a053d07c367f550e4acb0a941cb338869b12ba01d2d9cbd032c4ad38b4`;
- February report: `84be58245193913f73c80400b6209328a5d0e3be6daac3c064b47500ac1fbf00`.

Both PDFs have metadata showing modification after their stated release. Their current hashes are
therefore present official archived evidence, not proof that the same bytes were served at the
historical release instant. Current HTTP metadata and retrieval timestamps are never backdated.
Full PDFs remain in ignored content-addressed storage; the repository retains minimal facts, URLs,
hashes, and release-snapshot provenance.

## Transparent range with no probability

ShockCompiler uses only the two first-report total new-orders changes:

- latest-known persistence baseline: `120` basis points;
- one known initial-release increase: `120 - (-20) = 140` basis points;
- stress endpoints: persistence at `120`, or one repeat of the increase at `260` basis points;
- range width: `140` basis points;
- probability assigned: none.

This is a transparent stress construction from two values and one difference. M3 is not a
probability sample, so report-level sampling error, confidence intervals, and headline statistical
significance cannot be computed. The range is not a Census forecast, confidence interval,
calibrated coverage band, stationary-regime estimate, inflation model, pandemic-effect estimate,
or causal model.

## Disjoint post-decision event and revisions

`scenarios/census-m3-2020/event-lock.json` contains the April 24 report's March 2020 change of
`-1,440` basis points (`-14.4%`) and level of `$213,184 million`. Its 8:30 a.m. EDT release time is
2020-04-24 12:30 UTC, strictly after the decision boundary. The event record ID is disjoint from
both input IDs.

The same report snapshot retains January at `10` basis points and revises February from its
first-report `120` basis points to `110` basis points. It reports exact levels of `$246,558 million`
for January and `$249,167 million` for February. Those later-snapshot values and revision deltas
remain in the event lock and never overwrite the first-report inputs. On the range fixed at the
March 25 boundary, the reported March change is `1,560` basis points below the `120`-basis-point
lower endpoint. The verifier requires the miss to remain visible; it neither widens the range nor
relabels the outcome as success.

The event PDF has an April 23 creation timestamp and a May 27 modification timestamp. Its exact
current hash, `ffafe420861628e384cbd49e0558157cf7e4b03a608cdcf599d4912e55a816a2`, is verified as
official archived evidence, but the scenario does not claim byte identity at release. The report's
COVID-19 language says the estimates met publication standards. It is not treated as proof of
causality, complete response, or unaffected measurement.

## Four relevant engines

TimeVault reconstructs the two-release, two-record decision set; ShockCompiler compiles the
no-probability persistence-or-one-known-increase endpoints; TrialCourt retains and rejects a
retrospective one-increase attempt; ReplayStudio exports a deterministic human- and machine-readable
pack. MarketTwin, ExecutionLab, and CapitalAllocator are absent because no firm network, security,
order, execution, portfolio, allocation, capital, return, or real-user evidence exists for this
question.

## Rebuild and counted proof

```bash
python scripts/build_durable_goods_change_boundary_replaypack.py \
  --input-lock scenarios/census-m3-2020/input-lock.json \
  --output verification/replaypacks/census-m3-2020 \
  --code-commit d2d89ebef0b1ae7033bda41f1bcd67453693e5e1

python scripts/verify_durable_goods_change_boundary_replaypack.py \
  --input-lock scenarios/census-m3-2020/input-lock.json \
  --event-lock scenarios/census-m3-2020/event-lock.json \
  --pack verification/replaypacks/census-m3-2020 \
  --receipt verification/scenarios/rebuilds/census-m3-2020.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 24 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/census-m3-2020-durable-goods-change-boundary-v1.json`
binds the supporting inventory, two PDF hashes, locks, scripts, pack, receipt, truth labels,
persistence baseline, current-byte boundary, no-probability range, TrialCourt rejection, exact
event identity, revision isolation, and required `1,560`-basis-point breach. This establishes
internal reproducibility only—not forecast skill, calibrated coverage, source or economic-method
correctness, statistical significance, price-adjusted output, a contemporaneous COVID effect,
manufacturing, inflation, pandemic, or policy causality, external validation, deployment,
investment performance, or user impact.
