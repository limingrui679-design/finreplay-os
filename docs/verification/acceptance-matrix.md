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

Current status: **PROVEN; 30/30 replay-proven scenarios.** The SVB, PacWest, Western Alliance,
2022 Q4 GDP revision, March 2023 BTFP early-growth, early-2023 BLS payroll and CPI release,
March 2020 BLS PPI final-demand monthly-change,
spring-2023 FOMC target range, March 2023 Treasury-curve, and June 2023 Treasury TGA cash-boundary
replays, plus the September 2019 New York Fed SOFR spike, April 2020 EIA commercial-crude-stock,
March 2020 EIA working-gas-stock, March 2020 DOL initial-claims, March 2020 Treasury 91-day-bill
auction-rate, March 2020 BEA
personal-saving-rate, March 2020 Federal Reserve G.17 industrial-production, March 2020 Census
MARTS retail-sales, March 2020 Census/HUD NRC housing-starts, March 2020 Federal Reserve G.19
revolving-credit, March 2020 Census C30 construction-spending, March 2020 FHFA purchase-only
House Price Index monthly-change, March 2020 Census M3 durable-goods new-orders, and March 2020
joint Census/BEA FT-900 goods-and-services-deficit, March 2020 Census/HUD NRS new-home-sales
level, July 2026 CFTC TFF UST 2-year open-interest, and March 2020 Federal Reserve H.4.1
central-bank-liquidity-swap balance, plus February 2020 BLS all-import and all-export
monthly-change boundaries are counted.
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
The WNGSR case recovers original Lower 48 stock estimates from EIA's revision-safe workbook,
cross-checks them against the current history and the 2020–2022 performance evaluation, and uses
exact 10:30 a.m. Eastern release timing. Its range repeats at most the single known 9 Bcf decline,
assigns no probability, and does not use EIA coefficients of variation or weekly-net-change
standard errors as endpoints. The separately locked March 20 event remains a visible 20 Bcf miss
below the fixed range, while rounded regional differences are retained rather than forcibly
reconciled. The PPI case validates three complete archived HTML/PDF pairs and their exact 8:30
a.m. EDT embargo boundaries. Its range persists the March final-demand monthly change or repeats
the one known 40-basis-point February-to-March increase, assigns no probability, and keeps
unadjusted indexes, 12-month changes, and COVID-19 methodology language outside endpoint
construction. The separately locked April change remains a visible 110-basis-point miss below
the fixed range and does not retroactively widen it. PPI remains an aggregate seller-price
measure subject to revision, not CPI, producer-level behavior, or causal evidence. All
twenty-seven non-bank cases use only four relevant engines and labelled post-event
checks.
The CFTC TFF case cross-checks three exact API rows against the annual Futures Only file and binds
the release schedule, policy page, and TFF notes PDF. Its July 21 persistence-or-one-known-decline
range assigns no probability and retains the later July 28 level as a visible 71,513-contract
upper-bound breach without widening the range. Because the schedule is tentative and no row-level
actual-publication log exists, timing remains explicitly scheduled at `0.98` confidence rather
than being represented as an exact actual timestamp. Category positions, trader counts, and the
face-value label set no endpoint and establish no direction, intent, notional, P&L, or causality.
The H.4.1 liquidity-swap case validates three complete archived HTML/ASCII release pairs and binds
the four exact decision-input response hashes plus the idempotent six-response supporting receipt.
Its range persists the March 25 Wednesday aggregate balance or repeats the one known March 18-to-25
increase, assigns no probability, and uses neither weekly averages nor year-ago changes as
endpoints. The separately locked April 1 balance lies inside the fixed range, but the verifier
requires that result to remain labelled as evaluation only and never as forecast success. The
April 2 release pair gives no exact time, so eligibility waits until the following New York
midnight. The source's exchange-rate measurement convention is not represented as current-market
exposure, transaction behavior, P&L, policy effectiveness, or causality.
The BLS import-price case validates three complete archived HTML/PDF release pairs and exact 8:30
a.m. EST/EDT embargo timing. Its range uses only the January and February all-import first reports,
persists the February `-50`-basis-point change or repeats the one known `50`-basis-point decline,
and assigns no probability. The later January `+10`-basis-point and February `-20`-basis-point
revisions remain explicit lineage but set no endpoint. The separately locked March first report is
`-230` basis points, a visible `130`-basis-point breach below the fixed lower endpoint; the verifier
requires the miss to remain visible, forbids an after-the-fact range change, and records no forecast
success. The non-seasonally-adjusted modified-Laspeyres importer-price index is not an import
quantity, nominal trade value, tariff, CPI, firm result, P&L, or causal estimate. The release's
COVID-19 statement remains methodology and response-rate context, not pandemic causality or proof
of unaffected measurement.
The BLS export-price case uses an independent Table 2 entity, metric, payload hash, and revision
chain from the import-price case while binding the same complete archived release pairs. Its range
uses only the January `+70`-basis-point and February `-110`-basis-point all-export first reports,
persists February or repeats the one known `180`-basis-point decline, and assigns no probability.
The later `-10`-basis-point January revision remains explicit lineage but sets no endpoint. The
separately locked March first report is `-160` basis points, inside the fixed `[-290, -110]` range,
`130` basis points above the lower endpoint and `50` below the upper. The verifier labels that
inclusion as post-event evaluation, records `forecast_success_claimed=false`, and forbids an
after-the-fact range change. The non-seasonally-adjusted modified-Laspeyres export-price index is
not export quantity, nominal export value, a tariff, PPI, firm result, P&L, or causal estimate.
Any future scenarios must continue to diversify
mechanisms and source families. The verifier recomputes the deterministic inventory under
`verification/scenarios/`. A scenario title, plan row, unverified pack directory, or self-reported
status still counts as 0.

## D. Scale and performance — 15 points

| ID | Requirement | Completion evidence | Current status |
|---|---|---|---|
| D1 | At least 1,000,000,000 distinct public-source records actually processed | Source-partition manifests with independent counts, hashes, no synthetic multiplication, and no double counting | IN_PROGRESS — exact physical-row SEC partitions and a self-hashed uniqueness manifest exist, but only `target_met=true` in the deeply reverified manifest completes this gate |
| D2 | Billion-row point-in-time query demonstrated | Re-runnable benchmark receipt with hardware, cold/warm state, SQL, elapsed time, peak RSS and scanned bytes | IN_PROGRESS — two fresh-process smoke receipts record logical SQL, input hashes/rows/bytes, elapsed time, peak RSS and explicitly uncontrolled OS cache; this is not yet a billion-row benchmark |
| D3 | Scalable local/object-store layout | Partition-pruning and incremental-ingestion tests; interrupted-run recovery | IN_PROGRESS — daily raw/Parquet partitions, atomic writes, bounded four-worker ingestion, ETag/Last-Modified range resume and an actually resumed smoke download are proven; billion-scale completion remains open |
| D4 | Scale claim is reproducible without shipping restricted raw data | Downloaders, source locks, sampled fixtures and manifest verifier | IN_PROGRESS — official annual inventory locks, accountable download receipts, hostile ZIP/CSV tests, partition verifiers and a cross-partition deep verifier are committed; final target-scale re-download/rebuild evidence remains open |

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
