# FinReplay OS

**Put the market back in time. Put the strategy on trial. Decide whether to allocate capital.**

FinReplay OS is a planned point-in-time financial-system digital twin and adversarial
quant research platform. It is being built to answer a stricter question than “did this
strategy backtest well?”:

> Using only information that was actually knowable at the decision time, does the claim
> survive leakage, multiple testing, regime change, execution costs, capacity limits,
> incomplete institutional exposures, and decision reversal analysis?

## Current status

**Pre-alpha implementation in progress.** This repository must not yet be described as a
finished platform, a production risk system, a billion-row deployment, or evidence of
investment performance, institutional adoption, external validation, or real-world impact.

| Target | Current evidence | Completion rule |
|---|---|---|
| Seven connected engines | Seven run in one deterministic SVB boundary flow over seven locked SEC facts; the committed pack and clean-worktree two-rebuild receipt pass 12 cross-engine assertions | All seven execute in an end-to-end ReplayPack with tests |
| 20–30 official-data adapters | 30 live-validated: 8 FDIC, 3 SEC, 5 Treasury, 9 New York Fed, 1 BLS, and 4 distinct CFTC COT products; temporal eligibility is recorded per source | Each counted adapter retrieves and validates an official source or fails honestly |
| 30 historical/boundary scenarios | 13/30 internally replay-proven: three bank boundaries plus ALFRED GDP revision, Federal Reserve H.4.1 BTFP growth, BLS payroll and CPI, FOMC target range, ALFRED Treasury-curve, Treasury DTS TGA, New York Fed SOFR, EIA commercial-crude-stock, and DOL initial-claims boundaries pass the eight-gate verifier | Each scenario passes evidence gates and produces a versioned ReplayPack |
| Billion-record scale | Not achieved | Machine manifest proves at least 1,000,000,000 distinct public records processed and queried |
| Public demo and external review | Not achieved | Public read-only deployment plus recorded independent reproduction/review |

The machine-auditable requirements are maintained in
[`docs/verification/acceptance-matrix.md`](docs/verification/acceptance-matrix.md).

## The seven engines

1. **TimeVault** — bitemporal storage and “what was knowable then?” queries.
2. **TrialCourt** — preregistration, complete experiment history, leakage and multiplicity attacks.
3. **MarketTwin** — evidence-graded bank–fund–security–issuer networks.
4. **ShockCompiler** — observed, bounded, counterfactual, and adversarial shock programs.
5. **ExecutionLab** — cost, latency, queue, liquidity, capacity, and failure envelopes.
6. **CapitalAllocator** — robust allocation, constraints, reversal thresholds, and value of information.
7. **ReplayStudio** — human-readable and machine-readable ReplayPack reports.

## Truth boundaries

- `observed`, `reported`, `extracted`, `inferred`, and `simulated` are never merged.
- Economic time and knowledge/availability time are separate fields.
- A current revised value cannot silently replace a historical vintage.
- Public-data cases are not clients; historical replays are not live trading.
- Simulated P&L is not investment performance.
- Tests and hashes prove internal behavior, not source authenticity or real-world impact.
- Missing market microstructure or exposure data produces bounds, not fabricated precision.

## Intended first vertical slice

The first release gate is an SVB 2023 point-in-time reconstruction using official SEC, FDIC,
ALFRED/FRED, and U.S. Treasury data. No second flagship slice counts as complete until a fresh
environment can reproduce the first one without leaking revised future values.

## Local development

The supported runtime is Python 3.11 or newer. The current pre-alpha verification loop is:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/mypy src tests
.venv/bin/pytest --cov=finreplay --cov-report=term-missing
```

Live SEC validation additionally requires an accountable `FINREPLAY_SEC_USER_AGENT` containing
a real contact email, as required by SEC fair-access guidance. The value is sent only as an HTTP
header and is not persisted in receipts. Local raw responses and DuckDB files are gitignored;
portable content hashes and bounded evidence receipts are committed under `verification/live/`.
The latest one-per-adapter evidence inventory can be rebuilt with
`python scripts/verify_live_receipts.py`; legacy schema-1.0 receipts remain historical Git evidence
but are excluded from current adapter counts.

The nine New York Fed products can be revalidated with
`python scripts/validate_nyfed_catalog.py`. They are deliberately `latest_only`: event, as-of,
release, and `lastUpdated` fields remain in the payload but do not backdate the exact retrieved
value. Source-content reuse remains subject to the current New York Fed Terms of Use, including
the additional reference-rate attribution and non-endorsement conditions.

The fixed BLS CPI-U product and four independently classified CFTC COT products can be
revalidated with `python scripts/validate_bls.py` and
`python scripts/validate_cftc_catalog.py`. BLS annual-average `M13` rows remain in the hashed raw
response but are excluded from the monthly normalized stream. CFTC historical rows are immutable
published observations, yet the generic API receipts remain ineligible for historical
decision-time use until a row-specific release timestamp is independently bound.

The GDP revision scenario uses `fred.alfred.vintage_gdp` as a separately verified supporting
source. It retrieves four explicitly named ALFRED snapshots, retains raw CSV only in local ignored
storage, and applies a two-calendar-day conservative knowledge bound because a vintage date does
not establish an intraday release timestamp. It is deliberately absent from the formal adapter
inventory, so the capped target remains exactly 30 rather than silently becoming 31.

The BTFP growth scenario likewise uses
`federal_reserve.h41.btfp_historical_release` as a separately verified supporting source. It
retrieves only three explicitly dated archived H.4.1 pages, parses the BTFP Table 1 row, retains
full HTML locally, and applies the same conservative date-plus-two-day rule. It also remains outside
the formal 30-adapter inventory.

The payroll-release scenario uses `bls.employment_situation.archived_release` as a third verified
supporting source. It retrieves only the January 6, February 3, and March 10, 2023 BLS archive
pages, strictly parses the report-period headline and stated 8:30 a.m. Eastern embargo end, and
keeps each headline value as a versioned release-snapshot fact rather than replacing it with later
revisions. The January release's annual benchmark and seasonal-factor changes remain an explicit
comparability limitation. This source is also outside the formal 30-adapter inventory.

The target-range scenario uses `federal_reserve.fomc.archived_statement` as another verified
supporting source. It retrieves only the February 1, March 22, and May 3, 2023 statement pages,
strictly parses their target endpoints, and validates each page's 2:00 p.m. EST/EDT release label
against `America/New_York` before UTC conversion. Full HTML remains local; this source is also
outside the formal 30-adapter inventory.

The CPI-release scenario uses `bls.cpi.archived_release` as another verified supporting source.
It retrieves only the January 12, February 14, and March 14, 2023 archive pages, strictly parses
their CPI-U all-items headlines, and validates the 8:30 a.m. Eastern embargo end with
`America/New_York`. Each value remains tied to its archived release snapshot. The February page's
annual weight update and five-year seasonal recalculation are an explicit comparability limit, so
the resulting interval is a release-snapshot stress range rather than a calibrated forecast. Full
HTML remains local and this source is outside the formal 30-adapter inventory.

The Treasury-curve scenario uses `fred.alfred.vintage_treasury_yield` as another supporting
source. It retrieves only six DGS2/DGS10 observations across three explicitly named ALFRED
vintages, converts reported percent yields exactly to integer basis points, and applies the same
conservative date-plus-two-day knowledge bound used by the GDP connector. The derived
10-year-minus-2-year spread is not represented as upstream reported data. Raw CSV stays in ignored
download-only storage and this source is outside the formal 30-adapter inventory.

The TGA cash-boundary scenario uses `treasury.dts.published_report` as another supporting source.
It retrieves only the May 31, June 1, and June 2, 2023 date-stamped Daily Treasury Statement PDFs,
strictly parses and arithmetically reconciles Table I, and uses Treasury's following-business-day
4:00 p.m. publication deadline as the conservative knowledge time. The deadline is not represented
as the exact publication instant. Full PDFs stay in ignored download-only storage and this source
also remains outside the formal 30-adapter inventory.

The counted SOFR boundary uses `nyfed.sofr.final_historical_rate` as another supporting source. It
retrieves only the September 13, 16, and 17, 2019 effective dates from the official New York Fed
Markets API, normalizes each final percentage to exact integer basis points, and permits use only
at 3:00 p.m. New York time on the following publication business day—after the stated same-day
revision window. Ancillary percentiles are validated but excluded from normalized historical facts
because lagged summary statistics can change. Raw JSON stays local and this source remains outside
the formal 30-adapter inventory.

The counted commercial-crude-stock boundary uses
`eia.wpsr.archived_commercial_crude_stocks` as another supporting source. It pairs exact Table 4
CSV values with the full archived WPSR PDF for April 8, 15, and 22, 2020. Each pair must agree on
release identity and rounded values, and the CSV stock arithmetic must reconcile exactly. Because
the archived release text says tables are posted “after 10:30 a.m.” rather than proving an exact
instant, the source becomes eligible only at the following local midnight in
`America/New_York`. Raw CSV/PDF bytes stay local and this source remains outside the formal
30-adapter inventory.

The counted initial-claims boundary uses `dol.eta.archived_weekly_initial_claims` as another
supporting source. It retrieves only the March 12, 19, and 26, 2020 DOL weekly-claims PDFs,
validates each nine-page report, exact 8:30 a.m. Eastern embargo end, headline arithmetic, USDL
release number, and official `Last-Modified` timestamp. The March 19 annual seasonal-factor
revision remains an explicit comparability boundary, and the March 26 revision of the prior week
is preserved only in that later release snapshot. Raw PDFs stay local and this source remains
outside the formal 30-adapter inventory.

The planned 91-day Treasury-bill auction boundary uses
`treasury.auctions.archived_91_day_bill_results` as another supporting source. It retrieves paired
TreasuryDirect result XML and one-page PDF files for March 9, 16, and 23, 2020, and cross-checks
CUSIP, dates, rates, price, tender amounts, bidder totals, bid-to-cover arithmetic, and result
filename. The XML carries the official release time under Treasury's documented auction process;
FinReplay nevertheless waits until the following New York midnight before use. Migrated current
`Last-Modified` headers are not backdated. Raw pairs stay local and the source remains outside the
formal 30-adapter inventory.

ReplayStudio accepts a typed JSON specification and emits a deterministic static report directory:

```bash
finreplay build-replaypack spec.json output/replay --archive output/replay.zip
finreplay verify-replaypack output/replay
python scripts/verify_replaystudio_golden.py
```

The committed golden pack proves packaging and rendering over labelled fixtures only; it is not the
SVB end-to-end replay and does not count toward the 30 historical scenarios.

The first actual integration flow can be rebuilt with:

```bash
python scripts/build_svb_replaypack.py
python scripts/verify_svb_replaypack.py
python scripts/verify_scenario_catalog.py
```

It deliberately excludes current FDIC/Treasury snapshots from the 2023 decision input, rejects a
retrospective TrialCourt attempt, and labels the no-microstructure execution/allocation boundary as
simulated. Its post-decision SEC event lock is verified separately and cannot appear in the pack's
decision-input manifest. The committed evidence proves internal deterministic integration, not
historical completeness, method correctness, deployment, or external validation. See
[`docs/scenarios/svb-2023.md`](docs/scenarios/svb-2023.md).

The second counted flow locks seven PacWest Bancorp facts accepted on 2023-02-27, sets a
2023-05-03 20:00 UTC decision boundary, and separately locks the post-decision 2023-05-04 SEC 8-K
event. It uses the reusable bank-boundary builder and verifier while preserving scenario-specific
identities, concepts, values, hashes, and claims. See
[`docs/scenarios/pacwest-2023.md`](docs/scenarios/pacwest-2023.md).

The third counted flow sets a 2023-05-02 16:00 UTC Western Alliance decision boundary before the
same-day 17:08:31 UTC EDGAR acceptance of its post-decision 8-K. It is the final planned use of the
current regional-bank filing template before scenario work moves to different mechanisms and
official data families. See
[`docs/scenarios/western-alliance-2023.md`](docs/scenarios/western-alliance-2023.md).

The fourth counted flow changes both mechanism and source family. It sets a 2023-02-01 decision
boundary over four native-vintage ALFRED GDP facts, derives a no-probability Q4 revision envelope
from the already known Q3 revision path, and keeps the February 23 Q4 second estimate in a disjoint
post-decision event lock. Only TimeVault, ShockCompiler, TrialCourt, and ReplayStudio run because
the scenario makes no market-network, execution, or allocation claim. See
[`docs/scenarios/gdp-revision-2022q4.md`](docs/scenarios/gdp-revision-2022q4.md).

The fifth counted flow changes publisher and mechanism again. It locks the March 16 and March 23,
2023 Federal Reserve H.4.1 BTFP balances before a March 25 decision boundary, constructs a
no-probability next-week growth interval from the one already known Wednesday change, and keeps the
March 30 balance in a disjoint event lock. See
[`docs/scenarios/btfp-growth-2023.md`](docs/scenarios/btfp-growth-2023.md).

The sixth counted flow uses archived BLS Employment Situation releases with exact embargo-end
timing. It locks December and January payroll changes and unemployment rates before a February 4
decision boundary, uses the two already-known payroll headlines only as a no-probability stress
range, and keeps the March 10 February payroll headline in a disjoint event lock. The annual
benchmarking caveat prevents a stationary-sample claim. See
[`docs/scenarios/bls-payroll-2023.md`](docs/scenarios/bls-payroll-2023.md).

The seventh counted flow uses archived FOMC statements with exact page-stated release timing. It
locks the February and March federal-funds target endpoints before a March 23 decision boundary,
uses persistence or one repeat of the already-known 25-basis-point step as a no-probability
next-upper-target range, and keeps the May 3 upper target in a disjoint event lock. See
[`docs/scenarios/fomc-target-2023.md`](docs/scenarios/fomc-target-2023.md).

The eighth counted flow uses archived BLS CPI releases with exact embargo-end timing. It locks
December and January monthly and 12-month changes before a February 15 decision boundary, uses the
two already-known monthly changes only as a no-probability release-snapshot stress range, and keeps
the March 14 February monthly change in a disjoint event lock. The documented annual weight update
and five-year seasonal recalculation prevent a stationary-sample claim. See
[`docs/scenarios/bls-cpi-2023.md`](docs/scenarios/bls-cpi-2023.md).

The ninth counted flow changes mechanism to the U.S. Treasury curve. It locks DGS2 and DGS10 on
March 8 and March 13 using native ALFRED vintages and a conservative date-plus-two-day knowledge
rule, then bounds the next DGS10-minus-DGS2 spread at `[-107, -48]` basis points with no
probability. The disjoint March 15 pair yields `-42`, a required visible 6-basis-point breach rather
than a retrospectively widened success. See
[`docs/scenarios/treasury-curve-2023.md`](docs/scenarios/treasury-curve-2023.md).

The tenth counted flow uses date-stamped Daily Treasury Statement PDFs rather than a market or
release headline. It locks the May 31 and June 1 TGA closing balances, uses the known values
`22,892` and `48,512` million dollars as no-probability endpoints, and keeps the June 2 balance of
`23,368` million dollars in a disjoint later event lock. The event lies inside the declared range
but differs from the latest-balance persistence baseline by `476` million dollars. See
[`docs/scenarios/treasury-tga-2023.md`](docs/scenarios/treasury-tga-2023.md).

The eleventh counted flow uses final historical New York Fed SOFR rows. It locks the September 13
and 16 rates of `220` and `243` basis points before a September 17 decision boundary and keeps the
September 17 rate of `525` basis points in a disjoint event lock that becomes final only after the
following business day's revision window. The event is `282` basis points above the declared
upper endpoint. The verifier requires that breach to remain visible and does not widen the
no-probability range after observing it. See
[`docs/scenarios/nyfed-sofr-2019.md`](docs/scenarios/nyfed-sofr-2019.md).

The twelfth counted flow uses paired archived EIA Weekly Petroleum Status Report CSV and PDF
releases. It locks commercial crude stocks excluding SPR of `484,370` and `503,618` thousand
barrels before an April 16, 2020 decision boundary, using next-local-midnight eligibility because
the official schedule says only “after 10:30 a.m.” Eastern. The separately locked April 17 stock
is `518,640` thousand barrels, a required visible `15,022`-thousand-barrel breach above the
no-probability range. See
[`docs/scenarios/eia-wpsr-2020.md`](docs/scenarios/eia-wpsr-2020.md).

The thirteenth counted flow uses archived DOL Unemployment Insurance Weekly Claims PDFs. It locks
advance seasonally adjusted initial claims of `211,000` and `281,000` persons before a March 20,
2020 decision boundary, then constructs only persistence or one repeat of the known
`70,000`-person increase: `[281,000, 351,000]`, with no probability. The separately locked March
21 value is `3,283,000`, a required visible `2,932,000`-person breach. Its later revision of the
prior week from `281,000` to `282,000` remains in the event snapshot and never overwrites the
decision input. See
[`docs/scenarios/dol-ui-2020.md`](docs/scenarios/dol-ui-2020.md).

## Research and investment disclaimer

FinReplay OS is research software. It does not provide investment, legal, accounting, or risk
management advice and does not connect to brokerage execution by default.

## License

Code is intended for release under Apache-2.0. Dataset licenses and redistribution rules are
source-specific and tracked separately; the presence of a connector never grants a right to
redistribute upstream data.
