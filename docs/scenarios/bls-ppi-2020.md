# March 2020 BLS PPI final-demand monthly-change boundary replay

This counted scenario places a historical decision boundary at 2020-04-09 12:30 UTC, the exact
8:30 a.m. EDT embargo time printed in the March 2020 *Producer Price Indexes* release. Its two
inputs are the February and March final-demand monthly changes in the March 12 and April 9 BLS
releases. The May 13 release's April change is locked separately as a post-decision event and is
absent from every ReplayPack source record.

PPI measures average changes over time in prices received by domestic producers from the seller
perspective. These are aggregate index changes, not CPI, household costs, product or establishment
observations, transactions, quantities, revenues, profits, or returns. The scenario does not claim
that FinReplay existed at the historical boundary, forecast a BLS release, identify price-setting
or pass-through mechanisms, or attribute any change to COVID-19 or a market cause.

## Paired official archives and knowledge boundary

`scenarios/bls-ppi-2020/input-lock.json` contains exactly two reported release facts:

- March 12 release for February 2020: `-60` basis points (`-0.6%`), with January at `0.5%`;
- April 9 release for March 2020: `-20` basis points (`-0.2%`), with February retained at
  `-0.6%` and a zero revision delta.

The adapter downloads the dated BLS HTML and PDF for each release. It validates the release
identity, exact embargo line and `America/New_York` timezone, headline and prior values, year-over-
year value, Table 1, the technical PPI definition, the four-month revision rule, complete PDF page
structure and geometry, and additional tables before accepting the pair. Every nonblank PDF page
must extract, and the paired formats must agree. Current retrieval times remain retrieval metadata
and are never backdated to 2020.

The four decision-input responses are bound by SHA-256:

- February HTML: `515855b318616035f7d4a9d06672f90636f3ec3e424a630a0eb6076167573ca2`;
- February PDF: `392c9ee30d9deae5007a796917f8c332ecbc617e947a61b38562d67fc86c96b2`;
- March HTML: `318dafbdf942ea9ac3157e4369de66cc11f09994f7ff8d07de3c159cd9d3f9ec`;
- March PDF: `18540697b82c4cbb42703f24a44d808661bf2baf8883b135a2c1a385c1c6d7fb`.

The archived source pairs remain official public evidence. Their present availability and hashes
do not prove contemporaneous retrieval in 2020 or external validation of FinReplay's method.

## Transparent range with no probability

ShockCompiler uses only the two headline monthly changes known at the boundary:

- latest-known persistence baseline: `-20` basis points;
- one known February-to-March increase: `-20 - (-60) = 40` basis points;
- stress endpoints: persistence at `-20`, or one repeat of the increase at `+20` basis points;
- range width: `40` basis points;
- probability assigned: none.

This is a transparent two-point stress construction, not a BLS forecast, confidence interval,
calibrated coverage band, regime estimate, price model, or causal model. Unadjusted index levels,
12-month changes, and release methodology text remain source metadata and set neither endpoint.
PPI values are subject to revision four months after original publication.

## Disjoint post-decision breach

`scenarios/bls-ppi-2020/event-lock.json` contains the May 13 release's April 2020 final-demand
monthly change of `-130` basis points (`-1.3%`). Its 8:30 a.m. EDT release time is 2020-05-13
12:30 UTC, strictly after the decision boundary. The release preserves March at `-0.2%` with a
zero revision delta. Its record ID is disjoint from both input IDs.

The event is `110` basis points below the fixed `-20` lower endpoint. The verifier requires that
miss to remain visible; it neither widens the range nor relabels the outcome as success. The event
HTML and PDF hashes are
`f26f413c1b8aa505baaa25b995ce0ce69f280b6c30bf08b645dc24f0fdce9900` and
`eda79108129061e29ebccc1b26bce97df55326d66d4bb01855a9fdbafc8b067c`.
The retained BLS statement about response rates and estimation procedures is methodology text,
not proof of unaffected measurement or COVID-19 causality.

## Four relevant engines

TimeVault reconstructs the paired-release decision set; ShockCompiler compiles the no-probability
persistence-or-one-known-increase endpoints; TrialCourt retains and rejects a retrospective
one-change attempt; ReplayStudio exports a deterministic human- and machine-readable pack.
MarketTwin, ExecutionLab, and CapitalAllocator are absent because no producer network, security,
order, execution, portfolio, allocation, capital, return, or real-user evidence exists here.

## Rebuild and counted proof

```bash
.venv/bin/python scripts/build_ppi_boundary_replaypack.py \
  --input-lock scenarios/bls-ppi-2020/input-lock.json \
  --output verification/replaypacks/bls-ppi-2020 \
  --code-commit 4ee8d1281a67286caa7880c371ea24a65f8bf28f

.venv/bin/python scripts/verify_ppi_boundary_replaypack.py \
  --input-lock scenarios/bls-ppi-2020/input-lock.json \
  --event-lock scenarios/bls-ppi-2020/event-lock.json \
  --pack verification/replaypacks/bls-ppi-2020 \
  --receipt verification/scenarios/rebuilds/bls-ppi-2020.json

.venv/bin/python scripts/verify_scenario_catalog.py
```

The clean-checkout receipt passes 27 assertions over two fresh directory and ZIP rebuilds. The
deterministic pack SHA-256 is
`4601bc2b1f9b751197d253bd2f27497a08743e34b6cdef28033fe87501fd96b3`, with stable trace
`trace:8d27b18954bac6b955d03e59e10dc9e3011cf926aa0a1347e86d6473f6e8fea5`.
The sealed eight-gate proof SHA-256 is
`27daf92f64dda57e3dc6e101db49daf9a3f56dcc811792245fb59459fde15ae6`.

These artifacts establish internal reproducibility, exact timing, paired official-source hashes,
release-snapshot and truth-label separation, a visible out-of-range event, and deliberate
fail-closed adjudication. They do not establish method correctness, forecast skill, calibrated
coverage, causal identification, external validation, deployment, investment performance, or
real-world user impact.
