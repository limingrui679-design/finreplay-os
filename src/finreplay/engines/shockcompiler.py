"""Evidence-aware compiler for observed, bounded, counterfactual, and adversarial shocks."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finreplay.contracts import EvidenceClass, ScenarioMode, SourceReference


class ShockCompilationError(RuntimeError):
    """Raised when a shock program is unsafe, inconsistent, or too large to compile."""


class ShockOperation(StrEnum):
    SET = "set"
    ADD = "add"
    MULTIPLY = "multiply"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ShockParameter(_StrictModel):
    """One input dimension with explicit value bounds and evidence provenance."""

    parameter_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    target_id: str = Field(min_length=1, max_length=200)
    variable: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    unit: str = Field(min_length=1, max_length=60)
    operation: ShockOperation
    lower: float
    upper: float
    grid_points: int = Field(default=1, ge=1, le=1_000)
    evidence_class: EvidenceClass
    source_record_ids: tuple[str, ...]
    sources: tuple[SourceReference, ...]
    derivation: str = Field(min_length=10, max_length=2000)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_parameter(self) -> ShockParameter:
        if not math.isfinite(self.lower) or not math.isfinite(self.upper):
            raise ValueError("shock bounds must be finite")
        if self.upper < self.lower:
            raise ValueError("shock upper bound must not be below lower bound")
        if any(not item.strip() for item in self.source_record_ids):
            raise ValueError("source_record_ids must be non-empty identifiers")
        if any(not item.strip() for item in self.limitations):
            raise ValueError("limitations must be non-empty")
        return self


class ShockProgram(_StrictModel):
    """A mode-specific, temporally anchored shock specification before expansion."""

    program_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    mode: ScenarioMode
    decision_time: datetime
    parameters: tuple[ShockParameter, ...] = Field(min_length=1)
    hypothesis: str = Field(min_length=20, max_length=4000)
    global_limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_program(self) -> ShockProgram:
        _require_aware(self.decision_time, "decision_time")
        ids = [parameter.parameter_id for parameter in self.parameters]
        if len(ids) != len(set(ids)):
            raise ValueError("shock parameter IDs must be unique")
        target_variables = [
            (parameter.target_id, parameter.variable) for parameter in self.parameters
        ]
        if len(target_variables) != len(set(target_variables)):
            raise ValueError("one program cannot assign the same target variable twice")
        self._validate_mode_contract()
        return self

    def _validate_mode_contract(self) -> None:
        sourced_classes = {
            EvidenceClass.OBSERVED,
            EvidenceClass.REPORTED,
            EvidenceClass.EXTRACTED,
        }
        for parameter in self.parameters:
            if self.mode is ScenarioMode.OBSERVED_RECONSTRUCTION:
                if parameter.evidence_class not in sourced_classes:
                    raise ValueError("observed reconstruction requires sourced evidence")
                if parameter.lower != parameter.upper or parameter.grid_points != 1:
                    raise ValueError("observed reconstruction parameters must be exact")
                if not parameter.sources or not parameter.source_record_ids:
                    raise ValueError("observed reconstruction requires source provenance")
            elif self.mode is ScenarioMode.BOUNDED_RECONSTRUCTION:
                if parameter.evidence_class is EvidenceClass.SIMULATED:
                    raise ValueError("bounded reconstruction cannot use simulated evidence")
                if not parameter.sources and not parameter.source_record_ids:
                    raise ValueError("bounded reconstruction requires source provenance")
                if parameter.grid_points not in {1, 2}:
                    raise ValueError("bounded reconstruction compiles only interval endpoints")
            elif self.mode is ScenarioMode.COUNTERFACTUAL:
                if parameter.evidence_class is not EvidenceClass.SIMULATED:
                    raise ValueError("counterfactual parameters must be labelled simulated")
                if parameter.grid_points != 1 or parameter.lower != parameter.upper:
                    raise ValueError("counterfactual parameters must specify one explicit value")
            elif self.mode is ScenarioMode.ADVERSARIAL:
                if parameter.evidence_class is not EvidenceClass.SIMULATED:
                    raise ValueError("adversarial parameters must be labelled simulated")
                if parameter.grid_points < 2:
                    raise ValueError("adversarial parameters require at least two grid points")


class CompiledShock(_StrictModel):
    parameter_id: str
    target_id: str
    variable: str
    unit: str
    operation: ShockOperation
    value: float
    evidence_class: EvidenceClass
    source_record_ids: tuple[str, ...]
    source_hashes: tuple[str, ...]
    derivation: str
    limitations: tuple[str, ...]


class ShockTrial(_StrictModel):
    trial_index: int = Field(ge=0)
    trial_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,239}$")
    shocks: tuple[CompiledShock, ...] = Field(min_length=1)
    vector_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompiledShockProgram(_StrictModel):
    program_id: str
    scenario_id: str
    mode: ScenarioMode
    decision_time: datetime
    source_program_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trials: tuple[ShockTrial, ...] = Field(min_length=1)
    compiled_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_compiled(self) -> CompiledShockProgram:
        _require_aware(self.decision_time, "decision_time")
        if tuple(trial.trial_index for trial in self.trials) != tuple(range(len(self.trials))):
            raise ValueError("compiled trial indexes must be contiguous")
        return self


class ShockCompiler:
    """Deterministically expand a validated program while retaining every provenance field."""

    def __init__(self, *, max_trials: int = 100_000) -> None:
        if max_trials <= 0:
            raise ValueError("max_trials must be positive")
        self.max_trials = max_trials

    def compile(self, program: ShockProgram) -> CompiledShockProgram:
        grids = [self._grid(parameter, mode=program.mode) for parameter in program.parameters]
        trial_count = math.prod(len(grid) for grid in grids)
        if trial_count > self.max_trials:
            raise ShockCompilationError(
                f"shock grid expands to {trial_count} trials, above max_trials={self.max_trials}"
            )
        program_payload = program.model_dump(mode="json")
        program_hash = _hash(program_payload)
        trials: list[ShockTrial] = []
        for trial_index, values in enumerate(itertools.product(*grids)):
            shocks = tuple(
                self._compiled_shock(parameter, value)
                for parameter, value in zip(program.parameters, values, strict=True)
            )
            vector_payload = [shock.model_dump(mode="json") for shock in shocks]
            vector_hash = _hash(vector_payload)
            trials.append(
                ShockTrial(
                    trial_index=trial_index,
                    trial_id=f"{program.program_id}:{trial_index:08d}:{vector_hash[:16]}",
                    shocks=shocks,
                    vector_sha256=vector_hash,
                )
            )
        compiled_payload: dict[str, Any] = {
            "program_id": program.program_id,
            "scenario_id": program.scenario_id,
            "mode": program.mode.value,
            "decision_time": program.decision_time.isoformat(),
            "source_program_sha256": program_hash,
            "trials": [trial.model_dump(mode="json") for trial in trials],
            "limitations": list(program.global_limitations),
        }
        compiled_hash = _hash(compiled_payload)
        return CompiledShockProgram(
            program_id=program.program_id,
            scenario_id=program.scenario_id,
            mode=program.mode,
            decision_time=program.decision_time,
            source_program_sha256=program_hash,
            trials=tuple(trials),
            compiled_sha256=compiled_hash,
            limitations=program.global_limitations,
        )

    @staticmethod
    def apply(
        values: dict[tuple[str, str], float],
        trial: ShockTrial,
    ) -> dict[tuple[str, str], float]:
        """Apply a compiled vector to an explicit baseline without mutating the baseline."""

        result = dict(values)
        for shock in trial.shocks:
            key = (shock.target_id, shock.variable)
            if shock.operation is ShockOperation.SET:
                result[key] = shock.value
            else:
                if key not in result:
                    raise ShockCompilationError(
                        f"operation {shock.operation.value} requires baseline for {key}"
                    )
                if shock.operation is ShockOperation.ADD:
                    result[key] += shock.value
                else:
                    result[key] *= shock.value
            if not math.isfinite(result[key]):
                raise ShockCompilationError(f"shock operation produced non-finite value for {key}")
        return result

    @staticmethod
    def _grid(parameter: ShockParameter, *, mode: ScenarioMode) -> tuple[float, ...]:
        if mode is ScenarioMode.BOUNDED_RECONSTRUCTION and parameter.lower != parameter.upper:
            return (parameter.lower, parameter.upper)
        if parameter.grid_points == 1:
            return (parameter.lower,)
        step = (parameter.upper - parameter.lower) / (parameter.grid_points - 1)
        values = tuple(
            parameter.lower + step * index for index in range(parameter.grid_points)
        )
        # Assign exact declared endpoints to avoid cumulative floating-point drift.
        return (*values[:-1], parameter.upper)

    @staticmethod
    def _compiled_shock(parameter: ShockParameter, value: float) -> CompiledShock:
        return CompiledShock(
            parameter_id=parameter.parameter_id,
            target_id=parameter.target_id,
            variable=parameter.variable,
            unit=parameter.unit,
            operation=parameter.operation,
            value=value,
            evidence_class=parameter.evidence_class,
            source_record_ids=parameter.source_record_ids,
            source_hashes=tuple(source.sha256 for source in parameter.sources),
            derivation=parameter.derivation,
            limitations=parameter.limitations,
        )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
