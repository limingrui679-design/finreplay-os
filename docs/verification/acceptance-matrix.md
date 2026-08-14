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

Current status: **IN_PROGRESS; 24/30 replay-proven scenarios.** The SVB, PacWest, Western Alliance,
2022 Q4 GDP revision, March 2023 BTFP early-growth, early-2023 BLS payroll and CPI release,
spring-2023 FOMC target range, March 2023 Treasury-curve, and June 2023 Treasury TGA cash-boundary
replays, plus the September 2019 New York Fed SOFR spike, April 2020 EIA commercial-crude-stock,
March 2020 DOL initial-claims, March 2020 Treasury 91-day-bill auction-rate, March 2020 BEA
personal-saving-rate, March 2020 Federal Reserve G.17 industrial-production, March 2020 Census
MARTS retail-sales, March 2020 Census/HUD NRC housing-starts, March 2020 Federal Reserve G.19
revolving-credit, March 2020 Census C30 construction-spending, March 2020 FHFA purchase-only
House Price Index monthly-change, March 2020 Census M3 durable-goods new-orders, and March 2020
joint Census/BEA FT-900 goods-and-services-deficit, and March 2020 Census/HUD NRS new-home-sales
level boundaries are counted.
`scripts/verify_scenario_catalog.py` opens
each scenario's official timing records, immutable decision input lock, separately locked
post-decision official event record, ReplayPack, source-label map, explicit naive baseline,
deliberate TrialCourt rejection, build and verification routes, and clean-worktree double-rebuild
receipt. It fails if post-decision event evidence appears in a ReplayPack source manifest. The GDP
case is the first non-bank template: it uses four native ALFRED vintages and a symmetric
no-probability revision envelope. The BTFP case adds date-stamped Federal Reserve H.4.1 archives and
a one-sided no-growth-to-prior-growth continuation envelope. Both use conservative date-granular
knowledge bounds. The BLS case adds an exact page-stated 8:30 a.m. Eastern embargo boundary, a
two-headline no-probability payroll range, and an annual-benchmark comparability limitation. The
FOMC case adds exact EST/EDT policy-
release timing and a zero-or-one-known-step next-upper-target boundary, without market-expectation,
causal-effect, or policy-correctness claims. The CPI case adds exact winter/daylight embargo timing,
release-snapshot preservation across a documented annual seasonal recalculation, and a two-point
monthly-change stress range. The Treasury-curve case derives DGS10-minus-DGS2 from four reported
native-vintage yields and requires the later 6-basis-point range breach to remain visible rather
than becoming a retroactive success. The TGA case arithmetically verifies two date-stamped Daily
Treasury Statement balances, applies Treasury's following-business-day deadline, and keeps the
later inside-range event separate from the persistence baseline. The SOFR case verifies two final
historical reference rates with a conservative post-revision-window knowledge boundary and
preserves the later 282-basis-point miss above the declared range. The EIA case cross-validates
exact archived CSV values against paired full-report PDFs, uses a conservative
next-local-midnight knowledge boundary, and preserves the later
15,022-thousand-barrel miss. The DOL case adds exact embargo timing, annual seasonal-factor
comparability warnings, snapshot-preserving prior-week revisions, and a required visible
2,932,000-person miss above its persistence-or-one-known-increase range. The Treasury auction case
cross-validates paired XML/PDF results and preserves the later zero-rate result as a 19-basis-point
miss below its persistence-or-one-known-decline range. The BEA case cross-validates paired
HTML/PDF release snapshots, preserves the later February revision without overwriting its earlier
snapshot, and keeps the reported March rate's 460-basis-point miss above the declared range
visible. The G.17 case preserves paired archived release facts, exact 9:15 a.m. Eastern timing,
the April revision of February as a later-only snapshot, and the reported March change's
600-basis-point miss below the declared range. The Census MARTS case cross-validates paired
archived PDF/XLS snapshots, exact 8:30 a.m. Eastern timing, and the later February revision while
keeping Census's official 90-percent sampling margins separate from FinReplay's no-probability
stress range; the reported March change remains a visible 740-basis-point miss. The Census/HUD NRC
case validates three complete seven-page archived PDFs and exact 8:30 a.m. Eastern timing. Its
range uses only the two release-time preliminary headline levels—not official monthly changes
against revised priors or official sampling-confidence intervals—and preserves the later February
revision in the event snapshot; the reported March level remains a visible 383,000-unit miss. The
G.19 case validates three complete four-page rotated archived PDFs and exact 3:00 p.m.
Eastern timing, retains the table's one-decimal simple annual rates rather than rounded headline
fractions, preserves May's January and February revisions only in the event snapshot, and keeps
the reported March change's 3,550-basis-point miss below the declared range visible. The Census
C30 case validates three complete six-page PDF/XLSX pairs and exact 10:00 a.m. Eastern timing,
preserves each initial monthly level instead of silently substituting later revisions, and keeps
the two-initial-level stress step distinct from Census's official monthly change against a revised
prior. Its reported March level remains a visible 3,659-million-dollar miss below the fixed range,
while official 90-percent sampling intervals remain source facts rather than FinReplay range
inputs. The FHFA HPI case validates the preannounced 9 a.m. ET calendar and exact archived report
PDFs, preserves the January footer's `9AM EST` wording discrepancy, binds stable schedule semantics
without treating today's HTML wrapper as an immutable 2019 snapshot, and retains the May report's
January and February values only as a later event snapshot. Its reported March national change
remains a visible 60-basis-point miss below the fixed range. The currently served event PDF's June
15 modification metadata is explicit rather than misrepresented as unchanged since May 26. All
three selected M3 PDFs pass complete seven-page validation, exact 8:30 a.m. Eastern timing, and
Table 1/Table 2 cross-checks. The M3 case preserves first-report January and February changes,
keeps the April report's revisions only in the event snapshot, and retains the reported March
change as a visible 1,560-basis-point miss below the fixed range. It assigns no confidence interval
because M3 is not a probability sample, distinguishes seasonal adjustment from price adjustment,
and does not backdate current modified PDF bytes. The FT-900 case validates three joint release
PDFs against their three 31-member XLS ZIPs and exact 8:30 a.m. Eastern timing. Its range uses the
revised January and initial February deficit levels co-published in the April 2 decision snapshot,
not the stale January initial value from the earlier release. It retains the May release's
February revision only in the event snapshot and preserves the reported March deficit's visible
4,483-million-dollar miss above the fixed range. Goods-document enumeration is not represented as
complete economic measurement, and seasonally adjusted nominal dollars are not price-adjusted
trade volume. The NRS case validates three complete five-page PDFs and exact 10:00 a.m. Eastern
timing.
Its range uses revised January and initial February SAAR values co-published in the March 24
decision snapshot, not January's stale initial value or Census/HUD's official 90-percent sampling
interval. It retains April's February revision only in the event snapshot and preserves the
reported March rate's visible 103,000-unit SAAR miss below the fixed range. A source-defined sale
may precede permit issuance and is not represented as a closing or actual monthly transaction.
All twenty-one non-bank cases use only four relevant engines and labelled post-event checks.
Further scenarios must continue to diversify
mechanisms and source families. The verifier recomputes the deterministic inventory under
`verification/scenarios/`. A scenario title, plan row, unverified pack directory, or self-reported
status still counts as 0.

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
