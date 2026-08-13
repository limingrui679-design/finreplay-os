#!/usr/bin/env python3
"""Build hand-checked and fixed-size CapitalAllocator benchmark evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import scipy

from finreplay.contracts import EvidenceClass
from finreplay.engines import (
    AllocationProblem,
    AllocationStatus,
    AssetCandidate,
    CapitalAllocator,
    InformationState,
    LinearAllocationConstraint,
    RiskScenario,
    SensitivityAxis,
    SensitivityKind,
)

DECISION = datetime(2023, 3, 8, 18, tzinfo=UTC)
TOLERANCE = 1e-8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/evidence/capitalallocator-benchmark.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    allocator = CapitalAllocator()
    golden_problem = _golden_problem()
    golden = allocator.solve(golden_problem)
    expected = {
        "weight_asset_a": 0.7,
        "weight_asset_b": 0.3,
        "cash_weight": 0.0,
        "expected_return_lower": 0.088,
        "transaction_cost_fraction": 0.001,
        "robust_utility": 0.087,
    }
    actual = {
        "weight_asset_a": golden.weights["asset:a"],
        "weight_asset_b": golden.weights["asset:b"],
        "cash_weight": golden.cash_weight,
        "expected_return_lower": golden.expected_return_lower,
        "transaction_cost_fraction": golden.transaction_cost_fraction,
        "robust_utility": golden.robust_utility,
    }
    absolute_error = {
        key: abs(float(actual[key]) - value) for key, value in expected.items()
    }
    infeasible = allocator.solve(_infeasible_problem())
    return_axis = SensitivityAxis(
        axis_id="axis:a-return",
        kind=SensitivityKind.EXPECTED_RETURN_LOWER,
        asset_id="asset:a",
        values=(0.04, 0.08, 0.12),
    )
    loss_axis = SensitivityAxis(
        axis_id="axis:a-loss",
        kind=SensitivityKind.SCENARIO_LOSS,
        asset_id="asset:a",
        scenario_id="scenario:base",
        values=(0.0, 0.2),
    )
    surface = allocator.reversal_surface(_surface_problem(), (return_axis, loss_axis))
    information = allocator.value_of_perfect_information(
        _information_problem(),
        (
            _information_state(
                "state:good-a", 0.5, {"asset:a": 0.2, "asset:b": 0.0}
            ),
            _information_state(
                "state:good-b", 0.5, {"asset:a": 0.0, "asset:b": 0.2}
            ),
        ),
    )
    stress_problem = _stress_problem(asset_count=100, scenario_count=40)
    started = time.perf_counter()
    stress = allocator.solve(stress_problem)
    elapsed = time.perf_counter() - started

    assertions = {
        "golden_optimal": golden.status is AllocationStatus.OPTIMAL,
        "golden_within_tolerance": all(value <= TOLERANCE for value in absolute_error.values()),
        "infeasible_preserved": (
            infeasible.status is AllocationStatus.INFEASIBLE
            and not infeasible.weights
            and infeasible.cash_weight is None
        ),
        "surface_contains_reversal": surface.adjacent_reversal_count >= 1,
        "evpi_matches_hand_calculation": abs(
            information.expected_value_of_perfect_information - 0.1
        )
        <= TOLERANCE,
        "stress_optimal": stress.status is AllocationStatus.OPTIMAL,
        "stress_weights_sum_to_one": abs(
            sum(stress.weights.values()) + float(stress.cash_weight) - 1.0
        )
        <= TOLERANCE,
    }
    semantic: dict[str, Any] = {
        "schema_version": "1.0.0",
        "evidence_kind": "synthetic_hand_checked_and_fixed_size_solver_benchmark",
        "claim_boundary": (
            "This receipt proves internal LP compilation, hand-calculated arithmetic, explicit "
            "infeasibility, finite-grid decision reversals, discrete-state EVPI, and one "
            "fixed-size local solve. It is not a real portfolio, market return, external review, "
            "production capacity, or investment recommendation."
        ),
        "tolerance": TOLERANCE,
        "assertions": assertions,
        "all_assertions_passed": all(assertions.values()),
        "golden": {
            "expected": expected,
            "actual": actual,
            "absolute_error": absolute_error,
            "result": golden.model_dump(mode="json"),
        },
        "infeasible": infeasible.model_dump(mode="json"),
        "reversal_surface": surface.model_dump(mode="json"),
        "value_of_perfect_information": information.model_dump(mode="json"),
        "stress": {
            "asset_count": len(stress_problem.assets),
            "scenario_count": len(stress_problem.risk_scenarios),
            "linear_constraint_count": len(stress_problem.linear_constraints),
            "result": stress.model_dump(mode="json"),
        },
    }
    if not semantic["all_assertions_passed"]:
        raise SystemExit("capital allocator benchmark assertion failed")
    semantic_sha256 = _hash(semantic)
    payload = {
        **semantic,
        "semantic_sha256": semantic_sha256,
        "runtime": {
            "measured_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": elapsed,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "solver_method": "scipy.optimize.linprog(method=highs)",
        },
    }
    payload["receipt_sha256"] = _hash(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    print(
        f"golden_pass=true infeasible_pass=true reversals={surface.adjacent_reversal_count} "
        f"evpi={information.expected_value_of_perfect_information:.6f} "
        f"stress_assets={len(stress_problem.assets)} "
        f"stress_scenarios={len(stress_problem.risk_scenarios)} "
        f"elapsed_seconds={elapsed:.6f} receipt={args.output}"
    )


def _asset(
    asset_id: str,
    lower: float,
    upper: float,
    *,
    current_weight: float = 0.0,
    max_weight: float = 0.7,
    cost_bps: float = 10.0,
) -> AssetCandidate:
    return AssetCandidate(
        asset_id=asset_id,
        label=asset_id.upper(),
        expected_return_lower=lower,
        expected_return_upper=upper,
        current_weight=current_weight,
        min_weight=0.0,
        max_weight=max_weight,
        capacity_usd=1_000_000_000.0,
        transaction_cost_bps=cost_bps,
        evidence_class=EvidenceClass.SIMULATED,
        available_at=DECISION,
        source_record_ids=(),
        sources=(),
        derivation="Deterministic synthetic return interval for solver verification.",
        limitations=("Synthetic benchmark input; not a forecast.",),
    )


def _scenario(scenario_id: str, losses: dict[str, float]) -> RiskScenario:
    return RiskScenario(
        scenario_id=scenario_id,
        loss_fraction_by_asset=losses,
        evidence_class=EvidenceClass.SIMULATED,
        available_at=DECISION,
        source_record_ids=(),
        sources=(),
        derivation="Deterministic synthetic risk vector for solver verification.",
        limitations=("Synthetic benchmark input; not a forecast.",),
    )


def _problem(
    problem_id: str,
    assets: tuple[AssetCandidate, ...],
    scenarios: tuple[RiskScenario, ...],
    *,
    cash_return: float = 0.02,
    cash_max: float = 1.0,
    loss_aversion: float = 0.0,
    constraints: tuple[LinearAllocationConstraint, ...] = (),
) -> AllocationProblem:
    return AllocationProblem(
        problem_id=problem_id,
        decision_time=DECISION,
        total_capital_usd=1_000_000.0,
        assets=assets,
        risk_scenarios=scenarios,
        current_cash_weight=1.0,
        cash_return=cash_return,
        cash_min_weight=0.0,
        cash_max_weight=cash_max,
        max_one_way_turnover=1.0,
        loss_aversion=loss_aversion,
        uncertainty_aversion=0.0,
        linear_constraints=constraints,
        limitations=("Synthetic allocator benchmark; not an investment recommendation.",),
    )


def _golden_problem() -> AllocationProblem:
    assets = (
        _asset("asset:a", 0.10, 0.12),
        _asset("asset:b", 0.06, 0.07),
    )
    return _problem(
        "problem:golden",
        assets,
        (_scenario("scenario:none", {"asset:a": 0.0, "asset:b": 0.0}),),
    )


def _infeasible_problem() -> AllocationProblem:
    base = _golden_problem()
    constraints = (
        LinearAllocationConstraint(
            constraint_id="constraint:a-low",
            coefficients={"asset:a": 1.0},
            upper_bound=0.2,
            rationale="Intentional benchmark conflict.",
        ),
        LinearAllocationConstraint(
            constraint_id="constraint:a-high",
            coefficients={"asset:a": 1.0},
            lower_bound=0.8,
            rationale="Intentional benchmark conflict.",
        ),
    )
    values = base.model_dump(mode="python")
    values.update(problem_id="problem:infeasible", linear_constraints=constraints)
    return AllocationProblem.model_validate(values)


def _surface_problem() -> AllocationProblem:
    assets = (
        _asset("asset:a", 0.08, 0.12, max_weight=1.0, cost_bps=0.0),
        _asset("asset:b", 0.08, 0.08, max_weight=1.0, cost_bps=0.0),
    )
    return _problem(
        "problem:surface",
        assets,
        (_scenario("scenario:base", {"asset:a": 0.0, "asset:b": 0.0}),),
        cash_return=0.0,
        cash_max=0.0,
        loss_aversion=0.5,
    )


def _information_problem() -> AllocationProblem:
    assets = (
        _asset("asset:a", 0.0, 0.0, max_weight=1.0, cost_bps=0.0),
        _asset("asset:b", 0.0, 0.0, max_weight=1.0, cost_bps=0.0),
    )
    return _problem(
        "problem:information",
        assets,
        (_scenario("scenario:none", {"asset:a": 0.0, "asset:b": 0.0}),),
        cash_return=0.0,
        cash_max=0.0,
    )


def _information_state(
    state_id: str,
    probability: float,
    returns: dict[str, float],
) -> InformationState:
    return InformationState(
        state_id=state_id,
        probability=probability,
        asset_returns=returns,
        evidence_class=EvidenceClass.SIMULATED,
        available_at=DECISION,
        source_record_ids=(),
        sources=(),
        derivation="Discrete synthetic state for EVPI arithmetic verification.",
        limitations=("Synthetic state; not a forecast.",),
    )


def _stress_problem(*, asset_count: int, scenario_count: int) -> AllocationProblem:
    rng = np.random.default_rng(20260813)
    assets = tuple(
        _asset(
            f"asset:{index:03d}",
            float(0.01 + rng.uniform(0.0, 0.08)),
            float(0.10 + rng.uniform(0.0, 0.10)),
            max_weight=0.05,
            cost_bps=float(1.0 + rng.uniform(0.0, 20.0)),
        )
        for index in range(asset_count)
    )
    asset_ids = tuple(asset.asset_id for asset in assets)
    scenarios = tuple(
        _scenario(
            f"scenario:{index:03d}",
            {asset_id: float(rng.uniform(0.0, 0.5)) for asset_id in asset_ids},
        )
        for index in range(scenario_count)
    )
    return _problem(
        "problem:fixed-size-stress",
        assets,
        scenarios,
        cash_return=0.01,
        cash_max=0.25,
        loss_aversion=0.5,
    )


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


if __name__ == "__main__":
    main()
