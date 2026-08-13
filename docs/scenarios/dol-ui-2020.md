# March 2020 DOL initial-claims surge boundary replay

This counted scenario places a historical decision boundary at 2020-03-20 12:00 UTC. Its two
inputs are the advance seasonally adjusted initial-claims values for the weeks ending March 7 and
14, reported in archived U.S. Department of Labor Unemployment Insurance Weekly Claims releases.
The March 21 value is locked separately as a post-decision event and is absent from every
ReplayPack source record.

The source is the U.S. Department of Labor Employment and Training Administration. This case does
not claim that FinReplay existed at the historical decision time, identify individual claimants or
employers, or attribute claims changes to a particular pandemic, policy, industry, or causal
mechanism.

## Archived releases and knowledge boundary

`scenarios/dol-ui-2020/input-lock.json` contains:

- March 12 release, week ending March 7: `211,000` persons, down `4,000` from a prior level
  revised from `216,000` to `215,000`;
- March 19 release, week ending March 14: `281,000` persons, up `70,000` from the unrevised
  `211,000`.

Each official nine-page PDF must contain exactly one page-stated 8:30 a.m. Eastern embargo end,
one seasonally adjusted initial-claims headline, the matching USDL release number, and technical
notes describing ETA 538 advance data and following-week revision. Headline arithmetic and any
prior-week revision bridge must reconcile exactly.

FinReplay makes each exact archived PDF eligible at the later of its stated embargo end or its
official `Last-Modified` timestamp. The March 12 bytes therefore become eligible at
2020-03-12 12:30:10 UTC; the March 19 bytes become eligible at 2020-03-19 12:30:00 UTC. Both
precede the decision boundary. This rule preserves official server timing without treating
`Last-Modified` as proof of the actual first-publication second.

The March 19 PDF explicitly says that the release applies annual revisions to weekly-claims
seasonal-adjustment factors and the resulting seasonally adjusted history from 2015 onward.
Accordingly, the two adjacent release snapshots are not represented as a calibrated stationary
sample.

ShockCompiler uses only information in those two snapshots:

- latest-known persistence baseline: `281,000` persons;
- one known March 7-to-March 14 increase: `70,000` persons;
- stress endpoints: persistence at `281,000`, or one repeat of the known increase at `351,000`;
- range width: `70,000` persons;
- probability assigned: none.

The range is not a forecast, confidence interval, calibrated coverage statement, pandemic model,
labor-market causal model, trading signal, or policy recommendation.

## Disjoint post-decision breach and revision

`scenarios/dol-ui-2020/event-lock.json` contains the March 26 release for the week ending March
21: `3,283,000` persons, up `3,001,000` from a prior-week level revised from `281,000` to
`282,000`. The exact PDF becomes eligible at its official `Last-Modified` time of
2020-03-26 12:46:21 UTC, after the decision boundary.

The earlier March 19 snapshot remains `281,000`; the later `282,000` revision is retained only in
the event record and never overwrites the decision input. The reported March 21 event is
`2,932,000` persons above the previously declared upper endpoint of `351,000`. The verifier
requires this miss to remain visible. It does not widen the range, relabel the outcome as a
success, or leak either the outcome or later revision into the pack. A transparent miss is valid
reproducibility evidence but strongly negative evidence for coverage or forecast claims.

## Four relevant engines

TimeVault reconstructs the two-release decision set; ShockCompiler compiles the no-probability
persistence-or-one-known-increase endpoints; TrialCourt retains and rejects a retrospective
one-increase attempt; ReplayStudio exports a deterministic human- and machine-readable pack.
MarketTwin, ExecutionLab, and CapitalAllocator are absent because no claimant/employer network,
position, order, execution, portfolio, allocation, or return is represented.

## Rebuild and counted proof

```bash
python scripts/build_initial_claims_boundary_replaypack.py \
  --input-lock scenarios/dol-ui-2020/input-lock.json \
  --output verification/replaypacks/dol-ui-2020

python scripts/verify_initial_claims_boundary_replaypack.py \
  --input-lock scenarios/dol-ui-2020/input-lock.json \
  --event-lock scenarios/dol-ui-2020/event-lock.json \
  --pack verification/replaypacks/dol-ui-2020 \
  --receipt verification/evidence/dol-ui-four-engine-rebuild.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 20 assertions over two fresh directory and ZIP rebuilds. The
proof at `verification/scenarios/proofs/dol-ui-2020-initial-claims-boundary-v1.json` binds the
supporting inventory, source hashes, locks, scripts, pack, receipt, truth labels, persistence
baseline, no-probability marker, TrialCourt rejection, exact event identity, later revision
isolation, and required `2,932,000`-person breach. This establishes internal reproducibility
only—not forecast skill, calibrated coverage, pandemic or labor-market causality, policy
effectiveness, external validation, deployment, investment performance, or user impact.
