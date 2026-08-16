# Scenario authoring guide

A counted scenario is a bounded evidence exercise with a reproducible input lock, not a narrative
case study. It must be independently runnable from committed, redistribution-safe material.

## Scenario structure

Use a stable directory under `scenarios/<slug>/` containing `input-lock.json`. Implement one
exported loader and one exported spec builder in `finreplay.scenarios`, then add a thin build script
that imports that pair. The catalog generator discovers this explicit pair without executing the
script.

Each specification should record:

- stable scenario, version, and replay identities;
- a decision time and scenario mode;
- the exact recorded code commit;
- artifacts with hashes, temporal coverage, status, and evidence class;
- claims with support locators and explicit negative boundaries; and
- the engines and outputs needed for the bounded question.

## Eight evidence gates

The counted catalog verifier requires the repository-defined gates for source identity, temporal
eligibility, input locking, deterministic build, semantic verification, human-readable output,
machine-readable output, and fresh-process reproducibility. Consult the
[acceptance matrix](verification/acceptance-matrix.md) for the executable rule rather than copying
this summary into a new verifier.

## Add and verify a scenario

```bash
python scripts/build_<scenario>_replaypack.py \
  --input-lock scenarios/<slug>/input-lock.json \
  --output /tmp/<slug>
python scripts/verify_scenario_catalog.py
python scripts/build_user_catalogs.py --write
finreplay scenario verify <slug>
```

Commit the scenario proof, deterministic ReplayPack, rebuild receipt, documentation, and updated
catalog together. The generated package resource must remain byte-identical to the counted input
lock.

## Claim discipline

A scenario may show what the code produces under its explicit assumptions. It does not establish
source authenticity, general method correctness, forecast skill, investment performance, client
use, or real-world impact. Later observations used for evaluation must remain temporally separate
from decision-time inputs.
