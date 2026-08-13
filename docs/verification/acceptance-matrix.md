# FinReplay OS completion and evidence matrix

This matrix defines “done.” A passing unit test, README claim, schema file, source URL, generated
row count, or self-assigned score cannot prove a broader requirement than it actually exercises.

Status vocabulary: `NOT_STARTED`, `IN_PROGRESS`, `PROVEN`, `BLOCKED_EXTERNAL`.

## A. Seven connected engines — 20 points

| ID | Requirement | Required authoritative evidence | Current status |
|---|---|---|---|
| A1 | TimeVault supports bitemporal append, revision history, and as-of knowledge queries | Unit/property tests plus a real-source golden replay proving future revisions are excluded | IN_PROGRESS |
| A2 | TrialCourt records preregistration, every attempt, leakage/multiplicity/regime/execution attacks, and disposition | Immutable trial ledger, method tests against published examples, negative-result fixture | IN_PROGRESS — hash-chain ledger, six attack classes, Holm example, negative-result retention and tamper tests, and a rejected retrospective SVB attempt are committed; published-method comparison and external method review remain |
| A3 | MarketTwin stores evidence-graded temporal institution/security graphs and bounded contagion | Graph contract tests plus at least one live official multi-source graph | IN_PROGRESS — append-only temporal graph, latest-only fallback, hand-checked bounded propagation, and SEC/FDIC/Treasury SVB graph receipt implemented; external domain review remains |
| A4 | ShockCompiler distinguishes observed reconstruction, bounds, counterfactual, and adversarial perturbations | Scenario compiler tests and machine-readable provenance for every shock | IN_PROGRESS — mode-specific contracts, grid limits, provenance-preserving compiler, four-mode evidence, and integration into the actual SVB ReplayPack are committed; external method review remains |
| A5 | ExecutionLab models non-zero costs, latency/capacity and data-dependent precision tiers | Golden microstructure replay and conservative fallback tests | PROVEN — 37 unit/boundary tests plus a deterministic four-case hand-calculated receipt cover quote, OHLCV, reference-only, limit, queue, timing, provenance and tamper paths; this is internal method evidence, not an observed fill or external review |
| A6 | CapitalAllocator supports constraints, robust solutions, reversal surfaces, and value of information | Solver benchmark, infeasibility preservation, sensitivity/VOI tests | PROVEN — HiGHS LP compiler, post-solve constraint checks, no-relaxation failure results, 1D/2D reversal maps, EVPI, 24 focused tests, hand-checked optimum/infeasibility/EVPI receipt, and a fixed-seed 100-asset × 40-scenario local benchmark; internal synthetic method evidence only |
| A7 | ReplayStudio exports human- and machine-readable, evidence-labelled ReplayPacks | Recomputed output hashes, browser/CLI tests, accessible static report | PROVEN — deterministic compiler, atomic fixed-file export, semantic re-render verification, portable ZIP, CLI, 55 focused test cases, committed seven-engine/five-label static golden report, and recorded desktop/mobile browser checks; internal packaging evidence only, not the A8 engine-run flow or an external accessibility audit |
| A8 | All seven run in one deterministic end-to-end flow | Fresh-clone rebuild with cross-engine trace IDs and byte/semantic comparison | PROVEN — the committed SVB boundary pack runs all six analysis engines plus ReplayStudio from seven locked SEC records; a clean-worktree verifier reruns the flow twice, passes 12/12 assertions, byte-matches both directories and ZIPs, and preserves one cross-engine trace ID. This is internal integration evidence, not a complete SVB reconstruction or external validation |

## B. Official-data adapters — 20 points

Completion target: 20–30 counted adapters. The working target is 30. A catalog entry is not an
implemented adapter. An adapter counts only when it has:

- an official publisher URL and machine-readable redistribution rule;
- explicit authentication, rate-limit, availability-time, revision, and pagination behavior;
- a parser/schema test, corrupt/partial response test, and source-specific semantic validation;
- an opt-in live receipt containing response hash, retrieval time, record count, and source version;
- an honest failure path that never substitutes a current value for a missing historical vintage.

Current status: **PROVEN; 30/30 live-validated adapters.** Eight FDIC BankFind products, three SEC
EDGAR/XBRL products, five U.S. Treasury Fiscal Data tables, nine distinct New York Fed Markets API
products, one fixed BLS CPI-U product, and four independently classified CFTC COT products have
current schema-1.1 live receipts under `verification/live/`. The 23
FDIC/Treasury/New York Fed/BLS current-snapshot products deliberately remain `latest_only`; only the
three SEC accession/event products are currently historical-replay eligible. The four CFTC products
contain regulator-described immutable historical observations, but their generic API report dates do
not establish row-specific release times, so their receipts remain historical-replay ineligible.
`scripts/verify_live_receipts.py` recomputes every current receipt self-hash and raw-response hash and
reconciles counts before selecting one receipt per adapter. A count here proves connector behavior
and live retrieval, not historical-vintage depth, analytical correctness, or external validation.

## C. Thirty historical or boundary ReplayPacks — 20 points

Each of the 30 planned scenarios must independently provide:

1. regulator/official event timing evidence;
2. point-in-time availability rules;
3. immutable input manifest and download/rebuild route;
4. observed, reported, inferred, bounded, and simulated inputs in separate fields;
5. naive baseline and at least one deliberate failure mode;
6. a deterministic configuration and machine-readable result;
7. a limitations file and non-causal/non-deployment boundary;
8. a fresh-clone replay receipt.

Current status: **IN_PROGRESS; 1/30 replay-proven scenarios.** The SVB 2023 boundary replay is the
first counted scenario. `scripts/verify_scenario_catalog.py` opens its official SEC timing records,
immutable decision input lock, separately locked post-decision SEC event record, ReplayPack,
source-label map, explicit naive status-quo baseline, deliberate TrialCourt rejection, build and
verification routes, and clean-worktree double-rebuild receipt. It fails if post-decision event
evidence appears in the ReplayPack source manifest.
It then recomputes the deterministic inventory under `verification/scenarios/`. A scenario title,
plan row, unverified pack directory, or self-reported status still counts as 0.

## D. Scale and performance — 15 points

| ID | Requirement | Completion evidence | Current status |
|---|---|---|---|
| D1 | At least 1,000,000,000 distinct public-source records actually processed | Source-partition manifests with independent counts, hashes, no synthetic multiplication, and no double counting | NOT_STARTED |
| D2 | Billion-row point-in-time query demonstrated | Re-runnable benchmark receipt with hardware, cold/warm state, SQL, elapsed time, peak RSS and scanned bytes | NOT_STARTED |
| D3 | Scalable local/object-store layout | Partition-pruning and incremental-ingestion tests; interrupted-run recovery | NOT_STARTED |
| D4 | Scale claim is reproducible without shipping restricted raw data | Downloaders, source locks, sampled fixtures and manifest verifier | NOT_STARTED |

## E. Methods, security, reproducibility, and product — 15 points

Requirements include 100+ meaningful tests; 90%+ branch-aware coverage of the Python core; static
typing and lint; dependency and secret scanning; hostile archive/CSV/JSON limits; no-key demo;
fresh-archive reconstruction; fixed-version benchmarks; usable CLI/API; responsive, accessible,
read-only ReplayStudio; exportable reports; and visible simulation/evidence labels.

Current status: **IN_PROGRESS.** A test count alone cannot prove method correctness or product quality.

## F. Source and claim integrity — 5 points

Every public number must resolve to a machine result. Public data is not a client engagement,
historical replay is not live trading, shadow mode is not deployment, simulated P&L is not return,
and stars are not users or impact. Current status: **IN_PROGRESS.** Core contracts encode the first
boundaries; public-artifact scanning and claim traceability remain to be built.

## G. Independent evidence — 5 points

Completion requires a public read-only demo and at least one recorded independent reproduction or
domain-method review that identifies a real issue and follows it to resolution. Maintainer-written
feedback, automated tests, stars, traffic, or a friend saying “looks good” do not count.

Current status: **BLOCKED_EXTERNAL by definition until a real external reviewer participates.** This
does not stop implementation work and must never be filled with fabricated evidence.

## Scoring rule

The verifier may award only points whose evidence locators exist and pass. The project reaches
100/100 only when A–G are all proven. Internal completion before external review can score at most
95/100. The score is a repository-completeness measure, not an admissions or investment-quality score.
