# FinReplay OS completion and evidence matrix

This matrix defines “done.” A passing unit test, README claim, schema file, source URL, generated
row count, or self-assigned score cannot prove a broader requirement than it actually exercises.

Status vocabulary: `NOT_STARTED`, `IN_PROGRESS`, `PROVEN`, `BLOCKED_EXTERNAL`.

## A. Seven connected engines — 20 points

| ID | Requirement | Required authoritative evidence | Current status |
|---|---|---|---|
| A1 | TimeVault supports bitemporal append, revision history, and as-of knowledge queries | Unit/property tests plus a real-source golden replay proving future revisions are excluded | IN_PROGRESS |
| A2 | TrialCourt records preregistration, every attempt, leakage/multiplicity/regime/execution attacks, and disposition | Immutable trial ledger, method tests against published examples, negative-result fixture | NOT_STARTED |
| A3 | MarketTwin stores evidence-graded temporal institution/security graphs and bounded contagion | Graph contract tests plus at least one live official multi-source graph | NOT_STARTED |
| A4 | ShockCompiler distinguishes observed reconstruction, bounds, counterfactual, and adversarial perturbations | Scenario compiler tests and machine-readable provenance for every shock | NOT_STARTED |
| A5 | ExecutionLab models non-zero costs, latency/capacity and data-dependent precision tiers | Golden microstructure replay and conservative fallback tests | NOT_STARTED |
| A6 | CapitalAllocator supports constraints, robust solutions, reversal surfaces, and value of information | Solver benchmark, infeasibility preservation, sensitivity/VOI tests | NOT_STARTED |
| A7 | ReplayStudio exports human- and machine-readable, evidence-labelled ReplayPacks | Recomputed output hashes, browser/CLI tests, accessible static report | NOT_STARTED |
| A8 | All seven run in one deterministic end-to-end flow | Fresh-clone rebuild with cross-engine trace IDs and byte/semantic comparison | NOT_STARTED |

## B. Official-data adapters — 20 points

Completion target: 20–30 counted adapters. The working target is 30. A catalog entry is not an
implemented adapter. An adapter counts only when it has:

- an official publisher URL and machine-readable redistribution rule;
- explicit authentication, rate-limit, availability-time, revision, and pagination behavior;
- a parser/schema test, corrupt/partial response test, and source-specific semantic validation;
- an opt-in live receipt containing response hash, retrieval time, record count, and source version;
- an honest failure path that never substitutes a current value for a missing historical vintage.

Current status: **IN_PROGRESS; 16/30 live-validated adapters.** Eight FDIC BankFind
products, three SEC EDGAR/XBRL products, and five U.S. Treasury Fiscal Data tables have current
schema-1.1 live receipts under `verification/live/`. Of these, the 13 FDIC/Treasury current-table
products deliberately remain `latest_only`; only the three SEC accession/event products are
currently historical-replay eligible. `scripts/verify_live_receipts.py` recomputes every current
receipt self-hash and raw-response hash and reconciles counts before selecting one receipt per
adapter. A count here proves connector behavior and live retrieval, not historical-vintage depth.

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

Current status: **NOT_STARTED; 0/30 replay-proven scenarios.** A scenario title in a plan is 0.

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
