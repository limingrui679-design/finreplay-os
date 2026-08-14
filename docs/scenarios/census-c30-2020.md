# March 2020 Census Construction Spending level boundary replay

This counted scenario places a historical decision boundary at 2020-04-01 14:00 UTC, the exact
10:00 a.m. EDT time printed on the archived April 1 U.S. Census Bureau Monthly Construction
Spending release. Its two inputs are the preliminary January and February total-construction
seasonally adjusted annual-rate levels from their respective initial releases. The May 1 March
level and that release's revisions are locked separately as post-decision evidence and are absent
from every ReplayPack source record.

The values are nominal annual rates adjusted for seasonality but not for price changes. This case
does not claim that FinReplay existed at the historical boundary, observe real construction
volume, projects, firms, regions, transactions, investments, prices, pandemic or policy effects,
or predict a future release.

## Official releases and knowledge boundary

`scenarios/census-c30-2020/input-lock.json` contains exactly two reported Table 1 facts:

- January 2020: preliminary total construction of `$1,369,223 million` SAAR from the March 2
  release;
- February 2020: preliminary total construction of `$1,366,697 million` SAAR from the April 1
  release.

Each value is the current reference month's initial-release level. The scenario deliberately does
not replace January's March 2 value with the April 1 release's revised January value of
`$1,384,486 million`. Keeping the initial-release sequence provides a simple, auditable boundary
over two equivalently positioned snapshots; it is not Census's official month-over-month series.

The adapter requires each selected official release to contain a six-page `612 x 792` PDF with a
nonblank text layer and a bounded, structurally valid XLSX workbook. It validates the release
identity, exact 10:00 a.m. EST/EDT time under `America/New_York`, page and sheet structure,
headline and Table 1 levels, private/public components, Table 2 totals, Table 3 sampling facts,
Table 4 annual facts when present, methodology text, status markers, and revision notices. Values
must cross-check between the paired PDF and XLSX before a record is emitted.

The four decision-input responses are bound by SHA-256:

- January PDF: `73d0e0ec0216d74255ebcafb316a2081a91b80ef76a34e07f6b31c79d57f9918`;
- January XLSX: `a224c4f710f41c610725fe58c88bbf7263a02bfcaaeeab425cc2697cd7461f4d`;
- February PDF: `c212b816fce0823d3e15b01c35d306253bb86280581a3a7d61421ba614dc25bb`;
- February XLSX: `566f2267ff69d815ce4bf1ffac6206775d0e3696ea79102352444e051e405579`.

Current HTTP metadata is retrieval evidence only and is never backdated. Full source pairs remain
in ignored content-addressed storage; the repository retains minimal facts, URLs, hashes, and
release-snapshot provenance.

## Transparent range with no probability

ShockCompiler uses only the two initial current-month levels:

- latest-known persistence baseline: `$1,366,697 million` SAAR;
- one known initial-level decline: `1,369,223 - 1,366,697 = 2,526` million dollars;
- stress endpoints: one repeat of the decline at `$1,364,171 million`, or persistence at
  `$1,366,697 million`;
- range width: `$2,526 million`;
- probability assigned: none.

This is a transparent stress construction from two values and one difference. The
`$2,526 million` step is not the official February month-over-month change, because the official
change uses January's revised value in the April release. It is not a forecast, confidence interval,
calibrated coverage band, stationary-regime estimate, or causal model. Census's reported
90-percent sampling intervals remain source metadata and are not used as FinReplay endpoints.

## Disjoint post-decision event and revisions

`scenarios/census-c30-2020/event-lock.json` contains the May 1 release's preliminary March 2020
total-construction level of `$1,360,512 million` SAAR. Its stated 10:00 a.m. EDT release time is
2020-05-01 14:00 UTC, strictly after the decision boundary. The event record ID is disjoint from
both input IDs.

The same May release revises January from `$1,384,486 million` to `$1,382,963 million` and
February from its initial `$1,366,697 million` to `$1,348,386 million`. These later-snapshot
values and their `-$1,523 million` and `-$18,311 million` deltas remain in the event lock and
never overwrite the initial-release inputs.

Census reports March's official monthly change as `+0.9%` because that calculation compares
March with the May release's revised February denominator of `$1,348,386 million`. The separate
initial-level evaluation compares March with the range fixed at the April 1 boundary. On that
predeclared scale, the March level is `$3,659 million` below the `$1,364,171 million` lower
endpoint. The verifier requires the miss to remain visible; it neither widens the range nor
relabels the outcome as success.

## Four relevant engines

TimeVault reconstructs the two-release, two-record decision set; ShockCompiler compiles the
no-probability persistence-or-one-known-initial-decline endpoints; TrialCourt retains and rejects
a retrospective one-decline attempt; ReplayStudio exports a deterministic human- and
machine-readable pack. MarketTwin, ExecutionLab, and CapitalAllocator are absent because no
project network, security, order, execution, portfolio, allocation, capital, return, or real-user
evidence exists for this question.

## Rebuild and counted proof

```bash
python scripts/build_construction_spending_boundary_replaypack.py \
  --input-lock scenarios/census-c30-2020/input-lock.json \
  --output verification/replaypacks/census-c30-2020 \
  --code-commit c6c78190231ead524db37c22fdbdfd5c7101acc2

python scripts/verify_construction_spending_boundary_replaypack.py \
  --input-lock scenarios/census-c30-2020/input-lock.json \
  --event-lock scenarios/census-c30-2020/event-lock.json \
  --pack verification/replaypacks/census-c30-2020 \
  --receipt verification/scenarios/rebuilds/census-c30-2020.json

python scripts/verify_scenario_catalog.py
```

The clean-worktree receipt passes 25 assertions over two fresh directory and ZIP rebuilds. The
proof at
`verification/scenarios/proofs/census-c30-2020-construction-spending-boundary-v1.json` binds the
supporting inventory, paired-response hashes, locks, scripts, pack, receipt, truth labels,
persistence baseline, initial-level-versus-official-change distinction, sampling-interval
exclusion, no-probability range, TrialCourt rejection, exact event identity, revision isolation,
and required `$3,659 million` breach. This establishes internal reproducibility only—not forecast
skill, calibrated coverage, source or economic-method correctness, real volume, project, firm,
regional, construction, inflation, pandemic, or policy causality, external validation,
deployment, investment performance, or user impact.
