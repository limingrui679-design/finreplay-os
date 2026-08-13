"""Evidence-aware robust capital allocation, reversals, and value of information."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypeAlias, TypeVar

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.optimize import linprog

from finreplay.contracts import EvidenceClass, SourceReference, TemporalCoverage

NUMERICAL_TOLERANCE = 1e-8
HASH_DECIMALS = 12
FloatArray: TypeAlias = NDArray[np.float64]
ModelT = TypeVar("ModelT", bound=BaseModel)


class AllocationError(RuntimeError):
    """Raised when a requested allocation analysis is internally inconsistent."""


class AllocationStatus(StrEnum):
    OPTIMAL = "optimal"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    SOLVER_LIMIT = "solver_limit"
    SOLVER_ERROR = "solver_error"


class SensitivityKind(StrEnum):
    EXPECTED_RETURN_LOWER = "expected_return_lower"
    EXPECTED_RETURN_UPPER = "expected_return_upper"
    TRANSACTION_COST_BPS = "transaction_cost_bps"
    SCENARIO_LOSS = "scenario_loss"
    CASH_RETURN = "cash_return"
    LOSS_AVERSION = "loss_aversion"
    UNCERTAINTY_AVERSION = "uncertainty_aversion"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AssetCandidate(_StrictModel):
    """One long-only asset with interval return, policy bounds, and provenance."""

    asset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,199}$")
    label: str = Field(min_length=1, max_length=300)
    expected_return_lower: float = Field(ge=-1.0)
    expected_return_upper: float = Field(ge=-1.0)
    current_weight: float = Field(ge=0.0, le=1.0)
    min_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    max_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    capacity_usd: float = Field(gt=0.0)
    transaction_cost_bps: float = Field(ge=0.0)
    evidence_class: EvidenceClass
    available_at: datetime
    source_record_ids: tuple[str, ...]
    sources: tuple[SourceReference, ...]
    derivation: str = Field(min_length=10, max_length=2_000)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_asset(self) -> AssetCandidate:
        _require_aware(self.available_at, "available_at")
        finite = (
            self.expected_return_lower,
            self.expected_return_upper,
            self.current_weight,
            self.min_weight,
            self.max_weight,
            self.capacity_usd,
            self.transaction_cost_bps,
        )
        if any(not math.isfinite(value) for value in finite):
            raise ValueError("asset numeric values must be finite")
        if self.expected_return_upper < self.expected_return_lower:
            raise ValueError("expected return upper bound must not be below lower bound")
        if self.max_weight < self.min_weight:
            raise ValueError("asset max_weight must not be below min_weight")
        _validate_provenance(
            evidence_class=self.evidence_class,
            source_record_ids=self.source_record_ids,
            sources=self.sources,
        )
        if any(not item.strip() for item in self.limitations):
            raise ValueError("asset limitations must be non-empty")
        return self


class RiskScenario(_StrictModel):
    """One loss vector used in the worst-case epigraph."""

    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,199}$")
    loss_fraction_by_asset: dict[str, float] = Field(min_length=1)
    evidence_class: EvidenceClass
    available_at: datetime
    source_record_ids: tuple[str, ...]
    sources: tuple[SourceReference, ...]
    derivation: str = Field(min_length=10, max_length=2_000)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scenario(self) -> RiskScenario:
        _require_aware(self.available_at, "available_at")
        if any(not key.strip() for key in self.loss_fraction_by_asset):
            raise ValueError("risk scenario asset IDs must be non-empty")
        losses = tuple(self.loss_fraction_by_asset.values())
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in losses):
            raise ValueError("risk scenario losses must be finite fractions between zero and one")
        _validate_provenance(
            evidence_class=self.evidence_class,
            source_record_ids=self.source_record_ids,
            sources=self.sources,
        )
        if any(not item.strip() for item in self.limitations):
            raise ValueError("risk scenario limitations must be non-empty")
        return self


class LinearAllocationConstraint(_StrictModel):
    """General auditable linear constraint over risky-asset weights."""

    constraint_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,199}$")
    coefficients: dict[str, float] = Field(min_length=1)
    lower_bound: float | None = None
    upper_bound: float | None = None
    rationale: str = Field(min_length=5, max_length=1_000)

    @model_validator(mode="after")
    def validate_constraint(self) -> LinearAllocationConstraint:
        if self.lower_bound is None and self.upper_bound is None:
            raise ValueError("linear constraint requires a lower or upper bound")
        values = (
            *self.coefficients.values(),
            self.lower_bound,
            self.upper_bound,
        )
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("linear constraint values must be finite")
        if any(not key.strip() for key in self.coefficients):
            raise ValueError("linear constraint asset IDs must be non-empty")
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.upper_bound < self.lower_bound
        ):
            raise ValueError("linear constraint upper bound must not be below lower bound")
        return self


class AllocationProblem(_StrictModel):
    """One immutable robust allocation decision with no silent constraint relaxation."""

    problem_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,199}$")
    decision_time: datetime
    total_capital_usd: float = Field(gt=0.0)
    assets: tuple[AssetCandidate, ...] = Field(min_length=1)
    risk_scenarios: tuple[RiskScenario, ...] = Field(min_length=1)
    current_cash_weight: float = Field(ge=0.0, le=1.0)
    cash_return: float = Field(ge=-1.0)
    cash_min_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    cash_max_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    max_one_way_turnover: float = Field(default=1.0, ge=0.0, le=1.0)
    loss_aversion: float = Field(default=1.0, ge=0.0)
    uncertainty_aversion: float = Field(default=0.0, ge=0.0)
    max_worst_case_loss: float | None = Field(default=None, ge=0.0, le=1.0)
    linear_constraints: tuple[LinearAllocationConstraint, ...] = ()
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_problem(self) -> AllocationProblem:
        _require_aware(self.decision_time, "decision_time")
        finite = (
            self.total_capital_usd,
            self.current_cash_weight,
            self.cash_return,
            self.cash_min_weight,
            self.cash_max_weight,
            self.max_one_way_turnover,
            self.loss_aversion,
            self.uncertainty_aversion,
            self.max_worst_case_loss,
        )
        if any(value is not None and not math.isfinite(value) for value in finite):
            raise ValueError("allocation problem numeric values must be finite")
        if self.cash_max_weight < self.cash_min_weight:
            raise ValueError("cash maximum must not be below cash minimum")
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset IDs must be unique")
        scenario_ids = [scenario.scenario_id for scenario in self.risk_scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("risk scenario IDs must be unique")
        constraint_ids = [constraint.constraint_id for constraint in self.linear_constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("linear constraint IDs must be unique")
        expected = set(asset_ids)
        for scenario in self.risk_scenarios:
            if set(scenario.loss_fraction_by_asset) != expected:
                raise ValueError("every risk scenario must cover every asset exactly once")
        for constraint in self.linear_constraints:
            unknown = set(constraint.coefficients) - expected
            if unknown:
                raise ValueError(f"linear constraint references unknown assets: {sorted(unknown)}")
        current_total = self.current_cash_weight + sum(
            asset.current_weight for asset in self.assets
        )
        if not math.isclose(current_total, 1.0, abs_tol=NUMERICAL_TOLERANCE):
            raise ValueError("current asset and cash weights must sum to one")
        for asset in self.assets:
            if asset.available_at > self.decision_time:
                raise ValueError("allocation input was not available by decision_time")
            if any(
                source.temporal_coverage is TemporalCoverage.LATEST_ONLY
                and source.retrieved_at > self.decision_time
                for source in asset.sources
            ):
                raise ValueError(
                    "latest_only allocation evidence cannot predate its retrieval"
                )
        for scenario in self.risk_scenarios:
            if scenario.available_at > self.decision_time:
                raise ValueError("allocation input was not available by decision_time")
            if any(
                source.temporal_coverage is TemporalCoverage.LATEST_ONLY
                and source.retrieved_at > self.decision_time
                for source in scenario.sources
            ):
                raise ValueError(
                    "latest_only allocation evidence cannot predate its retrieval"
                )
        if any(not item.strip() for item in self.limitations):
            raise ValueError("allocation limitations must be non-empty")
        return self


class AllocationResult(_StrictModel):
    problem_id: str
    status: AllocationStatus
    weights: dict[str, float]
    cash_weight: float | None
    expected_return_lower: float | None
    expected_return_upper: float | None
    uncertainty_penalty: float | None
    worst_case_loss: float | None
    worst_scenario_ids: tuple[str, ...]
    transaction_cost_fraction: float | None
    one_way_turnover: float | None
    robust_utility: float | None
    solver_status: int
    solver_message: str
    solver_iterations: int | None
    constraint_slacks: dict[str, float]
    binding_constraints: tuple[str, ...]
    infeasibility_reasons: tuple[str, ...]
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_result(self) -> AllocationResult:
        solved_fields = (
            self.cash_weight,
            self.expected_return_lower,
            self.expected_return_upper,
            self.uncertainty_penalty,
            self.worst_case_loss,
            self.transaction_cost_fraction,
            self.one_way_turnover,
            self.robust_utility,
        )
        if self.status is AllocationStatus.OPTIMAL:
            if not self.weights or any(value is None for value in solved_fields):
                raise ValueError("optimal result requires complete allocation metrics")
            if self.infeasibility_reasons:
                raise ValueError("optimal result cannot contain infeasibility reasons")
        elif self.weights or any(value is not None for value in solved_fields):
            raise ValueError("non-optimal result cannot contain a candidate allocation")
        numeric_values = (
            *self.weights.values(),
            *(
                value
                for value in solved_fields
                if value is not None
            ),
        )
        if any(not math.isfinite(value) for value in numeric_values):
            raise ValueError("allocation result numeric values must be finite")
        if any(value < -NUMERICAL_TOLERANCE for value in self.weights.values()):
            raise ValueError("allocation result weights cannot be negative")
        if self.cash_weight is not None and self.cash_weight < -NUMERICAL_TOLERANCE:
            raise ValueError("allocation result cash weight cannot be negative")
        if self.status is AllocationStatus.OPTIMAL:
            assert self.cash_weight is not None
            if not math.isclose(
                sum(self.weights.values()) + self.cash_weight,
                1.0,
                abs_tol=NUMERICAL_TOLERANCE,
            ):
                raise ValueError("optimal allocation weights and cash must sum to one")
        if any(not math.isfinite(value) for value in self.constraint_slacks.values()):
            raise ValueError("constraint slacks must be finite")
        expected_hash = _hash(_result_payload(self))
        if expected_hash != self.result_sha256:
            raise ValueError("result_sha256 does not match allocation result content")
        return self


class SensitivityAxis(_StrictModel):
    axis_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,199}$")
    kind: SensitivityKind
    values: tuple[float, ...] = Field(min_length=2, max_length=1_000)
    asset_id: str | None = None
    scenario_id: str | None = None

    @model_validator(mode="after")
    def validate_axis(self) -> SensitivityAxis:
        if any(not math.isfinite(value) for value in self.values):
            raise ValueError("sensitivity values must be finite")
        if tuple(sorted(self.values)) != self.values or len(set(self.values)) != len(self.values):
            raise ValueError("sensitivity values must be strictly increasing")
        asset_kinds = {
            SensitivityKind.EXPECTED_RETURN_LOWER,
            SensitivityKind.EXPECTED_RETURN_UPPER,
            SensitivityKind.TRANSACTION_COST_BPS,
            SensitivityKind.SCENARIO_LOSS,
        }
        if self.kind in asset_kinds and not self.asset_id:
            raise ValueError("asset sensitivity axis requires asset_id")
        if self.kind is SensitivityKind.SCENARIO_LOSS and not self.scenario_id:
            raise ValueError("scenario-loss axis requires scenario_id")
        if self.kind is not SensitivityKind.SCENARIO_LOSS and self.scenario_id is not None:
            raise ValueError("scenario_id is only valid for scenario-loss axes")
        if self.kind not in asset_kinds and self.asset_id is not None:
            raise ValueError("global sensitivity axis cannot specify asset_id")
        return self


class ReversalPoint(_StrictModel):
    coordinates: dict[str, float]
    status: AllocationStatus
    leader_asset_id: str | None
    weights: dict[str, float]
    cash_weight: float | None
    robust_utility: float | None
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_point(self) -> ReversalPoint:
        numeric_values = (
            *self.coordinates.values(),
            *self.weights.values(),
            self.cash_weight,
            self.robust_utility,
        )
        if any(
            value is not None and not math.isfinite(value) for value in numeric_values
        ):
            raise ValueError("reversal point numeric values must be finite")
        if self.status is AllocationStatus.OPTIMAL:
            if self.cash_weight is None or self.robust_utility is None or not self.weights:
                raise ValueError("optimal reversal point requires allocation metrics")
        elif self.weights or self.cash_weight is not None or self.robust_utility is not None:
            raise ValueError("non-optimal reversal point cannot contain allocation metrics")
        return self


class ReversalSurface(_StrictModel):
    problem_id: str
    baseline_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    axes: tuple[SensitivityAxis, ...] = Field(min_length=1, max_length=2)
    points: tuple[ReversalPoint, ...] = Field(min_length=2)
    decision_region_count: int = Field(ge=0)
    adjacent_reversal_count: int = Field(ge=0)
    surface_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_surface(self) -> ReversalSurface:
        expected_points = math.prod(len(axis.values) for axis in self.axes)
        if len(self.points) != expected_points:
            raise ValueError("reversal surface does not cover the Cartesian grid")
        expected_coordinates = {
            tuple(zip((axis.axis_id for axis in self.axes), values, strict=True))
            for values in itertools.product(*(axis.values for axis in self.axes))
        }
        actual_coordinates = {
            tuple((axis.axis_id, point.coordinates.get(axis.axis_id)) for axis in self.axes)
            for point in self.points
        }
        if actual_coordinates != expected_coordinates:
            raise ValueError("reversal surface coordinates do not match the declared grid")
        leaders = {
            point.leader_asset_id
            for point in self.points
            if point.leader_asset_id is not None
        }
        if self.decision_region_count != len(leaders):
            raise ValueError("decision_region_count does not match surface points")
        if self.adjacent_reversal_count != _adjacent_reversals(
            list(self.points), self.axes
        ):
            raise ValueError("adjacent_reversal_count does not match surface points")
        if _hash(_surface_payload(self)) != self.surface_sha256:
            raise ValueError("surface_sha256 does not match surface content")
        return self


class InformationState(_StrictModel):
    state_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,199}$")
    probability: float = Field(gt=0.0, le=1.0)
    asset_returns: dict[str, float] = Field(min_length=1)
    evidence_class: EvidenceClass
    available_at: datetime
    source_record_ids: tuple[str, ...]
    sources: tuple[SourceReference, ...]
    derivation: str = Field(min_length=10, max_length=2_000)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_state(self) -> InformationState:
        _require_aware(self.available_at, "available_at")
        if any(not key.strip() for key in self.asset_returns):
            raise ValueError("information-state asset IDs must be non-empty")
        if any(
            not math.isfinite(value) or value < -1.0
            for value in self.asset_returns.values()
        ):
            raise ValueError("information-state returns must be finite and at least minus one")
        _validate_provenance(
            evidence_class=self.evidence_class,
            source_record_ids=self.source_record_ids,
            sources=self.sources,
        )
        if any(not item.strip() for item in self.limitations):
            raise ValueError("information-state limitations must be non-empty")
        return self


class InformationStateResult(_StrictModel):
    state_id: str
    probability: float
    state_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    utility_with_information: float
    utility_without_information: float
    solution_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    weights_with_information: dict[str, float]
    cash_weight_with_information: float

    @model_validator(mode="after")
    def validate_state_result(self) -> InformationStateResult:
        numeric_values = (
            self.probability,
            self.utility_with_information,
            self.utility_without_information,
            *self.weights_with_information.values(),
            self.cash_weight_with_information,
        )
        if any(not math.isfinite(value) for value in numeric_values):
            raise ValueError("information-state result values must be finite")
        if not math.isclose(
            sum(self.weights_with_information.values())
            + self.cash_weight_with_information,
            1.0,
            abs_tol=NUMERICAL_TOLERANCE,
        ):
            raise ValueError("information-state allocation must sum to one")
        return self


class InformationValueResult(_StrictModel):
    problem_id: str
    input_states_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_state_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_results: tuple[InformationStateResult, ...] = Field(min_length=2)
    expected_utility_without_information: float
    expected_utility_with_perfect_information: float
    expected_value_of_perfect_information: float = Field(ge=0.0)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_information_value(self) -> InformationValueResult:
        probabilities = sum(item.probability for item in self.state_results)
        if not math.isclose(probabilities, 1.0, abs_tol=NUMERICAL_TOLERANCE):
            raise ValueError("information-state probabilities must sum to one")
        values = (
            self.expected_utility_without_information,
            self.expected_utility_with_perfect_information,
            self.expected_value_of_perfect_information,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("information-value metrics must be finite")
        implied = (
            self.expected_utility_with_perfect_information
            - self.expected_utility_without_information
        )
        if not math.isclose(
            self.expected_value_of_perfect_information,
            max(0.0, implied),
            abs_tol=NUMERICAL_TOLERANCE,
        ):
            raise ValueError("EVPI does not match utility difference")
        if _hash(_information_value_payload(self)) != self.result_sha256:
            raise ValueError("result_sha256 does not match information-value content")
        return self


class _CompiledProblem:
    def __init__(self, problem: AllocationProblem) -> None:
        self.problem = problem
        self.asset_ids = tuple(asset.asset_id for asset in problem.assets)
        self.n = len(problem.assets)
        self.cash_index = self.n
        self.turnover_start = self.n + 1
        self.cash_turnover_index = self.turnover_start + self.n
        self.worst_loss_index = self.cash_turnover_index + 1
        self.dimension = self.worst_loss_index + 1

    def build(
        self,
    ) -> tuple[
        FloatArray,
        FloatArray,
        FloatArray,
        FloatArray,
        FloatArray,
        list[tuple[float | None, float | None]],
    ]:
        problem = self.problem
        c: FloatArray = np.zeros(self.dimension, dtype=np.float64)
        for index, asset in enumerate(problem.assets):
            width = asset.expected_return_upper - asset.expected_return_lower
            c[index] = (
                -asset.expected_return_lower
                + problem.uncertainty_aversion * width
            )
            c[self.turnover_start + index] = asset.transaction_cost_bps / 10_000
        c[self.cash_index] = -problem.cash_return
        c[self.worst_loss_index] = problem.loss_aversion

        a_eq: FloatArray = np.zeros((1, self.dimension), dtype=np.float64)
        a_eq[0, : self.n + 1] = 1.0
        b_eq = np.array([1.0], dtype=np.float64)
        rows: list[FloatArray] = []
        rhs: list[float] = []

        for index, asset in enumerate(problem.assets):
            positive: FloatArray = np.zeros(self.dimension, dtype=np.float64)
            positive[index] = 1.0
            positive[self.turnover_start + index] = -1.0
            rows.append(positive)
            rhs.append(asset.current_weight)
            negative: FloatArray = np.zeros(self.dimension, dtype=np.float64)
            negative[index] = -1.0
            negative[self.turnover_start + index] = -1.0
            rows.append(negative)
            rhs.append(-asset.current_weight)

        cash_positive: FloatArray = np.zeros(self.dimension, dtype=np.float64)
        cash_positive[self.cash_index] = 1.0
        cash_positive[self.cash_turnover_index] = -1.0
        rows.append(cash_positive)
        rhs.append(problem.current_cash_weight)
        cash_negative: FloatArray = np.zeros(self.dimension, dtype=np.float64)
        cash_negative[self.cash_index] = -1.0
        cash_negative[self.cash_turnover_index] = -1.0
        rows.append(cash_negative)
        rhs.append(-problem.current_cash_weight)

        turnover: FloatArray = np.zeros(self.dimension, dtype=np.float64)
        turnover[self.turnover_start : self.cash_turnover_index + 1] = 1.0
        rows.append(turnover)
        rhs.append(2.0 * problem.max_one_way_turnover)

        for scenario in problem.risk_scenarios:
            row: FloatArray = np.zeros(self.dimension, dtype=np.float64)
            row[: self.n] = [
                scenario.loss_fraction_by_asset[asset_id] for asset_id in self.asset_ids
            ]
            row[self.worst_loss_index] = -1.0
            rows.append(row)
            rhs.append(0.0)

        for constraint in problem.linear_constraints:
            coefficients = np.array(
                [constraint.coefficients.get(asset_id, 0.0) for asset_id in self.asset_ids]
            )
            if constraint.upper_bound is not None:
                row = np.zeros(self.dimension, dtype=np.float64)
                row[: self.n] = coefficients
                rows.append(row)
                rhs.append(constraint.upper_bound)
            if constraint.lower_bound is not None:
                row = np.zeros(self.dimension, dtype=np.float64)
                row[: self.n] = -coefficients
                rows.append(row)
                rhs.append(-constraint.lower_bound)

        bounds: list[tuple[float | None, float | None]] = []
        for asset in problem.assets:
            capacity_weight = asset.capacity_usd / problem.total_capital_usd
            bounds.append((asset.min_weight, min(asset.max_weight, capacity_weight)))
        bounds.append((problem.cash_min_weight, problem.cash_max_weight))
        bounds.extend((0.0, 2.0) for _ in range(self.n + 1))
        bounds.append((0.0, problem.max_worst_case_loss))
        a_ub: FloatArray = (
            np.vstack(rows) if rows else np.empty((0, self.dimension), dtype=np.float64)
        )
        b_ub: FloatArray = np.asarray(rhs, dtype=np.float64)
        return c, a_ub, b_ub, a_eq, b_eq, bounds


class CapitalAllocator:
    """Solve robust long-only allocation and explain where the decision reverses."""

    def solve(self, problem: AllocationProblem) -> AllocationResult:
        input_hash = _hash(problem.model_dump(mode="json"))
        preflight = self._preflight(problem)
        if preflight:
            return self._failed_result(
                problem=problem,
                input_hash=input_hash,
                status=AllocationStatus.INFEASIBLE,
                solver_status=-1,
                solver_message="preflight infeasibility",
                reasons=preflight,
            )
        compiled = _CompiledProblem(problem)
        c, a_ub, b_ub, a_eq, b_eq, bounds = compiled.build()
        try:
            raw = linprog(
                c,
                A_ub=a_ub,
                b_ub=b_ub,
                A_eq=a_eq,
                b_eq=b_eq,
                bounds=bounds,
                method="highs",
                options={"presolve": True},
            )
        except Exception as error:
            return self._failed_result(
                problem=problem,
                input_hash=input_hash,
                status=AllocationStatus.SOLVER_ERROR,
                solver_status=-2,
                solver_message=f"{type(error).__name__}: {error}",
                reasons=("solver raised an exception; constraints were not relaxed",),
            )
        status = _solver_status(int(raw.status))
        iterations = int(raw.nit) if raw.nit is not None else None
        if status is not AllocationStatus.OPTIMAL:
            reason = (
                "HiGHS reported infeasible; original constraints were preserved"
                if status is AllocationStatus.INFEASIBLE
                else "HiGHS did not return an optimal allocation"
            )
            return self._failed_result(
                problem=problem,
                input_hash=input_hash,
                status=status,
                solver_status=int(raw.status),
                solver_message=str(raw.message),
                solver_iterations=iterations,
                reasons=(reason,),
            )
        if raw.x is None:
            return self._failed_result(
                problem=problem,
                input_hash=input_hash,
                status=AllocationStatus.SOLVER_ERROR,
                solver_status=int(raw.status),
                solver_message="solver returned optimal status without a solution vector",
                solver_iterations=iterations,
                reasons=("missing solution vector",),
            )
        vector = np.asarray(raw.x, dtype=np.float64)
        weights = {
            asset.asset_id: _clean(float(vector[index]))
            for index, asset in enumerate(problem.assets)
        }
        cash_weight = _clean(float(vector[compiled.cash_index]))
        metrics = self._metrics(problem, weights, cash_weight)
        slacks = self._constraint_slacks(problem, weights, cash_weight, metrics)
        if min(slacks.values(), default=0.0) < -NUMERICAL_TOLERANCE:
            return self._failed_result(
                problem=problem,
                input_hash=input_hash,
                status=AllocationStatus.SOLVER_ERROR,
                solver_status=int(raw.status),
                solver_message="solver vector failed post-solve constraint verification",
                solver_iterations=iterations,
                reasons=("post-solve constraint residual below tolerance",),
            )
        binding = tuple(
            sorted(
                key for key, value in slacks.items() if value <= NUMERICAL_TOLERANCE
            )
        )
        payload: dict[str, Any] = {
            "problem_id": problem.problem_id,
            "status": AllocationStatus.OPTIMAL.value,
            "weights": weights,
            "cash_weight": cash_weight,
            **metrics,
            "solver_status": int(raw.status),
            "solver_message": str(raw.message),
            "solver_iterations": iterations,
            "constraint_slacks": slacks,
            "binding_constraints": list(binding),
            "infeasibility_reasons": [],
            "input_sha256": input_hash,
            "limitations": [
                *problem.limitations,
                "The result is a model allocation under declared intervals, scenarios, costs, "
                "constraints and capital. It is not an order, realized return, or fiduciary "
                "advice.",
            ],
        }
        return AllocationResult(**payload, result_sha256=_hash(payload))

    def reversal_surface(
        self,
        problem: AllocationProblem,
        axes: tuple[SensitivityAxis, ...],
    ) -> ReversalSurface:
        if not 1 <= len(axes) <= 2:
            raise AllocationError("reversal surface requires one or two axes")
        axis_ids = [axis.axis_id for axis in axes]
        if len(axis_ids) != len(set(axis_ids)):
            raise AllocationError("reversal surface axis IDs must be unique")
        semantic_targets = [
            (axis.kind, axis.asset_id, axis.scenario_id) for axis in axes
        ]
        if len(semantic_targets) != len(set(semantic_targets)):
            raise AllocationError("reversal axes cannot perturb the same semantic target twice")
        self._validate_axes(problem, axes)
        baseline = self.solve(problem)
        points: list[ReversalPoint] = []
        for coordinates in itertools.product(*(axis.values for axis in axes)):
            perturbed = problem
            coordinate_map: dict[str, float] = {}
            for axis, value in zip(axes, coordinates, strict=True):
                perturbed = self._perturb(perturbed, axis, value)
                coordinate_map[axis.axis_id] = value
            result = self.solve(perturbed)
            leader = _leader(result.weights, result.cash_weight)
            points.append(
                ReversalPoint(
                    coordinates=coordinate_map,
                    status=result.status,
                    leader_asset_id=leader,
                    weights=result.weights,
                    cash_weight=result.cash_weight,
                    robust_utility=result.robust_utility,
                    result_sha256=result.result_sha256,
                )
            )
        leaders = {point.leader_asset_id for point in points if point.leader_asset_id is not None}
        reversals = _adjacent_reversals(points, axes)
        payload: dict[str, Any] = {
            "problem_id": problem.problem_id,
            "baseline_result_sha256": baseline.result_sha256,
            "axes": [axis.model_dump(mode="json") for axis in axes],
            "points": [point.model_dump(mode="json") for point in points],
            "decision_region_count": len(leaders),
            "adjacent_reversal_count": reversals,
            "limitations": [
                "This finite grid shows model-decision changes only at declared coordinates; it "
                "does not prove continuity, causal thresholds, or realized investment outcomes."
            ],
        }
        return ReversalSurface(**payload, surface_sha256=_hash(payload))

    def value_of_perfect_information(
        self,
        problem: AllocationProblem,
        states: tuple[InformationState, ...],
    ) -> InformationValueResult:
        if len(states) < 2:
            raise AllocationError("value of information requires at least two states")
        ordered_states = tuple(sorted(states, key=lambda state: state.state_id))
        probabilities = sum(state.probability for state in ordered_states)
        if not math.isclose(probabilities, 1.0, abs_tol=NUMERICAL_TOLERANCE):
            raise AllocationError("information-state probabilities must sum to one")
        asset_ids = {asset.asset_id for asset in problem.assets}
        state_ids = [state.state_id for state in ordered_states]
        if len(state_ids) != len(set(state_ids)):
            raise AllocationError("information-state IDs must be unique")
        if any(set(state.asset_returns) != asset_ids for state in ordered_states):
            raise AllocationError("every information state must cover every asset exactly once")
        for state in ordered_states:
            if state.available_at > problem.decision_time:
                raise AllocationError("information state was not available by decision_time")
            if any(
                source.temporal_coverage is TemporalCoverage.LATEST_ONLY
                and source.retrieved_at > problem.decision_time
                for source in state.sources
            ):
                raise AllocationError(
                    "latest_only information state cannot predate its retrieval"
                )
        states_hash = _hash(
            [state.model_dump(mode="json") for state in ordered_states]
        )
        expected_returns = {
            asset_id: sum(
                state.probability * state.asset_returns[asset_id]
                for state in ordered_states
            )
            for asset_id in asset_ids
        }
        expected_problem = self._exact_return_problem(
            problem,
            expected_returns,
            suffix="expected-information-state",
        )
        expected_solution = self.solve(expected_problem)
        if expected_solution.status is not AllocationStatus.OPTIMAL:
            raise AllocationError("expected-state allocation is not feasible")
        assert expected_solution.cash_weight is not None
        state_results: list[InformationStateResult] = []
        expected_without = 0.0
        expected_with = 0.0
        for state in ordered_states:
            state_problem = self._exact_return_problem(
                problem,
                state.asset_returns,
                suffix=f"information-{state.state_id}",
            )
            state_solution = self.solve(state_problem)
            if state_solution.status is not AllocationStatus.OPTIMAL:
                raise AllocationError(f"state allocation is not feasible: {state.state_id}")
            assert state_solution.cash_weight is not None
            utility_without = self._realized_utility(
                problem,
                expected_solution.weights,
                expected_solution.cash_weight,
                state.asset_returns,
            )
            utility_with = self._realized_utility(
                problem,
                state_solution.weights,
                state_solution.cash_weight,
                state.asset_returns,
            )
            expected_without += state.probability * utility_without
            expected_with += state.probability * utility_with
            state_results.append(
                InformationStateResult(
                    state_id=state.state_id,
                    probability=state.probability,
                    state_input_sha256=_hash(state.model_dump(mode="json")),
                    utility_with_information=_clean(utility_with),
                    utility_without_information=_clean(utility_without),
                    solution_result_sha256=state_solution.result_sha256,
                    weights_with_information=state_solution.weights,
                    cash_weight_with_information=state_solution.cash_weight,
                )
            )
        evpi = expected_with - expected_without
        if evpi < -NUMERICAL_TOLERANCE:
            raise AllocationError("computed perfect information value is materially negative")
        payload: dict[str, Any] = {
            "problem_id": problem.problem_id,
            "input_states_sha256": states_hash,
            "expected_state_result_sha256": expected_solution.result_sha256,
            "state_results": [item.model_dump(mode="json") for item in state_results],
            "expected_utility_without_information": _clean(expected_without),
            "expected_utility_with_perfect_information": _clean(expected_with),
            "expected_value_of_perfect_information": _clean(max(0.0, evpi)),
            "limitations": [
                "EVPI is a model upper bound for the declared discrete states, probabilities, "
                "constraints and utility. It is not the price of a real data product or a return."
            ],
        }
        return InformationValueResult(**payload, result_sha256=_hash(payload))

    @staticmethod
    def _preflight(problem: AllocationProblem) -> tuple[str, ...]:
        reasons: list[str] = []
        effective_maxima: list[float] = []
        for asset in problem.assets:
            capacity_weight = asset.capacity_usd / problem.total_capital_usd
            effective_max = min(asset.max_weight, capacity_weight)
            effective_maxima.append(effective_max)
            if effective_max + NUMERICAL_TOLERANCE < asset.min_weight:
                reasons.append(
                    f"asset {asset.asset_id} minimum exceeds its capital/capacity maximum"
                )
        minimum_total = sum(asset.min_weight for asset in problem.assets) + problem.cash_min_weight
        maximum_total = sum(effective_maxima) + problem.cash_max_weight
        if minimum_total > 1.0 + NUMERICAL_TOLERANCE:
            reasons.append("sum of minimum asset and cash weights exceeds one")
        if maximum_total < 1.0 - NUMERICAL_TOLERANCE:
            reasons.append("sum of capacity-adjusted maximum weights is below one")
        minimum_turnover = _minimum_turnover_to_bounds(problem, effective_maxima)
        if minimum_turnover > problem.max_one_way_turnover + NUMERICAL_TOLERANCE:
            reasons.append("turnover cap cannot reach the declared weight bounds")
        return tuple(reasons)

    @staticmethod
    def _metrics(
        problem: AllocationProblem,
        weights: dict[str, float],
        cash_weight: float,
    ) -> dict[str, Any]:
        lower = cash_weight * problem.cash_return + sum(
            weights[asset.asset_id] * asset.expected_return_lower for asset in problem.assets
        )
        upper = cash_weight * problem.cash_return + sum(
            weights[asset.asset_id] * asset.expected_return_upper for asset in problem.assets
        )
        uncertainty = problem.uncertainty_aversion * sum(
            weights[asset.asset_id]
            * (asset.expected_return_upper - asset.expected_return_lower)
            for asset in problem.assets
        )
        scenario_losses = {
            scenario.scenario_id: sum(
                weights[asset.asset_id]
                * scenario.loss_fraction_by_asset[asset.asset_id]
                for asset in problem.assets
            )
            for scenario in problem.risk_scenarios
        }
        worst_loss = max(scenario_losses.values())
        worst_ids = tuple(
            sorted(
                scenario_id
                for scenario_id, loss in scenario_losses.items()
                if math.isclose(loss, worst_loss, abs_tol=NUMERICAL_TOLERANCE)
            )
        )
        transaction_cost = sum(
            abs(weights[asset.asset_id] - asset.current_weight)
            * asset.transaction_cost_bps
            / 10_000
            for asset in problem.assets
        )
        turnover = 0.5 * (
            sum(
                abs(weights[asset.asset_id] - asset.current_weight)
                for asset in problem.assets
            )
            + abs(cash_weight - problem.current_cash_weight)
        )
        utility = (
            lower
            - uncertainty
            - problem.loss_aversion * worst_loss
            - transaction_cost
        )
        return {
            "expected_return_lower": _clean(lower),
            "expected_return_upper": _clean(upper),
            "uncertainty_penalty": _clean(uncertainty),
            "worst_case_loss": _clean(worst_loss),
            "worst_scenario_ids": list(worst_ids),
            "transaction_cost_fraction": _clean(transaction_cost),
            "one_way_turnover": _clean(turnover),
            "robust_utility": _clean(utility),
        }

    @staticmethod
    def _constraint_slacks(
        problem: AllocationProblem,
        weights: dict[str, float],
        cash_weight: float,
        metrics: dict[str, Any],
    ) -> dict[str, float]:
        slacks: dict[str, float] = {
            "budget": _clean(
                NUMERICAL_TOLERANCE
                - abs(sum(weights.values()) + cash_weight - 1.0)
            ),
            "cash:min": _clean(cash_weight - problem.cash_min_weight),
            "cash:max": _clean(problem.cash_max_weight - cash_weight),
            "turnover:max": _clean(
                problem.max_one_way_turnover - float(metrics["one_way_turnover"])
            ),
        }
        for asset in problem.assets:
            value = weights[asset.asset_id]
            effective_max = min(
                asset.max_weight,
                asset.capacity_usd / problem.total_capital_usd,
            )
            slacks[f"asset:{asset.asset_id}:min"] = _clean(value - asset.min_weight)
            slacks[f"asset:{asset.asset_id}:max"] = _clean(effective_max - value)
        if problem.max_worst_case_loss is not None:
            slacks["worst_case_loss:max"] = _clean(
                problem.max_worst_case_loss - float(metrics["worst_case_loss"])
            )
        for constraint in problem.linear_constraints:
            value = sum(
                coefficient * weights[asset_id]
                for asset_id, coefficient in constraint.coefficients.items()
            )
            if constraint.lower_bound is not None:
                slacks[f"linear:{constraint.constraint_id}:min"] = _clean(
                    value - constraint.lower_bound
                )
            if constraint.upper_bound is not None:
                slacks[f"linear:{constraint.constraint_id}:max"] = _clean(
                    constraint.upper_bound - value
                )
        return dict(sorted(slacks.items()))

    @staticmethod
    def _failed_result(
        *,
        problem: AllocationProblem,
        input_hash: str,
        status: AllocationStatus,
        solver_status: int,
        solver_message: str,
        reasons: tuple[str, ...],
        solver_iterations: int | None = None,
    ) -> AllocationResult:
        payload: dict[str, Any] = {
            "problem_id": problem.problem_id,
            "status": status.value,
            "weights": {},
            "cash_weight": None,
            "expected_return_lower": None,
            "expected_return_upper": None,
            "uncertainty_penalty": None,
            "worst_case_loss": None,
            "worst_scenario_ids": [],
            "transaction_cost_fraction": None,
            "one_way_turnover": None,
            "robust_utility": None,
            "solver_status": solver_status,
            "solver_message": solver_message,
            "solver_iterations": solver_iterations,
            "constraint_slacks": {},
            "binding_constraints": [],
            "infeasibility_reasons": list(reasons),
            "input_sha256": input_hash,
            "limitations": [
                *problem.limitations,
                "No candidate allocation is emitted when the original problem is not optimal; "
                "constraints are never silently relaxed."
            ],
        }
        return AllocationResult(**payload, result_sha256=_hash(payload))

    @staticmethod
    def _validate_axes(
        problem: AllocationProblem,
        axes: tuple[SensitivityAxis, ...],
    ) -> None:
        assets = {asset.asset_id for asset in problem.assets}
        scenarios = {scenario.scenario_id for scenario in problem.risk_scenarios}
        for axis in axes:
            if axis.asset_id is not None and axis.asset_id not in assets:
                raise AllocationError(f"unknown sensitivity asset: {axis.asset_id}")
            if axis.scenario_id is not None and axis.scenario_id not in scenarios:
                raise AllocationError(f"unknown sensitivity scenario: {axis.scenario_id}")
            if axis.kind in {
                SensitivityKind.EXPECTED_RETURN_LOWER,
                SensitivityKind.EXPECTED_RETURN_UPPER,
            } and any(value < -1.0 for value in axis.values):
                raise AllocationError("return sensitivity cannot fall below minus one")
            if axis.kind is SensitivityKind.CASH_RETURN and min(axis.values) < -1.0:
                raise AllocationError("cash-return sensitivity cannot fall below minus one")
            if axis.kind is SensitivityKind.TRANSACTION_COST_BPS and min(axis.values) < 0:
                raise AllocationError("transaction-cost sensitivity cannot be negative")
            if axis.kind is SensitivityKind.SCENARIO_LOSS and (
                min(axis.values) < 0 or max(axis.values) > 1
            ):
                raise AllocationError("scenario-loss sensitivity must lie between zero and one")
            if axis.kind in {
                SensitivityKind.LOSS_AVERSION,
                SensitivityKind.UNCERTAINTY_AVERSION,
            } and min(axis.values) < 0:
                raise AllocationError("aversion sensitivity cannot be negative")

    @staticmethod
    def _perturb(
        problem: AllocationProblem,
        axis: SensitivityAxis,
        value: float,
    ) -> AllocationProblem:
        if axis.kind is SensitivityKind.CASH_RETURN:
            return _validated_update(problem, {"cash_return": value})
        if axis.kind is SensitivityKind.LOSS_AVERSION:
            return _validated_update(problem, {"loss_aversion": value})
        if axis.kind is SensitivityKind.UNCERTAINTY_AVERSION:
            return _validated_update(problem, {"uncertainty_aversion": value})
        if axis.kind is SensitivityKind.SCENARIO_LOSS:
            scenarios = tuple(
                _validated_update(
                    scenario,
                    {
                        "loss_fraction_by_asset": {
                            **scenario.loss_fraction_by_asset,
                            str(axis.asset_id): value,
                        }
                    },
                )
                if scenario.scenario_id == axis.scenario_id
                else scenario
                for scenario in problem.risk_scenarios
            )
            return _validated_update(problem, {"risk_scenarios": scenarios})
        assets: list[AssetCandidate] = []
        for asset in problem.assets:
            if asset.asset_id != axis.asset_id:
                assets.append(asset)
                continue
            update: dict[str, float] = {axis.kind.value: value}
            try:
                updated = _validated_update(asset, update)
            except ValueError as error:
                raise AllocationError(
                    f"sensitivity point is invalid: {axis.axis_id}={value}"
                ) from error
            assets.append(updated)
        return _validated_update(problem, {"assets": tuple(assets)})

    @staticmethod
    def _exact_return_problem(
        problem: AllocationProblem,
        returns: dict[str, float],
        *,
        suffix: str,
    ) -> AllocationProblem:
        assets = tuple(
            _validated_update(
                asset,
                {
                    "expected_return_lower": returns[asset.asset_id],
                    "expected_return_upper": returns[asset.asset_id],
                    "evidence_class": EvidenceClass.SIMULATED,
                    "source_record_ids": (),
                    "sources": (),
                    "derivation": (
                        "Exact conditional return supplied by the discrete information-state "
                        "analysis; not an observed forecast."
                    ),
                },
            )
            for asset in problem.assets
        )
        return _validated_update(
            problem,
            {
                "problem_id": f"{problem.problem_id}:{suffix}"[:199],
                "assets": assets,
                "uncertainty_aversion": 0.0,
            },
        )

    @staticmethod
    def _realized_utility(
        problem: AllocationProblem,
        weights: dict[str, float],
        cash_weight: float,
        returns: dict[str, float],
    ) -> float:
        realized_return = cash_weight * problem.cash_return + sum(
            weights[asset.asset_id] * returns[asset.asset_id] for asset in problem.assets
        )
        scenario_losses = (
            sum(
                weights[asset.asset_id]
                * scenario.loss_fraction_by_asset[asset.asset_id]
                for asset in problem.assets
            )
            for scenario in problem.risk_scenarios
        )
        worst_loss = max(scenario_losses)
        transaction_cost = sum(
            abs(weights[asset.asset_id] - asset.current_weight)
            * asset.transaction_cost_bps
            / 10_000
            for asset in problem.assets
        )
        return realized_return - problem.loss_aversion * worst_loss - transaction_cost


def _minimum_turnover_to_bounds(
    problem: AllocationProblem,
    effective_maxima: list[float],
) -> float:
    changes = 0.0
    for asset, effective_max in zip(problem.assets, effective_maxima, strict=True):
        clipped = min(max(asset.current_weight, asset.min_weight), effective_max)
        changes += abs(clipped - asset.current_weight)
    clipped_cash = min(
        max(problem.current_cash_weight, problem.cash_min_weight),
        problem.cash_max_weight,
    )
    changes += abs(clipped_cash - problem.current_cash_weight)
    return 0.5 * changes


def _solver_status(status: int) -> AllocationStatus:
    return {
        0: AllocationStatus.OPTIMAL,
        1: AllocationStatus.SOLVER_LIMIT,
        2: AllocationStatus.INFEASIBLE,
        3: AllocationStatus.UNBOUNDED,
    }.get(status, AllocationStatus.SOLVER_ERROR)


def _leader(weights: dict[str, float], cash_weight: float | None) -> str | None:
    if cash_weight is None:
        return None
    candidates = {**weights, "cash": cash_weight}
    maximum = max(candidates.values())
    return min(
        key
        for key, value in candidates.items()
        if math.isclose(value, maximum, abs_tol=NUMERICAL_TOLERANCE)
    )


def _adjacent_reversals(
    points: list[ReversalPoint],
    axes: tuple[SensitivityAxis, ...],
) -> int:
    if len(axes) == 1:
        return sum(
            first.leader_asset_id != second.leader_asset_id
            for first, second in itertools.pairwise(points)
        )
    rows = len(axes[0].values)
    columns = len(axes[1].values)
    reversals = 0
    for row in range(rows):
        for column in range(columns):
            index = row * columns + column
            if column + 1 < columns:
                reversals += points[index].leader_asset_id != points[index + 1].leader_asset_id
            if row + 1 < rows:
                reversals += (
                    points[index].leader_asset_id
                    != points[index + columns].leader_asset_id
                )
    return reversals


def _validate_provenance(
    *,
    evidence_class: EvidenceClass,
    source_record_ids: tuple[str, ...],
    sources: tuple[SourceReference, ...],
) -> None:
    if any(not item.strip() for item in source_record_ids):
        raise ValueError("source_record_ids must be non-empty")
    if len(set(source_record_ids)) != len(source_record_ids):
        raise ValueError("source_record_ids must be unique")
    hashes = [source.sha256 for source in sources]
    if len(set(hashes)) != len(hashes):
        raise ValueError("sources must have unique content hashes")
    if evidence_class in {
        EvidenceClass.OBSERVED,
        EvidenceClass.REPORTED,
        EvidenceClass.EXTRACTED,
    } and (not sources or not source_record_ids):
        raise ValueError("observed, reported, and extracted evidence requires provenance")


def _validated_update(model: ModelT, update: dict[str, Any]) -> ModelT:
    values = model.model_dump(mode="python")
    values.update(update)
    return type(model).model_validate(values)


def _result_payload(result: AllocationResult) -> dict[str, Any]:
    return result.model_dump(mode="json", exclude={"result_sha256"})


def _surface_payload(surface: ReversalSurface) -> dict[str, Any]:
    return surface.model_dump(mode="json", exclude={"surface_sha256"})


def _information_value_payload(result: InformationValueResult) -> dict[str, Any]:
    return result.model_dump(mode="json", exclude={"result_sha256"})


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _clean(value: float) -> float:
    if abs(value) < 10 ** (-HASH_DECIMALS):
        return 0.0
    return round(value, HASH_DECIMALS)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    value.astimezone(UTC)
