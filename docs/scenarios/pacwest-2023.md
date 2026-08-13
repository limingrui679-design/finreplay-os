# PacWest 2023 seven-engine funding boundary replay

This counted scenario reconstructs a deliberately narrow PacWest Bancorp decision boundary at
2023-05-03 20:00 UTC. Its seven decision inputs come only from the 2022 Form 10-K accession
`0001628280-23-005257`, accepted by EDGAR on 2023-02-27 16:36:42 UTC. A separate event lock records
the Form 8-K accession `0001104659-23-055748`, accepted on 2023-05-04 06:37:37 UTC. That later filing
is official event-timing evidence and is cryptographically excluded from the ReplayPack input
manifest.

This is not a claim that the selected facts caused later market moves, that the decision time was
used by a live system, or that the later filing contains a regulator conclusion.

## Locked historical inputs

`scenarios/pacwest-2023/input-lock.json` contains exactly seven positive USD facts for
2022-12-31:

- assets: $41,228,936,000;
- deposits: $33,936,334,000;
- stockholders' equity: $3,950,531,000;
- held-to-maturity securities: $2,270,635,000;
- HTM accumulated unrecognized holding loss: $158,671,000;
- available-for-sale debt securities: $4,843,487,000; and
- accumulated loss on AFS securities in an unrealized-loss position: $811,136,000.

The last measure uses the filing concept
`DebtSecuritiesAvailableForSaleUnrealizedLossPositionAccumulatedLoss`. It is not silently renamed
as a total portfolio fair-value gap. Every record preserves the original concept, accession,
period end, EDGAR acceptance availability time, source URL, response hash, and `reported` label.

`scenarios/pacwest-2023/event-lock.json` contains one SEC submissions record for the later 8-K.
The proof verifier requires its availability time to follow the decision time and rejects any
overlap with the seven decision inputs.

## Seven-engine flow

1. TimeVault reproduces the seven-record point-in-time set.
2. TrialCourt retains a retrospective attempt and rejects it after all six attack findings.
3. MarketTwin builds a three-node, two-edge reported portfolio graph and a mechanical loss
   envelope.
4. ShockCompiler evaluates zero and the reported HTM loss-ratio endpoint without assigning a
   probability.
5. ExecutionLab exposes the absence of historical microstructure through a simulated,
   reference-only execution boundary.
6. CapitalAllocator combines the inferred and simulated bounds and retains the all-cash model
   solution; it emits no order or recommendation.
7. ReplayStudio exports a deterministic, evidence-labelled ReplayPack.

## Rebuild and counted proof

```bash
python scripts/build_bank_boundary_replaypack.py \
  --input-lock scenarios/pacwest-2023/input-lock.json \
  --output verification/replaypacks/pacwest-2023-seven-engine

python scripts/verify_bank_boundary_replaypack.py \
  --input-lock scenarios/pacwest-2023/input-lock.json \
  --event-lock scenarios/pacwest-2023/event-lock.json \
  --pack verification/replaypacks/pacwest-2023-seven-engine \
  --receipt verification/evidence/pacwest-seven-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree verifier reruns all seven engines twice and passes 13 assertions, including
byte-identical directories and ZIPs, a stable cross-engine trace, visible simulation, TrialCourt
rejection, the all-cash boundary, and post-decision-event exclusion. The proof at
`verification/scenarios/proofs/pacwest-2023-funding-boundary-v1.json` binds the exact locks, scripts,
pack, receipt, baselines, failure modes, evidence labels, and limitations.

Internal determinism does not establish source authenticity beyond the locks, historical
completeness, causal or domain-method correctness, public deployment, investment performance,
external review, or user impact.
