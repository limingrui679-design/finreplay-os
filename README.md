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
| Seven connected engines | Five implemented individually; end-to-end connection is not yet proven | All seven execute in an end-to-end ReplayPack with tests |
| 20–30 official-data adapters | 16 live-validated: 8 FDIC, 3 SEC, 5 Treasury; temporal eligibility is recorded per source | Each counted adapter retrieves and validates an official source or fails honestly |
| 30 historical/boundary scenarios | Scenario specifications in progress | Each scenario passes evidence gates and produces a versioned ReplayPack |
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

## Research and investment disclaimer

FinReplay OS is research software. It does not provide investment, legal, accounting, or risk
management advice and does not connect to brokerage execution by default.

## License

Code is intended for release under Apache-2.0. Dataset licenses and redistribution rules are
source-specific and tracked separately; the presence of a connector never grants a right to
redistribute upstream data.
