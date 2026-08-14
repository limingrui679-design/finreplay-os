# March 2020 Census/HUD new-home-sales level boundary replay

This counted scenario places a historical decision boundary at 2020-03-24 14:00 UTC, the exact
10:00 a.m. EDT time printed in the February 2020 *Monthly New Residential Sales* release. Its
decision evidence consists of the January-data and February-data Census/HUD release records. The
April 23 March sales rate and that release's revision of February are locked separately as
post-decision evidence and are absent from every ReplayPack source record.

The values are seasonally adjusted annual rates (SAAR), not actual monthly transaction counts. A
source-defined sale means that a deposit was taken or a sales agreement was signed and may occur
before permit issuance; it is not necessarily a closing, mortgage, completed home, buyer, builder,
or property record. This case does not claim that FinReplay existed at the historical boundary,
isolate a COVID-19 or housing-policy effect, or predict a future release.

## Official releases and knowledge boundary

`scenarios/census-nrs-2020/input-lock.json` contains exactly two reported release records:

- January 2020: an initial national sales rate of `764,000` units SAAR from the February 26
  release, retained as revision lineage;
- February 2020: an initial rate of `765,000` from the March 24 release. The same decision-time
  snapshot revises January from `764,000` to `800,000`.

The range deliberately uses the revised January and initial February values co-published in the
single March 24 decision snapshot. It does not mix the stale `764,000` January initial value from
one release with the `765,000` February value from another. The February 26 report states 10:00
a.m. EST, or 15:00 UTC; the March 24 report states 10:00 a.m. EDT, or 14:00 UTC. The adapter
validates both labels with `America/New_York`.

Each response must be an official five-page, `612 x 792` PDF with nonblank text on every page and
the exact page-title sequence. The adapter cross-checks release identity and number, headline
facts, explanatory notes, Table 1a national value, revised prior, monthly change, 90-percent
sampling margin, and average RSE before emitting one record. Current HTTP metadata are retrieval
evidence only and are never backdated to the historical release instant.

The two decision-input PDF responses are bound by SHA-256:

- January PDF: `ba86558efb14745ddf6c56684c9023444397941a0c49bed406e1d6eda6dcca3b`;
- February PDF: `9a47e1fd70c0830394a9681ec0bc1881e1d0522c105ff9aeff60dd01c98c3fb8`.

Full PDFs remain in ignored content-addressed storage. The repository retains the necessary facts,
URLs, hashes, source warnings, and release-snapshot provenance.

## Transparent range with no probability

ShockCompiler uses only the March 24 decision snapshot's two month levels:

- latest-known persistence baseline: `765,000` units SAAR;
- one known decision-snapshot decline: `800,000 - 765,000 = 35,000` units SAAR;
- stress endpoints: one repeat of the decline at `730,000`, or persistence at `765,000`;
- range width: `35,000` units SAAR;
- probability assigned: none.

This is a transparent stress construction from two values in one official snapshot and one
difference. It is not a Census/HUD forecast, official confidence interval, calibrated coverage
band, stationary-regime estimate, housing-price model, pandemic-effect estimate, housing-policy
conclusion, or causal model. The February headline's `-4.4 percent (±14.8 percent)` 90-percent
sampling interval includes zero and remains reported source metadata. It does not define either
FinReplay endpoint.

## Disjoint post-decision event and revision

`scenarios/census-nrs-2020/event-lock.json` contains the April 23 release's initial March 2020
sales rate of `627,000` units SAAR. Its 10:00 a.m. EDT release time is 2020-04-23 14:00 UTC,
strictly after the decision boundary. The event record ID is disjoint from both input IDs.

The same release revises February from its decision-time `765,000` value to `741,000`, a
`-24,000` revision. That later value remains in the event lock and never overwrites the March 24
input. On the range fixed at the decision boundary, March is `103,000` units SAAR below the
`730,000` lower endpoint. The verifier requires the miss to remain visible; it neither widens the
range nor relabels the outcome as success.

The event PDF hash is
`c3d0d06001540a5dbdca154eb6c61139b8a8aaa9b9ec205bcca4fc67ee30575a`.
Its reported `-15.4 percent (±14.8 percent)` monthly change excludes zero at the source's stated
90-percent sampling level, but this post-decision fact is not a range input or proof of predictive
skill. The release's COVID-19 language says the estimates met publication standards. It is not
treated as proof of causality, complete response, or unaffected measurement.

## Four relevant engines

TimeVault reconstructs the two-release, two-record decision set; ShockCompiler compiles the
no-probability persistence-or-one-known-decline endpoints; TrialCourt retains and rejects a
retrospective one-decline attempt; ReplayStudio exports a deterministic human- and
machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator are absent because no
property network, security, order, execution, portfolio, allocation, capital, return, or real-user
evidence exists for this question.

## Rebuild and counted proof

```bash
.venv/bin/python scripts/build_new_home_sales_level_boundary_replaypack.py \
  --input-lock scenarios/census-nrs-2020/input-lock.json \
  --output verification/replaypacks/census-nrs-2020 \
  --code-commit b61b207b010a56fccb023951bbfd462b0ab84687

.venv/bin/python scripts/verify_new_home_sales_level_boundary_replaypack.py \
  --input-lock scenarios/census-nrs-2020/input-lock.json \
  --event-lock scenarios/census-nrs-2020/event-lock.json \
  --pack verification/replaypacks/census-nrs-2020 \
  --receipt verification/scenarios/rebuilds/census-nrs-2020.json

.venv/bin/python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt records 26 passing assertions, byte-identical directory and ZIP
rebuilds, deterministic pack SHA-256
`87ea7da019f6ad6255f0375650e9906efd7695108d13618d9630ce0685483d4d`, and stable trace
`trace:3954cc711c3352c64a9af890452323f9318a8a5dcfe936263b5811392aa77f6e`.
The sealed eight-gate proof SHA-256 is
`fbe73632a91f4a43d1ee06e6e0134228d1c8b6e60e34683f4ee7a0fbbcb55855`.

These artifacts establish internal reproducibility, explicit timing, source hashes, revision
lineage, truth-label separation, a visible out-of-range event, and deliberate fail-closed
adjudication. They do not establish method correctness, forecast skill, calibrated coverage,
actual monthly transaction counts, external validation, deployment, investment performance, or
real-world user impact.
