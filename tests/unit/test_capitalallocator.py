from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import HttpUrl, ValidationError

from finreplay.contracts import (
    EvidenceClass,
    LicenseClass,
    SourceReference,
    TemporalCoverage,
)
from finreplay.engines import (
    AllocationError,
    AllocationProblem,
    AllocationResult,
    AllocationStatus,
    AssetCandidate,
    CapitalAllocator,
    InformationState,
    InformationValueResult,
    LinearAllocationConstraint,
    ReversalSurface,
    RiskScenario,
    SensitivityAxis,
    SensitivityKind,
)
from finreplay.engines import capitalallocator as module

DECISION = datetime(2023, 3, 8, 18, tzinfo=UTC)
AVAILABLE = DECISION - timedelta(days=1)


def source(
    *,
    digit: str = "1",
    coverage: TemporalCoverage = TemporalCoverage.IMMUTABLE_EVENT,
    retrieved_at: datetime = AVAILABLE,
) -> SourceReference:
    return SourceReference(
        source_id="official.allocation.fixture",
        publisher="Official Allocation Fixture Publisher",
        url=HttpUrl("https://example.gov/allocation.json"),
        retrieved_at=retrieved_at,
        source_version=f"fixture-{digit}",
        sha256=digit * 64,
        license_class=LicenseClass.REDISTRIBUTABLE,
        temporal_coverage=coverage,
        vintage_as_of=None if coverage is TemporalCoverage.LATEST_ONLY else retrieved_at,
        redistribution_note="Deterministic fixture only.",
    )


def asset(
    asset_id: str,
    lower: float,
    upper: float | None = None,
    *,
    current_weight: float = 0.0,
    min_weight: float = 0.0,
    max_weight: float = 0.7,
    capacity_usd: float = 1_000_000_000.0,
    cost_bps: float = 10.0,
    evidence: EvidenceClass = EvidenceClass.SIMULATED,
    sources: tuple[SourceReference, ...] = (),
    record_ids: tuple[str, ...] = (),
    available_at: datetime = AVAILABLE,
) -> AssetCandidate:
    return AssetCandidate(
        asset_id=asset_id,
        label=asset_id.upper(),
        expected_return_lower=lower,
        expected_return_upper=lower if upper is None else upper,
        current_weight=current_weight,
        min_weight=min_weight,
        max_weight=max_weight,
        capacity_usd=capacity_usd,
        transaction_cost_bps=cost_bps,
        evidence_class=evidence,
        available_at=available_at,
        source_record_ids=record_ids,
        sources=sources,
        derivation="Deterministic fixture return interval for allocator verification.",
        limitations=("Synthetic fixture; not a forecast or realized return.",),
    )


def scenario(
    losses: dict[str, float],
    *,
    scenario_id: str = "scenario:base",
    evidence: EvidenceClass = EvidenceClass.SIMULATED,
    sources: tuple[SourceReference, ...] = (),
    record_ids: tuple[str, ...] = (),
    available_at: datetime = AVAILABLE,
) -> RiskScenario:
    return RiskScenario(
        scenario_id=scenario_id,
        loss_fraction_by_asset=losses,
        evidence_class=evidence,
        available_at=available_at,
        source_record_ids=record_ids,
        sources=sources,
        derivation="Deterministic fixture loss vector for robust allocation verification.",
        limitations=("Synthetic loss vector; not a forecast.",),
    )


def problem(
    *,
    assets: tuple[AssetCandidate, ...] | None = None,
    scenarios: tuple[RiskScenario, ...] | None = None,
    current_cash_weight: float = 1.0,
    cash_return: float = 0.02,
    cash_min: float = 0.0,
    cash_max: float = 1.0,
    turnover: float = 1.0,
    loss_aversion: float = 0.0,
    uncertainty_aversion: float = 0.0,
    max_worst_loss: float | None = None,
    constraints: tuple[LinearAllocationConstraint, ...] = (),
    total_capital: float = 1_000_000.0,
) -> AllocationProblem:
    candidates = assets or (
        asset("asset:a", 0.10, 0.12),
        asset("asset:b", 0.06, 0.07),
    )
    risk = scenarios or (
        scenario({candidate.asset_id: 0.0 for candidate in candidates}),
    )
    return AllocationProblem(
        problem_id="problem:allocator-golden",
        decision_time=DECISION,
        total_capital_usd=total_capital,
        assets=candidates,
        risk_scenarios=risk,
        current_cash_weight=current_cash_weight,
        cash_return=cash_return,
        cash_min_weight=cash_min,
        cash_max_weight=cash_max,
        max_one_way_turnover=turnover,
        loss_aversion=loss_aversion,
        uncertainty_aversion=uncertainty_aversion,
        max_worst_case_loss=max_worst_loss,
        linear_constraints=constraints,
        limitations=("Fixture allocation is not an investment recommendation.",),
    )


def test_solver_matches_hand_calculated_return_cost_solution() -> None:
    result = CapitalAllocator().solve(problem())
    assert result.status is AllocationStatus.OPTIMAL
    assert result.weights == pytest.approx({"asset:a": 0.7, "asset:b": 0.3})
    assert result.cash_weight == 0
    assert result.expected_return_lower == pytest.approx(0.088)
    assert result.expected_return_upper == pytest.approx(0.105)
    assert result.transaction_cost_fraction == pytest.approx(0.001)
    assert result.one_way_turnover == pytest.approx(1.0)
    assert result.robust_utility == pytest.approx(0.087)
    assert result.worst_case_loss == 0
    assert result.solver_status == 0
    assert "asset:asset:a:max" in result.binding_constraints
    assert "cash:min" in result.binding_constraints


def test_worst_case_loss_penalty_changes_the_capital_decision() -> None:
    candidates = (
        asset("asset:a", 0.10, max_weight=0.7),
        asset("asset:b", 0.06, max_weight=0.7),
    )
    stress = scenario({"asset:a": 0.50, "asset:b": 0.01})
    result = CapitalAllocator().solve(
        problem(assets=candidates, scenarios=(stress,), loss_aversion=0.2)
    )
    assert result.weights == pytest.approx({"asset:a": 0.0, "asset:b": 0.7})
    assert result.cash_weight == pytest.approx(0.3)
    assert result.worst_case_loss == pytest.approx(0.007)
    assert result.robust_utility == pytest.approx(0.0459)


def test_interval_width_penalty_prefers_the_narrower_candidate() -> None:
    candidates = (
        asset("asset:a", 0.08, 0.50, max_weight=0.7),
        asset("asset:b", 0.07, 0.08, max_weight=0.7),
    )
    result = CapitalAllocator().solve(
        problem(assets=candidates, uncertainty_aversion=0.2)
    )
    assert result.weights == pytest.approx({"asset:a": 0.0, "asset:b": 0.7})
    assert result.cash_weight == pytest.approx(0.3)
    assert result.uncertainty_penalty == pytest.approx(0.0014)


def test_capacity_turnover_loss_and_linear_constraints_are_enforced() -> None:
    candidates = (
        asset("asset:a", 0.20, max_weight=1.0, capacity_usd=300_000),
        asset("asset:b", 0.10, max_weight=1.0),
    )
    constraints = (
        LinearAllocationConstraint(
            constraint_id="constraint:b-minimum",
            coefficients={"asset:b": 1.0},
            lower_bound=0.4,
            rationale="Fixture policy minimum for asset B.",
        ),
    )
    stress = scenario({"asset:a": 0.2, "asset:b": 0.1})
    result = CapitalAllocator().solve(
        problem(
            assets=candidates,
            scenarios=(stress,),
            constraints=constraints,
            turnover=0.7,
            max_worst_loss=0.10,
        )
    )
    assert result.status is AllocationStatus.OPTIMAL
    assert result.weights["asset:a"] <= 0.3 + 1e-9
    assert result.weights["asset:b"] >= 0.4 - 1e-9
    assert result.one_way_turnover is not None
    assert result.one_way_turnover <= 0.7 + 1e-9
    assert result.worst_case_loss is not None
    assert result.worst_case_loss <= 0.10 + 1e-9
    assert result.constraint_slacks["asset:asset:a:max"] == pytest.approx(0)
    assert result.constraint_slacks["linear:constraint:b-minimum:min"] >= -1e-9


@pytest.mark.parametrize(
    ("candidate_assets", "cash_max", "turnover", "expected_reason"),
    [
        (
            (
                asset(
                    "asset:a",
                    0.1,
                    min_weight=0.5,
                    max_weight=1.0,
                    capacity_usd=100_000,
                ),
                asset("asset:b", 0.1),
            ),
            1.0,
            1.0,
            "minimum exceeds",
        ),
        (
            (
                asset("asset:a", 0.1, min_weight=0.6, max_weight=1.0),
                asset("asset:b", 0.1, min_weight=0.6, max_weight=1.0),
            ),
            1.0,
            1.0,
            "minimum asset",
        ),
        (
            (
                asset("asset:a", 0.1, max_weight=0.4),
                asset("asset:b", 0.1, max_weight=0.4),
            ),
            0.0,
            1.0,
            "maximum weights",
        ),
        (
            (
                asset("asset:a", 0.1, min_weight=0.5, max_weight=1.0),
                asset("asset:b", 0.1, max_weight=1.0),
            ),
            0.5,
            0.1,
            "turnover cap",
        ),
    ],
)
def test_preflight_infeasibility_is_preserved_without_candidate_weights(
    candidate_assets: tuple[AssetCandidate, ...],
    cash_max: float,
    turnover: float,
    expected_reason: str,
) -> None:
    result = CapitalAllocator().solve(
        problem(assets=candidate_assets, cash_max=cash_max, turnover=turnover)
    )
    assert result.status is AllocationStatus.INFEASIBLE
    assert result.solver_status == -1
    assert result.weights == {}
    assert result.cash_weight is None
    assert any(expected_reason in reason for reason in result.infeasibility_reasons)
    assert "never silently relaxed" in result.limitations[-1]


def test_highs_infeasibility_is_preserved_for_conflicting_general_constraints() -> None:
    constraints = (
        LinearAllocationConstraint(
            constraint_id="constraint:a-low",
            coefficients={"asset:a": 1.0},
            upper_bound=0.2,
            rationale="Fixture upper bound.",
        ),
        LinearAllocationConstraint(
            constraint_id="constraint:a-high",
            coefficients={"asset:a": 1.0},
            lower_bound=0.8,
            rationale="Fixture conflicting lower bound.",
        ),
    )
    result = CapitalAllocator().solve(problem(constraints=constraints))
    assert result.status is AllocationStatus.INFEASIBLE
    assert result.solver_status == 2
    assert result.weights == {}
    assert "preserved" in result.infeasibility_reasons[0]


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        (1, AllocationStatus.SOLVER_LIMIT),
        (3, AllocationStatus.UNBOUNDED),
        (4, AllocationStatus.SOLVER_ERROR),
    ],
)
def test_nonoptimal_solver_status_never_leaks_a_candidate_solution(
    monkeypatch: pytest.MonkeyPatch,
    raw_status: int,
    expected: AllocationStatus,
) -> None:
    monkeypatch.setattr(
        module,
        "linprog",
        lambda *args, **kwargs: SimpleNamespace(
            status=raw_status,
            message="fixture solver status",
            nit=3,
            x=[0.7, 0.3, 0.0],
        ),
    )
    result = CapitalAllocator().solve(problem())
    assert result.status is expected
    assert result.weights == {}
    assert result.cash_weight is None


def test_solver_exception_missing_vector_and_invalid_vector_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(module, "linprog", explode)
    failed = CapitalAllocator().solve(problem())
    assert failed.status is AllocationStatus.SOLVER_ERROR
    assert "fixture failure" in failed.solver_message

    monkeypatch.setattr(
        module,
        "linprog",
        lambda *args, **kwargs: SimpleNamespace(
            status=0,
            message="optimal but missing",
            nit=1,
            x=None,
        ),
    )
    missing = CapitalAllocator().solve(problem())
    assert missing.status is AllocationStatus.SOLVER_ERROR
    assert "without a solution" in missing.solver_message

    dimension = module._CompiledProblem(problem()).dimension
    monkeypatch.setattr(
        module,
        "linprog",
        lambda *args, **kwargs: SimpleNamespace(
            status=0,
            message="invalid fixture vector",
            nit=1,
            x=[0.0] * dimension,
        ),
    )
    invalid = CapitalAllocator().solve(problem())
    assert invalid.status is AllocationStatus.SOLVER_ERROR
    assert "post-solve" in invalid.solver_message


def test_result_hash_is_deterministic_and_rejects_tampering() -> None:
    first = CapitalAllocator().solve(problem())
    second = CapitalAllocator().solve(problem())
    assert first == second
    values = first.model_dump()
    values["robust_utility"] = 99.0
    with pytest.raises(ValidationError, match="does not match"):
        AllocationResult.model_validate(values)


def test_result_semantics_reject_tampering_even_with_recomputed_hash() -> None:
    original = CapitalAllocator().solve(problem())
    values = original.model_dump()
    values["weights"] = {"asset:a": 0.1, "asset:b": 0.1}
    values["result_sha256"] = module._hash(
        {key: value for key, value in values.items() if key != "result_sha256"}
    )
    with pytest.raises(ValidationError, match="sum to one"):
        AllocationResult.model_validate(values)

    values = original.model_dump()
    values["robust_utility"] = float("inf")
    values["result_sha256"] = module._hash(
        {key: value for key, value in values.items() if key != "result_sha256"}
    )
    with pytest.raises(ValidationError, match="finite"):
        AllocationResult.model_validate(values)


def surface_problem() -> AllocationProblem:
    candidates = (
        asset("asset:a", 0.08, 0.12, max_weight=1.0, cost_bps=0),
        asset("asset:b", 0.08, 0.08, max_weight=1.0, cost_bps=0),
    )
    return problem(
        assets=candidates,
        scenarios=(scenario({"asset:a": 0.0, "asset:b": 0.0}),),
        cash_return=0.0,
        cash_max=0.0,
        loss_aversion=0.5,
    )


def test_one_and_two_dimensional_reversal_surfaces_find_decision_changes() -> None:
    allocator = CapitalAllocator()
    return_axis = SensitivityAxis(
        axis_id="axis:a-return",
        kind=SensitivityKind.EXPECTED_RETURN_LOWER,
        asset_id="asset:a",
        values=(0.04, 0.08, 0.12),
    )
    one = allocator.reversal_surface(surface_problem(), (return_axis,))
    assert len(one.points) == 3
    assert one.decision_region_count >= 2
    assert one.adjacent_reversal_count >= 1
    assert {point.leader_asset_id for point in one.points} >= {"asset:a", "asset:b"}

    loss_axis = SensitivityAxis(
        axis_id="axis:a-loss",
        kind=SensitivityKind.SCENARIO_LOSS,
        asset_id="asset:a",
        scenario_id="scenario:base",
        values=(0.0, 0.2),
    )
    two = allocator.reversal_surface(surface_problem(), (return_axis, loss_axis))
    assert len(two.points) == 6
    assert two.decision_region_count >= 2
    assert two.adjacent_reversal_count >= 1


def test_reversal_surface_hash_rejects_tampering() -> None:
    axis = SensitivityAxis(
        axis_id="axis:a-return",
        kind=SensitivityKind.EXPECTED_RETURN_LOWER,
        asset_id="asset:a",
        values=(0.04, 0.12),
    )
    surface = CapitalAllocator().reversal_surface(surface_problem(), (axis,))
    values = surface.model_dump()
    values["adjacent_reversal_count"] = 99
    with pytest.raises(ValidationError, match="does not match"):
        ReversalSurface.model_validate(values)

    values = surface.model_dump()
    values["adjacent_reversal_count"] = 99
    values["surface_sha256"] = module._hash(
        {key: value for key, value in values.items() if key != "surface_sha256"}
    )
    with pytest.raises(ValidationError, match="does not match surface points"):
        ReversalSurface.model_validate(values)


def test_reversal_axes_fail_on_unknown_duplicate_or_invalid_targets() -> None:
    allocator = CapitalAllocator()
    unknown = SensitivityAxis(
        axis_id="axis:unknown",
        kind=SensitivityKind.EXPECTED_RETURN_LOWER,
        asset_id="asset:missing",
        values=(0.01, 0.02),
    )
    with pytest.raises(AllocationError, match="unknown sensitivity asset"):
        allocator.reversal_surface(surface_problem(), (unknown,))

    first = SensitivityAxis(
        axis_id="axis:first",
        kind=SensitivityKind.EXPECTED_RETURN_LOWER,
        asset_id="asset:a",
        values=(0.04, 0.08),
    )
    duplicate = first.model_copy(update={"axis_id": "axis:second"})
    with pytest.raises(AllocationError, match="same semantic target"):
        allocator.reversal_surface(surface_problem(), (first, duplicate))
    invalid = first.model_copy(update={"values": (0.13, 0.14)})
    with pytest.raises(AllocationError, match="sensitivity point is invalid"):
        allocator.reversal_surface(surface_problem(), (invalid,))
    with pytest.raises(AllocationError, match="one or two"):
        allocator.reversal_surface(surface_problem(), ())


def information_state(
    state_id: str,
    probability: float,
    returns: dict[str, float],
    *,
    available_at: datetime = AVAILABLE,
    sources: tuple[SourceReference, ...] = (),
) -> InformationState:
    return InformationState(
        state_id=state_id,
        probability=probability,
        asset_returns=returns,
        evidence_class=(EvidenceClass.REPORTED if sources else EvidenceClass.SIMULATED),
        available_at=available_at,
        source_record_ids=((f"record:{state_id}",) if sources else ()),
        sources=sources,
        derivation="Discrete conditional-return state for EVPI arithmetic verification.",
        limitations=("Fixture state; not a forecast.",),
    )


def information_problem() -> AllocationProblem:
    candidates = (
        asset("asset:a", 0.0, max_weight=1.0, cost_bps=0),
        asset("asset:b", 0.0, max_weight=1.0, cost_bps=0),
    )
    return problem(
        assets=candidates,
        scenarios=(scenario({"asset:a": 0.0, "asset:b": 0.0}),),
        cash_return=0.0,
        cash_max=0.0,
        loss_aversion=0.0,
    )


def test_value_of_perfect_information_matches_hand_calculation_and_order_is_canonical() -> None:
    states = (
        information_state("state:good-a", 0.5, {"asset:a": 0.2, "asset:b": 0.0}),
        information_state("state:good-b", 0.5, {"asset:a": 0.0, "asset:b": 0.2}),
    )
    allocator = CapitalAllocator()
    first = allocator.value_of_perfect_information(information_problem(), states)
    reversed_order = allocator.value_of_perfect_information(
        information_problem(), tuple(reversed(states))
    )
    assert first == reversed_order
    assert first.expected_utility_without_information == pytest.approx(0.1)
    assert first.expected_utility_with_perfect_information == pytest.approx(0.2)
    assert first.expected_value_of_perfect_information == pytest.approx(0.1)
    assert [item.state_id for item in first.state_results] == [
        "state:good-a",
        "state:good-b",
    ]
    assert first.state_results[0].weights_with_information == pytest.approx(
        {"asset:a": 1.0, "asset:b": 0.0}
    )
    assert first.state_results[1].weights_with_information == pytest.approx(
        {"asset:a": 0.0, "asset:b": 1.0}
    )


def test_information_value_hash_binds_state_inputs_and_rejects_tampering() -> None:
    states = (
        information_state("state:good-a", 0.5, {"asset:a": 0.2, "asset:b": 0.0}),
        information_state("state:good-b", 0.5, {"asset:a": 0.0, "asset:b": 0.2}),
    )
    result = CapitalAllocator().value_of_perfect_information(information_problem(), states)
    changed = (
        states[0].model_copy(update={"asset_returns": {"asset:a": 0.3, "asset:b": 0.0}}),
        states[1],
    )
    changed_result = CapitalAllocator().value_of_perfect_information(
        information_problem(), changed
    )
    assert changed_result.input_states_sha256 != result.input_states_sha256
    values = result.model_dump()
    values["expected_value_of_perfect_information"] = 0.9
    with pytest.raises(ValidationError, match="does not match"):
        InformationValueResult.model_validate(values)

    values = result.model_dump()
    values["expected_value_of_perfect_information"] = 0.09
    values["result_sha256"] = module._hash(
        {key: value for key, value in values.items() if key != "result_sha256"}
    )
    with pytest.raises(ValidationError, match="utility difference"):
        InformationValueResult.model_validate(values)


def test_information_value_fails_on_bad_state_sets_or_future_evidence() -> None:
    allocator = CapitalAllocator()
    base = information_problem()
    a = information_state("state:a", 0.6, {"asset:a": 0.2, "asset:b": 0.0})
    b = information_state("state:b", 0.3, {"asset:a": 0.0, "asset:b": 0.2})
    with pytest.raises(AllocationError, match="probabilities"):
        allocator.value_of_perfect_information(base, (a, b))
    with pytest.raises(AllocationError, match="cover every asset"):
        allocator.value_of_perfect_information(
            base,
            (
                a.model_copy(update={"probability": 0.5}),
                b.model_copy(
                    update={"probability": 0.5, "asset_returns": {"asset:a": 0.2}}
                ),
            ),
        )
    future = b.model_copy(
        update={"probability": 0.4, "available_at": DECISION + timedelta(seconds=1)}
    )
    with pytest.raises(AllocationError, match="not available"):
        allocator.value_of_perfect_information(base, (a, future))
    with pytest.raises(AllocationError, match="at least two"):
        allocator.value_of_perfect_information(base, (a,))


def test_asset_scenario_problem_and_constraint_contracts_fail_closed() -> None:
    with pytest.raises(ValidationError, match="requires provenance"):
        asset("asset:a", 0.1, evidence=EvidenceClass.REPORTED)
    with pytest.raises(ValidationError, match="upper bound"):
        asset("asset:a", 0.2, 0.1)
    with pytest.raises(ValidationError, match="max_weight"):
        asset("asset:a", 0.1, min_weight=0.6, max_weight=0.5)
    with pytest.raises(ValidationError, match="finite"):
        asset("asset:a", float("inf"))
    with pytest.raises(ValidationError, match="between zero and one"):
        scenario({"asset:a": 2.0, "asset:b": 0.0})
    with pytest.raises(ValidationError, match="lower or upper"):
        LinearAllocationConstraint(
            constraint_id="constraint:empty",
            coefficients={"asset:a": 1.0},
            rationale="Intentional invalid fixture.",
        )
    with pytest.raises(ValidationError, match="sum to one"):
        problem(current_cash_weight=0.5)
    with pytest.raises(ValidationError, match="cover every asset"):
        problem(scenarios=(scenario({"asset:a": 0.1}),))
    with pytest.raises(ValidationError, match="unknown assets"):
        problem(
            constraints=(
                LinearAllocationConstraint(
                    constraint_id="constraint:unknown",
                    coefficients={"asset:missing": 1.0},
                    upper_bound=0.5,
                    rationale="Intentional unknown asset.",
                ),
            )
        )


def test_temporal_contracts_reject_future_naive_and_latest_only_inputs() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        asset("asset:a", 0.1, available_at=AVAILABLE.replace(tzinfo=None))
    future_asset = asset("asset:a", 0.1, available_at=DECISION + timedelta(seconds=1))
    other = asset("asset:b", 0.1)
    with pytest.raises(ValidationError, match="not available"):
        problem(assets=(future_asset, other))
    latest = source(
        coverage=TemporalCoverage.LATEST_ONLY,
        retrieved_at=DECISION + timedelta(seconds=1),
    )
    current_claim = asset(
        "asset:a",
        0.1,
        evidence=EvidenceClass.REPORTED,
        sources=(latest,),
        record_ids=("record:latest",),
    )
    with pytest.raises(ValidationError, match="cannot predate"):
        problem(assets=(current_claim, other))


def test_sensitivity_axis_contract_rejects_bad_shape_and_semantics() -> None:
    with pytest.raises(ValidationError, match="strictly increasing"):
        SensitivityAxis(
            axis_id="axis:unordered",
            kind=SensitivityKind.CASH_RETURN,
            values=(0.1, 0.0),
        )
    with pytest.raises(ValidationError, match="requires asset_id"):
        SensitivityAxis(
            axis_id="axis:no-asset",
            kind=SensitivityKind.EXPECTED_RETURN_LOWER,
            values=(0.0, 0.1),
        )
    with pytest.raises(ValidationError, match="requires scenario_id"):
        SensitivityAxis(
            axis_id="axis:no-scenario",
            kind=SensitivityKind.SCENARIO_LOSS,
            asset_id="asset:a",
            values=(0.0, 0.1),
        )
