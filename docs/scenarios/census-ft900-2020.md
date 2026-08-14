# March 2020 joint Census/BEA FT-900 trade-deficit level boundary replay

This counted scenario places a historical decision boundary at 2020-04-02 12:30 UTC, the exact
8:30 a.m. EDT time stated in the February 2020 *U.S. International Trade in Goods and Services*
release. Its decision evidence consists of the January-data and February-data joint Census/BEA
FT-900 release records. The May 5 March deficit and that release's revision snapshot are locked
separately as post-decision evidence and are absent from every ReplayPack source record.

The values are seasonally adjusted nominal deficit levels, not price-adjusted trade volume. This
case does not claim that FinReplay existed at the historical boundary, observe every firm,
shipment, customs entry, service transaction, or trade flow, isolate a COVID-19 or trade-policy
effect, or predict a future release.

## Official releases and knowledge boundary

`scenarios/census-ft900-2020/input-lock.json` contains exactly two reported release records:

- January 2020: an initial goods-and-services deficit of `$45,338 million` from the March 6
  release, retained as revision lineage;
- February 2020: an initial deficit of `$39,932 million` from the April 2 release. The same
  decision-time snapshot revises January from `$45,338 million` to `$45,482 million`.

The range deliberately uses the revised January and initial February levels co-published in the
single April 2 decision snapshot. It does not mix the stale `$45,338 million` January initial
value from one release with the `$39,932 million` February value from another. The March 6 report
states 8:30 a.m. EST, or 13:30 UTC; the April 2 report states 8:30 a.m. EDT, or 12:30 UTC. The
adapter validates those labels with `America/New_York`.

For each release, the adapter downloads and pairs the official PDF with the corresponding legacy
XLS ZIP. It validates release identities, exact timing, complete PDF structure and metadata,
Exhibit 1 workbook shape, all 31 ZIP members and their sizes, exact exports/imports/balance
components, component arithmetic within the source's one-million-dollar rounding tolerance,
prior-month revisions, release-snapshot lineage, methodology text, and PDF/XLS equality before
emitting one record.

The four decision-input responses are bound by SHA-256:

- January PDF: `b1cfa18560bc0bbb4c325d5b49bdba078407d6d247197ce1edc2d6ae30be61bf`;
- January XLS ZIP: `e64a8fb9028b84789ae930db99aa67e3fb0918da7e729349f7b0907bf62193f7`;
- February PDF: `5c32f19b5b556d479de8a7cd228bda3348e5b1ceec8dfd9d327d6a783847bb7c`;
- February XLS ZIP: `7527ba2aab574733774950ac68480d95d6f4286ddc630fca8198844503941e98`.

Current archive bytes and HTTP metadata are retrieval evidence only and are never backdated to
the historical release instant. Full source pairs remain in ignored content-addressed storage;
the repository retains the necessary facts, URLs, hashes, and release-snapshot provenance.

## Transparent range with no probability

ShockCompiler uses only the April 2 decision snapshot's two month levels:

- latest-known persistence baseline: `$39,932 million`;
- one known decision-snapshot decline: `45,482 - 39,932 = 5,550` million dollars;
- stress endpoints: one repeat of the decline at `$34,382 million`, or persistence at
  `$39,932 million`;
- range width: `$5,550 million`;
- probability assigned: none.

This is a transparent stress construction from two values in one official snapshot and one
difference. It is not a Census or BEA forecast, confidence interval, calibrated coverage band,
stationary-regime estimate, price model, pandemic-effect estimate, trade-policy conclusion, or
causal model. The release does not provide an applicable headline statistical-significance result
for this construction. Goods data use a complete enumeration of collected CBP documents rather
than a probability sample, but nonsampling errors remain possible and service estimates retain
their own limitations; the scenario does not convert document coverage into a claim of complete
economic measurement.

## Disjoint post-decision event and revisions

`scenarios/census-ft900-2020/event-lock.json` contains the May 5 release's initial March 2020
goods-and-services deficit of `$44,415 million`. Its 8:30 a.m. EDT release time is 2020-05-05
12:30 UTC, strictly after the decision boundary. The event record ID is disjoint from both input
IDs.

The same release snapshot retains January at `$45,482 million` and revises February from its
decision-time `$39,932 million` to `$39,810 million`, a `-$122 million` revision. Those later
snapshot values and revision deltas remain in the event lock and never overwrite the April 2
input. On the range fixed at the decision boundary, March is `$4,483 million` above the
`$39,932 million` upper endpoint. The verifier requires the miss to remain visible; it neither
widens the range nor relabels the outcome as success.

The event PDF and XLS ZIP hashes are
`e78fd48355753e763a569142743a19d9273d82bc10d229740029b7ed2a114ef7` and
`99d434fd762df6b942805bc2c9014003840db52a8b5203bfd2588a74e9dd5cf1`. They are verified as
current official archive evidence, not proof of release-time byte identity. The release's
COVID-19 language concerns publication standards. It is not treated as proof of causality,
complete response, or unaffected measurement.

## Four relevant engines

TimeVault reconstructs the two-release, two-record decision set; ShockCompiler compiles the
no-probability persistence-or-one-known-decline endpoints; TrialCourt retains and rejects a
retrospective one-decline attempt; ReplayStudio exports a deterministic human- and
machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator are absent because no firm
network, security, order, execution, portfolio, allocation, capital, return, or real-user evidence
exists for this question.

## Rebuild and counted proof

```bash
python scripts/build_trade_deficit_level_boundary_replaypack.py \
  --input-lock scenarios/census-ft900-2020/input-lock.json \
  --output verification/replaypacks/census-ft900-2020 \
  --code-commit ed5d27ebdbbcae9e0b90c4970216f05f36418975

python scripts/verify_trade_deficit_level_boundary_replaypack.py \
  --input-lock scenarios/census-ft900-2020/input-lock.json \
  --event-lock scenarios/census-ft900-2020/event-lock.json \
  --pack verification/replaypacks/census-ft900-2020 \
  --receipt verification/scenarios/rebuilds/census-ft900-2020.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 27 assertions over two fresh directory and ZIP rebuilds. The
proof at
`verification/scenarios/proofs/census-ft900-2020-trade-deficit-level-boundary-v1.json` binds the
supporting inventory, four paired-response hashes, locks, scripts, pack, receipt, truth labels,
same-snapshot persistence baseline, stale-initial-value exclusion, current-byte boundary,
no-probability range, TrialCourt rejection, exact event identity, revision isolation, and required
`$4,483 million` breach. This establishes internal reproducibility only—not forecast skill,
calibrated coverage, source or economic-method correctness, statistical significance,
price-adjusted trade volume, trade-policy or pandemic effects, trade, price, pandemic, policy,
sector, firm, or macroeconomic causality, external validation, deployment, investment performance,
or user impact.
