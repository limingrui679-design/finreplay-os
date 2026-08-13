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
| 30 historical/boundary scenarios | 4/30 internally replay-proven: three 2023 bank boundaries plus a distinct ALFRED GDP revision-vintage boundary pass the eight-gate verifier | Each scenario passes evidence gates and produces a versioned ReplayPack |
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

## Research and investment disclaimer

FinReplay OS is research software. It does not provide investment, legal, accounting, or risk
management advice and does not connect to brokerage execution by default.

## License

Code is intended for release under Apache-2.0. Dataset licenses and redistribution rules are
source-specific and tracked separately; the presence of a connector never grants a right to
redistribute upstream data.
