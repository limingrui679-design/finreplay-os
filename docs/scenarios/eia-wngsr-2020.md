# March 2020 EIA working-gas stock boundary replay

This counted scenario places a historical decision boundary at 2020-03-19 14:30 UTC, or
10:30 a.m. EDT. Its two inputs are the original Lower 48 working-gas estimates published by the
U.S. Energy Information Administration for the weeks ending March 6 and March 13, 2020. The
March 20 stock is locked separately as a post-decision event and is absent from every ReplayPack
source record.

These are sampled aggregate stock estimates in billion cubic feet (Bcf), not measurements of
individual facilities, operators, reservoirs, pipelines, injections, withdrawals, capacity,
transactions, prices, or returns. This case does not claim that FinReplay existed at the
historical boundary, forecast an EIA release, or attribute a stock change to the pandemic,
weather, policy, or any market cause.

## Revision-safe archive and knowledge boundary

`scenarios/eia-wngsr-2020/input-lock.json` contains exactly two reported original-release facts:

- March 12 release, week ending March 6: `2,043` Bcf, prior `2,091`, net change `-48` Bcf;
- March 19 release, week ending March 13: `2,034` Bcf, prior `2,043`, net change `-9` Bcf.

The adapter obtains original estimates from EIA's revision/reclassification workbook, then
cross-checks the selected rows against the current historical workbook. It also validates the
2020–2022 WNGSR performance evaluation, which states the normal Thursday 10:30 a.m. Eastern
schedule, reports that every release in that period met the established schedule, and identifies
March 19, 2020 as the first remote-posture release without publication disruption. The selected
March 12, 19, and 26 dates are non-holiday Thursdays, so `America/New_York` determines the exact
UTC instants. Current retrieval headers remain retrieval metadata and are never backdated.

The three official response hashes bound to every input are:

- revisions workbook: `ee7c703c6d30176d0253b879aa4c8c6dc0178b411c36d73036d89aeff412dd3c`;
- current historical workbook: `7973c8f5721c1addb2f8df496134aa0697a98f1f4eb9b075223f19f12f513b18`;
- 2020–2022 performance evaluation PDF:
  `de3123137bf3d5055181aa709e522caec0afe301a1077fca79a886ee5249536b`.

The official workbooks currently redirect once to an EIA same-host signed download URL. The HTTP
client permits only that exact one-hop path and exact signed-query shape; every other redirect
remains rejected. Full XLS and PDF responses stay in ignored content-addressed storage. The
repository retains only selected facts, canonical URLs, hashes, source semantics, and timing.

## Transparent range with no probability

ShockCompiler uses only the two original stock levels known at the boundary:

- latest-known persistence baseline: `2,034` Bcf;
- one known original-stock decline: `2,043 - 2,034 = 9` Bcf;
- stress endpoints: one repeat of the decline at `2,025`, or persistence at `2,034` Bcf;
- range width: `9` Bcf;
- probability assigned: none.

This is a transparent two-point stress construction, not an EIA forecast, confidence interval,
calibrated coverage band, inventory-flow model, storage-capacity estimate, price signal, causal
model, or trading recommendation. The official Lower 48 stock coefficient of variation is
`0.5%` for both inputs; the reported weekly-net-change standard errors are `0.6` and `0.8` Bcf.
Those statistical measures remain source metadata and set neither endpoint. EIA's rounded region
and subregion totals can differ slightly from the Lower 48 aggregate, so the recorded differences
are retained rather than forcibly reconciled.

## Disjoint post-decision breach

`scenarios/eia-wngsr-2020/event-lock.json` contains the March 26 release for the week ending
March 20: `2,005` Bcf, prior `2,034`, net change `-29` Bcf. Its 10:30 a.m. EDT release time is
2020-03-26 14:30 UTC, strictly after the decision boundary. Its record ID is disjoint from both
input IDs, and its selected-fact payload SHA-256 is
`920dc8ff96d03bbf03787b186d5759256fe86325626cbb08531f60282a4c8061`.

The event is `20` Bcf below the fixed `2,025` lower endpoint. The verifier requires this miss to
remain visible. It neither widens the range nor relabels the outcome as success. A transparent
miss is valid reproducibility evidence and negative evidence for coverage or forecast claims.

## Four relevant engines

TimeVault reconstructs the two-release original-vintage decision set; ShockCompiler compiles the
no-probability persistence-or-one-known-decline endpoints; TrialCourt retains and rejects a
retrospective one-decline attempt; ReplayStudio exports a deterministic human- and
machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator are absent because no
facility network, security, order, execution, portfolio, allocation, capital, return, or real-user
evidence exists for this question.

## Rebuild and counted proof

```bash
.venv/bin/python scripts/build_working_gas_stock_boundary_replaypack.py \
  --input-lock scenarios/eia-wngsr-2020/input-lock.json \
  --output verification/replaypacks/eia-wngsr-2020 \
  --code-commit 0626388d7b93cd948f639acbaf0f26a23d7b5314

.venv/bin/python scripts/verify_working_gas_stock_boundary_replaypack.py \
  --input-lock scenarios/eia-wngsr-2020/input-lock.json \
  --event-lock scenarios/eia-wngsr-2020/event-lock.json \
  --pack verification/replaypacks/eia-wngsr-2020 \
  --receipt verification/scenarios/rebuilds/eia-wngsr-2020.json

.venv/bin/python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt records 26 passing assertions, byte-identical directory and ZIP
rebuilds, deterministic pack SHA-256
`00e71ea907ad3783423f66cf9e933ef949176e563ba4ab559ec76367bb2cf0e7`, and stable trace
`trace:cf04545be3a46eba12a13716838e9284c9e91a457c38d950f60dfcffee8bd652`.
The sealed eight-gate proof SHA-256 is
`b5eb42ff77918ae0553cf82a9b51cf4e2ef79ff4461f915c702ba6570ce3caeb`.

These artifacts establish internal reproducibility, exact timing, official source hashes,
original-value recovery, current-history cross-checking, truth-label separation, a visible
out-of-range event, and deliberate fail-closed adjudication. They do not establish method
correctness, forecast skill, calibrated coverage, direct measurements, causal identification,
external validation, deployment, investment performance, or real-world user impact.
