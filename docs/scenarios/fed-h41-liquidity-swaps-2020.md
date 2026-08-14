# March 2020 Federal Reserve H.4.1 liquidity-swap balance boundary replay

This counted scenario places a decision boundary at the archived March 26, 2020 H.4.1 release's
official stated 4:30 p.m. EDT time. Its two inputs are the March 18 and March 25 Wednesday
outstanding balances for central bank liquidity swaps in Table 1. The April 1 balance is locked
separately as a post-decision event and is absent from every ReplayPack source record.

H.4.1 reports the dollar value of foreign currency held under swap agreements using the exchange
rate used when the currency was acquired and to be used when returned to the foreign central
bank. The reported aggregate balance is therefore not current-market exposure, an institution or
transaction record, counterparty loss, P&L, policy effectiveness, or user activity.

## Three paired official releases

The supporting adapter retrieves the complete archived HTML and ASCII forms of the March 19,
March 26, and April 2 releases. It parses Table 1 independently from both formats and rejects a
pair unless the Wednesday balance, weekly average, weekly changes, unit, program, week, and
release identity agree exactly.

The current archived HTML wrappers contain changing front-end resource tokens. Raw downloaded
bytes remain content-addressed in the live receipts, while each normalized release fact binds a
stable cross-format semantic hash. That distinction prevents a current presentation-layer change
from being misrepresented as a historical financial-data revision.

The four decision-input response hashes are:

- March 19 HTML: `d08360db4285e0db87257f5f72b6e6eff91e3f937e9da00de0bbffb62dc0a515`;
- March 19 ASCII: `b5dc44df02874ba2f4d112a95a04449c924e5da68ee977dcd3fae1ca812bf571`;
- March 26 HTML: `a25a62443e7ee3bbda990ec2ef095624e1873c237819a81d1d17c6c7a2aef77e`;
- March 26 ASCII: `77157f38df055c43d46fb850d0534a5fd4836449df8067ed87612890f69b8819`.

The April 2 event HTML and ASCII response hashes are
`9e137f6651b46f4e49894ae027c7e47ecb9805e33ef617fa6a7fa663bde82041` and
`43d4857022652b21ab3cd9f4fd31aacbe69b4df28efa253d24fa7fe5d5def540`.
All six responses are bound by the idempotent supporting receipt
`312ef4c75191536fc8241076af9f42d7e55c90db8f47fdb91a38b11cab1b9580`.

`scenarios/fed-h41-liquidity-swaps-2020/input-lock.json` contains exactly two reported facts:

- week ending March 18: Wednesday outstanding balance `$45 million`, released March 19 at the
  page-stated 4:30 p.m. EDT;
- week ending March 25: Wednesday outstanding balance `$206,051 million`, released March 26 at
  the page-stated 4:30 p.m. EDT.

Both archived HTML pages explicitly state the release time, and the adapter validates it against
`America/New_York`. This is official stated timing, not an independently observed server log.
Current retrieval timestamps remain current and are never backdated to 2020.

## Transparent range with no probability

ShockCompiler uses only the two Wednesday balances available by the decision boundary:

- latest-known persistence baseline: `$206,051 million`;
- known March 18-to-March 25 increase: `$206,006 million`;
- stress endpoints: persistence at `$206,051 million`, or one repeat of the increase at
  `$412,057 million`;
- range width: `$206,006 million`;
- probability assigned: none.

Weekly averages and changes from the prior week or year remain reported source context and set
neither endpoint. The range is a transparent two-point stress construction, not a Federal Reserve
forecast, confidence interval, calibrated coverage band, policy model, or causal model.

## Disjoint post-decision event inside the range

`scenarios/fed-h41-liquidity-swaps-2020/event-lock.json` contains the April 1 Wednesday
outstanding balance of `$348,544 million`. The archived April 2 HTML/ASCII pair identifies its
release date but does not state an exact time. FinReplay therefore delays eligibility until the
following New York midnight, `2020-04-03T04:00:00Z`, rather than inventing a timestamp.

The event is `$142,493 million` above the fixed lower endpoint and `$63,513 million` below the
fixed upper endpoint. The verifier requires that inside-range result to remain visible but also
requires `forecast_success_claimed=false`: one later observation inside a mechanical range does
not establish forecasting skill, calibration, coverage, or policy correctness. The event never
changes either endpoint after the fact.

## Four relevant engines

TimeVault reconstructs the two-record decision set; ShockCompiler compiles the no-probability
persistence-or-one-known-increase endpoints; TrialCourt retains and rejects a retrospective
one-increase attempt; ReplayStudio exports a deterministic human- and machine-readable pack.
MarketTwin, ExecutionLab, and CapitalAllocator are absent because no counterparty network,
transaction, order, execution, portfolio, allocation, capital, return, or user evidence exists.

## Rebuild and counted proof

```bash
.venv/bin/python scripts/build_h41_liquidity_swaps_boundary_replaypack.py \
  --input-lock scenarios/fed-h41-liquidity-swaps-2020/input-lock.json \
  --output verification/replaypacks/fed-h41-liquidity-swaps-2020 \
  --code-commit 85b82f9cf69fa11fe670a06befbd7db3afca0bab

.venv/bin/python scripts/verify_h41_liquidity_swaps_boundary_replaypack.py \
  --input-lock scenarios/fed-h41-liquidity-swaps-2020/input-lock.json \
  --event-lock scenarios/fed-h41-liquidity-swaps-2020/event-lock.json \
  --pack verification/replaypacks/fed-h41-liquidity-swaps-2020 \
  --receipt verification/scenarios/rebuilds/fed-h41-liquidity-swaps-2020.json

.venv/bin/python scripts/verify_scenario_catalog.py
```

The clean-checkout receipt passes 29 assertions over two fresh directory and ZIP rebuilds. The
deterministic pack SHA-256 is
`a2603de494cd5c2e2aad527f8e30e2c832259c97bca931a6c146926c858d5584`, with stable trace
`trace:49f09dacda1e19d5e88d5d13be3614b8270ab7b7118cacc38e776a516c69ec41`.
The sealed eight-gate proof SHA-256 is
`563e14220cb7319cb9dc5fb209cf51603f95d51d0eb17530bad88a8c90e84b7c`.

These artifacts establish internal reproducibility, paired-format evidence, conservative timing,
truth-label separation, a visible inside-range event without success promotion, and deliberate
fail-closed adjudication. They do not establish method correctness, forecast skill, calibrated
coverage, causal identification, external validation, deployment, investment performance, or
real-world user impact.
