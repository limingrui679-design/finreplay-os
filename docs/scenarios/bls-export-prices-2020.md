# February 2020 BLS all-export price monthly-change boundary replay

This counted scenario places a decision boundary at the March 13, 2020 BLS *U.S. Import and
Export Price Indexes* release's stated 8:30 a.m. EDT embargo end. Its inputs are the January and
February all-export monthly changes as first reported. The March change is locked separately as a
post-decision event and is absent from every ReplayPack source record.

The all-export index aggregates U.S. export transaction prices, generally f.a.s. factory or
f.o.b., classified under Schedule B, using a modified Laspeyres formula. It is not seasonally
adjusted. The index is not export quantity, nominal export value, a tariff, PPI, an individual
exporter or shipment record, firm performance, P&L, or user activity.

## Three paired official releases

The supporting adapter retrieves the complete archived HTML and 18-page PDF forms of the February
14, March 13, and April 14 releases. It rejects a pair unless release identity, embargo timing,
headline and prior-month bridge, Table 2 values, technical definitions, revision policy, page
geometry, and nonblank text layers agree.

The four decision-input response hashes are:

- February 14 HTML: `dcac2c1daecc12c2bce0769999b467e25b4a4c6dea66af3538feb88fe72247ce`;
- February 14 PDF: `186c6a60276ac896bdf37e1db97e7c6a313dd5e2cd2087e592b2ae8a76323327`;
- March 13 HTML: `1b196f0ebed0fdd41d27a7696f956a5e962b1178b0687eade2ce06f845db15ae`;
- March 13 PDF: `e0167a9ec66bc0b884d0f58c5e7de42ddc8fd849f150bf438f9590f4be7fbbf9`.

The April 14 event HTML and PDF hashes are
`b5433f3a694f72261a14801e922459eb74cebea96c00ae1f6b2610ce5e786ae5` and
`215974814451294a33cfae984599752e5c9c5d1dc0e432031d8d49b484b6e382`.
All six responses are bound by the idempotent supporting receipt
`744153523c39d1b8df64900dad2544aec0d30c00c669f01ddc358cd64f5c630c`.

`scenarios/bls-export-prices-2020/input-lock.json` contains exactly two reported facts:

- January: `+70` basis points, released February 14 at 8:30 a.m. EST;
- February: `-110` basis points, released March 13 at 8:30 a.m. EDT.

The March 13 release also revises January from the locked first report of `+70` to `+60` basis
points, a `-10`-basis-point revision. That revision is retained as lineage but does not overwrite
the January input or set an endpoint. The adapter validates each timezone abbreviation against
`America/New_York`. Current retrieval timestamps remain current and are never backdated to 2020.

## Transparent range with no probability

ShockCompiler uses only the two first reports available by the decision boundary:

- latest-known persistence baseline: `-110` basis points;
- known January-to-February decline: `180` basis points;
- stress endpoints: one repeat of the decline at `-290`, or persistence at `-110` basis points;
- range width: `180` basis points;
- probability assigned: none.

Index levels, annual changes, detailed categories, and the later January revision remain source
context and set neither endpoint. The two-point range is not a BLS forecast, confidence interval,
calibrated coverage band, tariff model, PPI model, or causal model.

## Disjoint post-decision event inside the range

`scenarios/bls-export-prices-2020/event-lock.json` contains the March first report of `-160` basis
points, released April 14 at 8:30 a.m. EDT. That release repeats February's locked `-110` first
report, so the event snapshot records a zero February revision and overwrites no decision input.

The event lies inside the fixed `[-290, -110]` range, `130` basis points above its lower endpoint
and `50` below its upper endpoint. The verifier requires `inside_declared_range=true`,
`forecast_success_claimed=false`, and `range_changed_after_event=false`. Inclusion is labelled
post-event evaluation only; it does not validate the heuristic, establish calibrated coverage, or
permit an after-the-fact range change.

The event release's COVID-19 text reports survey timing, a response-rate comparison, and unchanged
estimation procedures. It is methodology context, not evidence of pandemic causality or proof that
measurement was unaffected.

## Four relevant engines

TimeVault reconstructs the two-record decision set; ShockCompiler compiles the no-probability
persistence-or-one-known-decline endpoints; TrialCourt retains and rejects a retrospective
one-decline attempt; ReplayStudio exports a deterministic human- and machine-readable pack.
MarketTwin, ExecutionLab, and CapitalAllocator are absent because no individual-exporter network,
shipment, order, execution, portfolio, allocation, capital, return, or user evidence exists.

## Rebuild and counted proof

```bash
.venv/bin/python scripts/build_export_price_boundary_replaypack.py \
  --input-lock scenarios/bls-export-prices-2020/input-lock.json \
  --output verification/replaypacks/bls-export-prices-2020 \
  --code-commit 49bc04ddd2d595d5f1e5e6b8064112619d1d64c6

.venv/bin/python scripts/verify_export_price_boundary_replaypack.py \
  --input-lock scenarios/bls-export-prices-2020/input-lock.json \
  --event-lock scenarios/bls-export-prices-2020/event-lock.json \
  --pack verification/replaypacks/bls-export-prices-2020 \
  --receipt verification/scenarios/rebuilds/bls-export-prices-2020.json

.venv/bin/python scripts/verify_scenario_catalog.py
```

The clean-checkout receipt passes 31 assertions over two fresh directory and ZIP rebuilds. The
deterministic pack SHA-256 is
`68677526e941a07bd9c0a7ddd5e364f3ca95174b90a899f53223b8083616f933`, with stable trace
`trace:60baf01f7379154c3eeeb58e854c73dcc9933c7b9178581f57e33d1c7687547e`.
The sealed eight-gate proof SHA-256 is
`a962565a750c729528239973b517105f4daf75b21c8c13da6c7247380752ff6d`.

These artifacts establish internal reproducibility, paired-format evidence, exact official stated
timing, revision lineage, truth-label separation, an inside-range evaluation that remains
explicitly non-success, and deliberate fail-closed adjudication. They do not establish method
correctness, forecast skill, calibrated coverage, causal identification, external validation,
deployment, investment performance, or real-world user impact.
