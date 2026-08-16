# FinReplay OS

[![Verify](https://github.com/limingrui679-design/finreplay-os/actions/workflows/verify.yml/badge.svg)](https://github.com/limingrui679-design/finreplay-os/actions/workflows/verify.yml)
[![Security](https://github.com/limingrui679-design/finreplay-os/actions/workflows/security.yml/badge.svg)](https://github.com/limingrui679-design/finreplay-os/actions/workflows/security.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![License: Apache--2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Evidence site](https://img.shields.io/badge/evidence-live-20c997.svg)](https://finreplay-evidence.limingrui2.chatgpt.site/)

**Put the market back in time. Put the strategy on trial. Decide whether to allocate capital.**

FinReplay OS is an open-source, point-in-time financial-system replay toolkit. It separates what
was known at a decision time from what became known later, subjects research claims to adversarial
checks, and emits a portable ReplayPack whose inputs, reasoning artifacts, and render are bound by
deterministic hashes.

![FinReplay evidence explorer](web/public/og.png)

## Three-minute offline demo

The flagship demo runs from seven byte-locked official-source facts bundled in the package. It
makes no network request and creates a verified HTML/JSON/Markdown ReplayPack plus a deterministic
ZIP.

```bash
git clone https://github.com/limingrui679-design/finreplay-os.git
cd finreplay-os
python3.12 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/finreplay demo svb-2023 --offline --open
```

Expected terminal fields include `demo_complete=true`, `offline=true`, `engines=7`, the trace ID,
the pack SHA-256, and the local report path. To recheck the recorded identity without keeping an
output directory:

```bash
.venv/bin/finreplay scenario verify svb-2023
```

The command must reproduce pack hash
`c62c22dcbd15e29592a10811117a565d2bf9bee34877a4fbcbf24994383efd35`.

## What is implemented

| Surface | Current machine evidence |
|---|---|
| Seven connected engines | One deterministic SVB boundary flow executes all seven engines over seven locked facts and passes cross-engine assertions |
| Official-source validation | 30 live-validated formal adapters, each with a temporal-coverage label and receipt |
| Scenario runners | 30/30 internally replay-proven bounded scenarios with installable input locks |
| Scale path | 244 continuous SEC EDGAR daily partitions with 1,014,736,394 exact physical CSV rows in the committed scale manifest |
| Public demo and external review | Public demo achieved; independent review pending |

The [acceptance matrix](docs/verification/acceptance-matrix.md) defines each completion rule. The
[public claim registry](verification/claims/public-claims.json) binds headline counts to committed
machine evidence and scans public text for a bounded set of inflated claims.

## Seven connected engines

1. **TimeVault** stores bitemporal facts and answers “what was knowable then?”
2. **TrialCourt** records preregistration, all trials, leakage checks, and multiplicity attacks.
3. **MarketTwin** models evidence-graded institution, security, issuer, and fund relationships.
4. **ShockCompiler** expresses observed, bounded, counterfactual, and adversarial shocks.
5. **ExecutionLab** models cost, latency, queue, liquidity, capacity, and failure envelopes.
6. **CapitalAllocator** applies robust allocation constraints, reversal thresholds, and value of
   information.
7. **ReplayStudio** compiles and verifies human- and machine-readable ReplayPacks.

```mermaid
flowchart LR
    A["Official release or locked archive"] --> B["TimeVault point-in-time lock"]
    B --> C["Adversarial research engines"]
    C --> D["ReplayStudio"]
    D --> E["Verified ReplayPack"]
    B -. "temporally ineligible" .-> X["Fail closed"]
    C -. "unsupported claim" .-> X
```

## CLI map

```text
finreplay adapter list|show|fetch|validate
finreplay scenario list|show|run|verify
finreplay replaypack build|verify|open
finreplay evidence verify [--all-scenarios]
finreplay demo [SCENARIO] --offline [--open]
```

Examples:

```bash
# See the formal live-validation catalog and its temporal boundaries.
finreplay adapter list
finreplay adapter list --historical-only

# Inspect or run any packaged scenario without network access.
finreplay scenario show bls-cpi-2023
finreplay scenario run bls-cpi-2023 ./out/bls-cpi --archive ./out/bls-cpi.zip

# Validate catalogs quickly, or reproduce all 30 scenario hashes.
finreplay evidence verify
finreplay evidence verify --all-scenarios
```

`adapter fetch` currently provides a packaged generic parameter contract for
`fdic.bankfind.financials`; the remaining adapters expose source-specific Python APIs. This limit is
reported explicitly instead of pretending that incompatible upstream query models share one safe
generic parameter set.

## Formal adapters versus scenario connectors

The two surfaces are intentionally separate:

- The **formal live-adapter catalog** records 30 current live validations. Only 3 are presently
  marked historical-replay eligible; a successful current response does not backdate its values.
- The **offline scenario catalog** records 30 bounded research cases and bundles only the exact
  locked inputs used by their counted proofs. Some cases use scenario-specific archive connectors
  that are not silently added to the capped formal-adapter total.

See the [catalog and eligibility matrix](docs/catalog-matrix.md), [adapter authoring guide](docs/adapter-authoring.md),
and [scenario authoring guide](docs/scenario-authoring.md).

## Python API

```python
from pathlib import Path

from finreplay.catalog import find_scenario, load_adapter_catalog, run_scenario

entry = find_scenario("svb-2023")
result = run_scenario(
    entry.slug,
    Path("out/svb-2023"),
    archive=Path("out/svb-2023.zip"),
)
assert result.receipt.pack_sha256 == entry.pack_sha256

historical = [
    adapter.adapter_id
    for adapter in load_adapter_catalog().adapters
    if adapter.historical_replay_eligible
]
```

More runnable material is in [`examples/`](examples/) and the
[quickstart](docs/quickstart.md). Evidence labels are defined in
[`docs/evidence-labels.md`](docs/evidence-labels.md).

## Development

Python 3.11 and 3.12 are tested.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python scripts/build_user_catalogs.py --check
.venv/bin/ruff check .
.venv/bin/mypy src tests scripts
.venv/bin/pytest --cov=finreplay --cov-report=term-missing
```

The repository also checks the scenario catalog, independent-review record schema, dependency
changes, dependency vulnerabilities, CodeQL findings, and committed secrets. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the pull-request path and [`ROADMAP.md`](ROADMAP.md) for
bounded next steps.

## Independent review

Independent review is still pending. A qualified reviewer can use the
[review protocol](docs/verification/independent-review-protocol.md) and open a bounded
[independent-review report](https://github.com/limingrui679-design/finreplay-os/issues/new?template=independent-review.yml).
An issue, form submission, passing schema, or maintainer-run reproduction does not by itself close
that gate.

## Truth boundaries

- `observed`, `reported`, `extracted`, `inferred`, and `simulated` remain distinct labels.
- Economic time and knowledge/availability time are separate fields.
- A revised current value cannot silently replace a historical vintage.
- Public-data cases are not clients; historical replays are not live trading.
- Simulated P&L is not investment performance.
- Tests and hashes prove internal behavior, not source authenticity or real-world impact.
- Missing microstructure or exposure data produces bounds, not fabricated precision.
- The public deployment receipt is time-bounded availability evidence, not continuous uptime,
  adoption, or user-impact evidence.

## Research and investment disclaimer

FinReplay OS is research software. It does not provide investment, legal, accounting, or risk
management advice and does not connect to brokerage execution by default.

## License and citation

Code is released under [Apache-2.0](LICENSE). Dataset licenses and redistribution rules are
source-specific; a connector does not grant a right to redistribute upstream data. Cite the
software using [`CITATION.cff`](CITATION.cff).
