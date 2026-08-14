# February 2020 BLS all-import price monthly-change boundary replay

This counted scenario places a decision boundary at the March 13, 2020 BLS *U.S. Import and
Export Price Indexes* release's stated 8:30 a.m. EDT embargo end. Its inputs are the January and
February all-import monthly changes as first reported. The March change is locked separately as a
post-decision event and is absent from every ReplayPack source record.

The all-import index aggregates U.S.-dollar transaction prices paid by U.S. importers, generally
on an f.o.b. foreign-port or c.i.f. U.S.-port basis, using a modified Laspeyres formula. It is not
seasonally adjusted. The index is not import quantity, nominal trade value, a tariff, CPI, an
importer or shipment record, firm performance, P&L, or user activity.

## Three paired official releases

The supporting adapter retrieves the complete archived HTML and 18-page PDF forms of the February
14, March 13, and April 14 releases. It rejects a pair unless release identity, embargo timing,
headline and prior-month bridge, Table 1 values, technical definitions, revision policy, page
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
`321264932de6a555118e505c858c0f1fb648cd2f9cd296fb4a4801258f483864`.

`scenarios/bls-import-prices-2020/input-lock.json` contains exactly two reported facts:

- January: `0` basis points, released February 14 at 8:30 a.m. EST;
- February: `-50` basis points, released March 13 at 8:30 a.m. EDT.

The March 13 release also revises January from the locked first report of `0` to `+10` basis
points. That revision is retained as lineage but does not overwrite the January input or set an
endpoint. The adapter validates each timezone abbreviation against `America/New_York`. Current
retrieval timestamps remain current and are never backdated to 2020.

## Transparent range with no probability

ShockCompiler uses only the two first reports available by the decision boundary:

- latest-known persistence baseline: `-50` basis points;
- known January-to-February decline: `50` basis points;
- stress endpoints: one repeat of the decline at `-100`, or persistence at `-50` basis points;
- range width: `50` basis points;
- probability assigned: none.

Index levels, annual changes, detailed categories, and the later January revision remain source
context and set neither endpoint. The two-point range is not a BLS forecast, confidence interval,
calibrated coverage band, tariff model, CPI model, or causal model.

## Disjoint post-decision event below the range

`scenarios/bls-import-prices-2020/event-lock.json` contains the March first report of `-230` basis
points, released April 14 at 8:30 a.m. EDT. That release revises February from the locked `-50`
first report to `-70` basis points, a `-20`-basis-point revision. Both values remain in the event
snapshot and overwrite no decision input.

The event is `130` basis points below the fixed `-100` lower endpoint. The verifier requires the
miss to remain visible, requires `forecast_success_claimed=false`, and requires
`range_changed_after_event=false`. A range miss does not validate the heuristic, and the event
cannot be used to widen the range retroactively.

The event release's COVID-19 text reports survey timing, a response-rate comparison, and unchanged
estimation procedures. It is methodology context, not evidence of pandemic causality or proof that
measurement was unaffected.

## Four relevant engines

TimeVault reconstructs the two-record decision set; ShockCompiler compiles the no-probability
persistence-or-one-known-decline endpoints; TrialCourt retains and rejects a retrospective
one-decline attempt; ReplayStudio exports a deterministic human- and machine-readable pack.
MarketTwin, ExecutionLab, and CapitalAllocator are absent because no importer network, shipment,
order, execution, portfolio, allocation, capital, return, or user evidence exists.

## Rebuild and counted proof

```bash
.venv/bin/python scripts/build_import_price_boundary_replaypack.py \
  --input-lock scenarios/bls-import-prices-2020/input-lock.json \
  --output verification/replaypacks/bls-import-prices-2020 \
  --code-commit 5c39012bb6e16482f750b85f32afb33d627ae358

.venv/bin/python scripts/verify_import_price_boundary_replaypack.py \
  --input-lock scenarios/bls-import-prices-2020/input-lock.json \
  --event-lock scenarios/bls-import-prices-2020/event-lock.json \
  --pack verification/replaypacks/bls-import-prices-2020 \
  --receipt verification/scenarios/rebuilds/bls-import-prices-2020.json

.venv/bin/python scripts/verify_scenario_catalog.py
```

The clean-checkout receipt passes 30 assertions over two fresh directory and ZIP rebuilds. The
deterministic pack SHA-256 is
`e6f74743d81e53a82297665e74d90da4718948f19cfeac29c8fe710a82c4eb43`, with stable trace
`trace:4c7ab74268838bf6a5cc5f70421e607b45ca64eacc1169766ad2dbfdc094cb4a`.
The sealed eight-gate proof SHA-256 is
`46920362e65d74958afe8bb2baa2eba02b89d8c26537cd1297f0fbc5216fa078`.

These artifacts establish internal reproducibility, paired-format evidence, exact official stated
timing, revision lineage, truth-label separation, a visible range miss, and deliberate fail-closed
adjudication. They do not establish method correctness, forecast skill, calibrated coverage,
causal identification, external validation, deployment, investment performance, or real-world
user impact.
