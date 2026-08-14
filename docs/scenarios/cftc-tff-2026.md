# July 2026 CFTC TFF UST 2-year open-interest boundary replay

This counted scenario places a decision boundary at the official scheduled July 24, 2026,
3:30 p.m. EDT publication time for the July 21 Traders in Financial Futures report. Its two
inputs are aggregate Futures Only open-interest levels for CFTC contract code `042601`, UST 2Y
NOTE. The July 28 row, scheduled for July 31, is locked separately as a post-decision event and is
absent from every ReplayPack source record.

CFTC calls the annual schedule tentative and does not provide a row-level actual-publication log.
The records therefore carry `0.98` availability confidence and describe official **scheduled**
availability, not independently confirmed publication to the second. Open interest is an
aggregate contract count. It is not volume, orders, executions, accounts, trader direction or
intent, notional exposure, P&L, market impact, investment performance, or user activity.

## Five official sources and a composite snapshot

`scenarios/cftc-tff-2026/input-lock.json` contains exactly two reported rows:

- July 14 report: `4,465,199` contracts, scheduled for July 17 at 3:30 p.m. EDT;
- July 21 report: `4,335,075` contracts and a reported weekly change of `-130,124`, scheduled for
  July 24 at 3:30 p.m. EDT.

The supporting adapter retrieves exactly the July 14, 21, and 28 rows from the CFTC TFF Socrata
API and cross-checks all selected positions, weekly changes, trader counts, units, report mode,
contract code, and dates against the 2026 annual Futures Only ZIP. It also validates the current
2026 release schedule, COT policy page, and complete four-page TFF explanatory PDF. The five exact
response hashes are:

- Socrata API: `6d9be78582d398274618bd19114b08479c4fb4c35367b05193818003460bf5da`;
- annual ZIP: `a4ffcf3bb82606d167b3492c826f2b03ced9df2a88e292bb9213fa78c464ecea`;
- release schedule: `3488b3fb375fcee6b53d8e3dffc4f5c0b1f5e35e83e9cb4d881475a5c88bcc3b`;
- COT policy page: `9e795b8609b595b004211c1df8af3a06936d002582f2a1274812e148d368335a`;
- TFF notes PDF: `a9695fe93031cc81f7ff22a6b5c12b1f6d9599b972248e9a65ce8634eaab34fa`.

All three normalized records bind one composite source snapshot through the July 31 scheduled
release. Their individual `available_at` fields preserve the July 17, 24, and 31 knowledge
boundaries. This avoids falsely describing the later-retrieved five-file bundle as three different
source vintages. Current retrieval and the API/annual agreement do not prove that FinReplay
retrieved the data on those release dates or that the schedule occurred at the stated second.

The policy and TFF notes checks retain classification, reclassification, entry/exit, spreading,
trader-count overlap, and intent limitations. The `$200,000 FACE VALUE` label remains source text;
FinReplay performs no notional conversion. Category positions and trader counts are validated but
set neither range endpoint.

## Transparent range with no probability

ShockCompiler uses only the two total open-interest levels scheduled to be available by the
decision boundary:

- latest-known persistence baseline: `4,335,075` contracts;
- one known July 14-to-July 21 decline: `130,124` contracts;
- stress endpoints: persistence at `4,335,075`, or one repeat of the decline at `4,204,951`;
- range width: `130,124` contracts;
- probability assigned: none.

This is a transparent two-point stress construction, not a CFTC forecast, confidence interval,
calibrated coverage band, market model, or causal model. It does not infer direction, intent, or
trader behavior from open interest or from CFTC's category fields.

## Disjoint post-decision breach

`scenarios/cftc-tff-2026/event-lock.json` contains the July 28 report's total open interest of
`4,406,588` contracts and reported weekly increase of `71,513`. Its official scheduled July 31,
3:30 p.m. EDT availability is strictly after the decision boundary, and its record ID is disjoint
from both input IDs.

The event is `71,513` contracts above the fixed `4,335,075` upper endpoint. The verifier requires
that miss to remain visible; it neither widens the range nor relabels the outcome as success. The
event is an evaluation fact only and does not establish forecast skill from one later row.

## Four relevant engines

TimeVault reconstructs the scheduled-available decision set; ShockCompiler compiles the
no-probability persistence-or-one-known-decline endpoints; TrialCourt retains and rejects a
retrospective one-decline attempt; ReplayStudio exports a deterministic human- and
machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator are absent because no
trader network, order, execution, portfolio, allocation, capital, return, or user evidence exists.

## Rebuild and counted proof

```bash
.venv/bin/python scripts/build_cftc_open_interest_boundary_replaypack.py \
  --input-lock scenarios/cftc-tff-2026/input-lock.json \
  --output verification/replaypacks/cftc-tff-2026 \
  --code-commit 821b753e256a2b749ddc942ac3e043b8c1d49cb7

.venv/bin/python scripts/verify_cftc_open_interest_boundary_replaypack.py \
  --input-lock scenarios/cftc-tff-2026/input-lock.json \
  --event-lock scenarios/cftc-tff-2026/event-lock.json \
  --pack verification/replaypacks/cftc-tff-2026 \
  --receipt verification/scenarios/rebuilds/cftc-tff-2026.json

.venv/bin/python scripts/verify_scenario_catalog.py
```

The clean-checkout receipt passes 28 assertions over two fresh directory and ZIP rebuilds. The
deterministic pack SHA-256 is
`2b125673f41771947662dfd0a0b5e1b5105b4f334d413f4404753abcfbb69412`, with stable trace
`trace:35be3384f422dbc64c3546ae80f18efcc6f3004765241b4d98bff2dc9b1a0f2a`.
The sealed eight-gate proof SHA-256 is
`5056597ad4c64d5973169c1c7a795cec9a057785066f2758cb46e27e21426eba`.

These artifacts establish internal reproducibility, five-source binding, API/annual agreement,
honest scheduled-time uncertainty, truth-label separation, a visible out-of-range event, and
deliberate fail-closed adjudication. They do not establish actual publication to the second,
method correctness, forecast skill, calibrated coverage, causal identification, external
validation, deployment, investment performance, or real-world user impact.
