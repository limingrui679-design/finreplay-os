# Western Alliance 2023 seven-engine funding boundary replay

This counted scenario places a Western Alliance Bancorporation decision boundary at
2023-05-02 16:00 UTC. Its seven inputs are 2022 year-end facts from Form 10-K accession
`0001212545-23-000093`, accepted by EDGAR on 2023-02-23 20:54:24 UTC. A separate event lock records
Form 8-K accession `0001212545-23-000122`, accepted later on 2023-05-02 at 17:08:31 UTC. The event
record is excluded from the decision-input manifest.

The exact same-day boundary makes future-information exclusion directly testable. It does not show
that the selected accounting facts caused the later filing, subsequent market moves, or any other
outcome; nor does it imply that FinReplay OS existed at the historical decision time.

## Locked historical inputs

`scenarios/western-alliance-2023/input-lock.json` contains exactly seven positive USD facts for
2022-12-31:

- assets: $67,734,000,000;
- deposits: $53,644,000,000;
- stockholders' equity: $5,356,000,000;
- held-to-maturity securities: $1,289,000,000;
- HTM accumulated unrecognized holding loss: $177,000,000;
- available-for-sale debt securities: $7,092,000,000; and
- AFS accumulated gross unrealized loss before tax: $890,000,000.

Every record preserves its original SEC concept, accession, period end, exact EDGAR acceptance
availability time, source URL, complete-response hash, and `reported` label. The lock is a selected
subset of filer-reported facts, not the complete filing or a regulator finding.

`scenarios/western-alliance-2023/event-lock.json` contains the later SEC submissions record. The
event-lock validator requires exact official timing, immutable-event coverage, an HTTPS source,
and availability strictly after the decision time. The scenario proof additionally rejects any
overlap between that record and ReplayPack sources.

## Seven-engine flow and boundaries

The reusable bank-boundary builder runs TimeVault, a rejected retrospective TrialCourt attempt, a
three-node MarketTwin envelope, a two-endpoint ShockCompiler program, a visibly simulated
reference-only ExecutionLab boundary, an all-cash CapitalAllocator solution, and ReplayStudio.
Scenario-specific identifiers, source concepts, values, timestamps, locks, hashes, claims, and
limitations remain distinct from SVB and PacWest.

No historical quote, order book, deposit-flow observation, execution venue, customer portfolio, or
live capital is represented. Network propagation and accounting ratios are inferred; execution and
allocation inputs are simulated; the result is not a trading signal, order, recommendation, causal
finding, or externally reviewed method.

## Rebuild and counted proof

```bash
python scripts/build_bank_boundary_replaypack.py \
  --input-lock scenarios/western-alliance-2023/input-lock.json \
  --output verification/replaypacks/western-alliance-2023-seven-engine

python scripts/verify_bank_boundary_replaypack.py \
  --input-lock scenarios/western-alliance-2023/input-lock.json \
  --event-lock scenarios/western-alliance-2023/event-lock.json \
  --pack verification/replaypacks/western-alliance-2023-seven-engine \
  --receipt verification/evidence/western-alliance-seven-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The committed clean-worktree receipt passes 13 assertions over two fresh rebuilds. The proof at
`verification/scenarios/proofs/western-alliance-2023-deposit-boundary-v1.json` binds the exact
locks, scripts, pack, receipt, labels, naive all-cash baseline, TrialCourt rejection, and
limitations. This proves internal reproducibility only—not historical completeness, domain-method
correctness, deployment, investment performance, external validation, or user impact.
