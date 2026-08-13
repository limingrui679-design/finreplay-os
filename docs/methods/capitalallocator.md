# CapitalAllocator robust decision method

CapitalAllocator solves a continuous, long-only linear robust-allocation problem. It is a decision
model over declared inputs, not a portfolio recommendation or evidence of realized performance.
The implementation uses SciPy's HiGHS-backed `linprog`; a solution vector is read only when the
solver reports optimal status. Infeasible, unbounded, limited, exceptional, missing-vector, or
post-verification failures return no candidate weights and never trigger silent constraint
relaxation.

## Objective and variables

The decision variables are risky-asset weights, cash weight, absolute-turnover auxiliaries, and a
worst-scenario-loss epigraph. The minimized objective is the negative of:

```text
lower-bound expected return
- uncertainty aversion × weighted return-interval width
- loss aversion × worst declared scenario loss
- asset-level transaction costs on absolute weight changes
```

The model retains an expected-return upper bound for reporting but allocates against lower bounds.
Every return interval, loss vector, clock, evidence class, source record, derivation, capacity and
limitation remains in the hashed problem. Simulated inputs never receive fake source references.

## Preserved constraints

- risky weights plus cash equal one;
- asset minimum and maximum weights;
- capital-specific capacity, `capacity_usd / total_capital_usd`;
- minimum and maximum cash;
- one-way turnover, defined as half the sum of absolute risky and cash weight changes;
- optional maximum worst-case loss;
- auditable general linear lower and upper constraints.

Preflight catches clear bound, capacity and minimum-turnover contradictions. Conflicts that require
the full LP remain visible through HiGHS' infeasible status. Neither path emits fallback weights.
Every optimal vector is independently re-evaluated against the constraints before being accepted.

## Reversal surfaces

One- or two-dimensional finite grids can perturb a return bound, transaction cost, individual
scenario loss, cash return, loss aversion, or uncertainty aversion. Each coordinate rebuilds and
fully revalidates the problem before solving. The surface records every result hash, leading
allocation, number of decision regions, and adjacent leader reversals. It is a finite model map,
not a claim of continuous or causal economic thresholds.

## Value of perfect information

Discrete information states must cover every asset, sum to probability one, carry evidence labels,
and be available by the decision time. The no-information policy optimizes expected state returns;
the perfect-information policy re-optimizes conditionally in each state. EVPI is the
probability-weighted conditional utility minus the utility of the fixed no-information policy,
floored only within the numerical tolerance. It is an upper bound within the declared state model,
not the real price or impact of a data product.

## Verification evidence

`scripts/build_capitalallocator_benchmark.py` writes
`verification/evidence/capitalallocator-benchmark.json`. It contains:

- a two-asset hand-calculated optimum and numerical errors;
- a conflicting-constraint problem preserved as infeasible;
- a two-axis decision-reversal surface;
- a hand-calculated two-state EVPI example;
- a fixed-seed, 100-asset × 40-scenario local stress solve.

`semantic_sha256` excludes runtime metadata so repeated runs can compare model results despite
normal timing variation. `receipt_sha256` covers the complete receipt including hardware/runtime
metadata. `scripts/verify_capitalallocator_benchmark.py` recomputes both hashes and every required
semantic assertion. These are synthetic internal method tests, not external validation, live
capital, investment performance, or production scale evidence.
