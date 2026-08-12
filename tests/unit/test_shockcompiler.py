from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import HttpUrl, ValidationError

from finreplay.contracts import (
    EvidenceClass,
    LicenseClass,
    ScenarioMode,
    SourceReference,
    TemporalCoverage,
)
from finreplay.engines import (
    ShockCompilationError,
    ShockCompiler,
    ShockOperation,
    ShockParameter,
    ShockProgram,
)

DECISION = datetime(2023, 3, 8, 18, tzinfo=UTC)


def source(digit: str = "1") -> SourceReference:
    return SourceReference(
        source_id="sec.xbrl.companyfacts",
        publisher="U.S. Securities and Exchange Commission",
        url=HttpUrl("https://data.sec.gov/api/xbrl/companyfacts/CIK0000719739.json"),
        retrieved_at=datetime(2026, 8, 12, tzinfo=UTC),
        source_version=f"fixture-{digit}",
        sha256=digit * 64,
        license_class=LicenseClass.REDISTRIBUTABLE,
        temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
        vintage_as_of=datetime(2026, 8, 12, tzinfo=UTC),
        redistribution_note="Fixture only.",
    )


def parameter(
    parameter_id: str = "htm-loss-fraction",
    *,
    lower: float = 0.1,
    upper: float = 0.1,
    grid_points: int = 1,
    evidence: EvidenceClass = EvidenceClass.REPORTED,
    sources: tuple[SourceReference, ...] = (source(),),
    source_ids: tuple[str, ...] = ("sec-fact-htm-loss",),
    operation: ShockOperation = ShockOperation.SET,
) -> ShockParameter:
    return ShockParameter(
        parameter_id=parameter_id,
        target_id="security:svb-htm-portfolio",
        variable="loss_fraction",
        unit="fraction",
        operation=operation,
        lower=lower,
        upper=upper,
        grid_points=grid_points,
        evidence_class=evidence,
        source_record_ids=source_ids,
        sources=sources,
        derivation="Fixture maps reported loss to portfolio carrying value.",
        limitations=("The ratio is descriptive and not a causal price forecast.",),
    )


def program(
    mode: ScenarioMode,
    parameters: tuple[ShockParameter, ...],
) -> ShockProgram:
    return ShockProgram(
        program_id=f"svb-{mode.value.replace('_', '-')}",
        scenario_id="svb-2023-boundary",
        mode=mode,
        decision_time=DECISION,
        parameters=parameters,
        hypothesis="Compile an evidence-labelled shock vector without changing its truth status.",
        global_limitations=("Compiled shocks are research inputs, not observed outcomes.",),
    )


def test_observed_exact_program_compiles_deterministically_with_full_provenance() -> None:
    value = program(ScenarioMode.OBSERVED_RECONSTRUCTION, (parameter(),))
    compiler = ShockCompiler()
    first = compiler.compile(value)
    second = compiler.compile(value)
    assert first == second
    assert len(first.trials) == 1
    shock = first.trials[0].shocks[0]
    assert shock.value == pytest.approx(0.1)
    assert shock.evidence_class is EvidenceClass.REPORTED
    assert shock.source_hashes == ("1" * 64,)
    assert shock.source_record_ids == ("sec-fact-htm-loss",)


def test_bounded_reconstruction_expands_endpoints_not_false_midpoint_precision() -> None:
    bounded = parameter(lower=0.08, upper=0.15, grid_points=2, evidence=EvidenceClass.INFERRED)
    compiled = ShockCompiler().compile(
        program(ScenarioMode.BOUNDED_RECONSTRUCTION, (bounded,))
    )
    assert [trial.shocks[0].value for trial in compiled.trials] == [0.08, 0.15]
    assert all(
        trial.shocks[0].evidence_class is EvidenceClass.INFERRED
        for trial in compiled.trials
    )


def test_counterfactual_requires_one_explicit_simulated_value() -> None:
    counterfactual = parameter(
        lower=0.25,
        upper=0.25,
        evidence=EvidenceClass.SIMULATED,
        sources=(),
        source_ids=(),
    )
    compiled = ShockCompiler().compile(
        program(ScenarioMode.COUNTERFACTUAL, (counterfactual,))
    )
    assert compiled.trials[0].shocks[0].value == 0.25
    assert compiled.trials[0].shocks[0].evidence_class is EvidenceClass.SIMULATED


def test_adversarial_cartesian_grid_is_deterministic_and_bounded() -> None:
    first = parameter(
        lower=0,
        upper=1,
        grid_points=3,
        evidence=EvidenceClass.SIMULATED,
        sources=(),
        source_ids=(),
    )
    second = parameter(
        "deposit-run-fraction",
        lower=0,
        upper=0.5,
        grid_points=2,
        evidence=EvidenceClass.SIMULATED,
        sources=(),
        source_ids=(),
    ).model_copy(
        update={
            "target_id": "issuer:svb-financial-group",
            "variable": "deposit_run_fraction",
        }
    )
    compiled = ShockCompiler(max_trials=6).compile(
        program(ScenarioMode.ADVERSARIAL, (first, second))
    )
    assert len(compiled.trials) == 6
    vectors = [tuple(shock.value for shock in trial.shocks) for trial in compiled.trials]
    assert vectors == [
        (0.0, 0.0),
        (0.0, 0.5),
        (0.5, 0.0),
        (0.5, 0.5),
        (1.0, 0.0),
        (1.0, 0.5),
    ]
    assert len({trial.vector_sha256 for trial in compiled.trials}) == 6


def test_grid_explosion_fails_before_materialization() -> None:
    parameters = tuple(
        parameter(
            f"parameter-{index}",
            lower=0,
            upper=1,
            grid_points=10,
            evidence=EvidenceClass.SIMULATED,
            sources=(),
            source_ids=(),
        ).model_copy(
            update={"target_id": f"target-{index}", "variable": f"value_{index}"}
        )
        for index in range(6)
    )
    with pytest.raises(ShockCompilationError, match="above max_trials"):
        ShockCompiler(max_trials=1_000).compile(
            program(ScenarioMode.ADVERSARIAL, parameters)
        )


@pytest.mark.parametrize(
    ("mode", "item", "match"),
    [
        (
            ScenarioMode.OBSERVED_RECONSTRUCTION,
            parameter(lower=0.1, upper=0.2, grid_points=2),
            "must be exact",
        ),
        (
            ScenarioMode.OBSERVED_RECONSTRUCTION,
            parameter(evidence=EvidenceClass.SIMULATED, sources=(), source_ids=()),
            "sourced evidence",
        ),
        (
            ScenarioMode.BOUNDED_RECONSTRUCTION,
            parameter(evidence=EvidenceClass.SIMULATED, sources=(), source_ids=()),
            "cannot use simulated",
        ),
        (
            ScenarioMode.BOUNDED_RECONSTRUCTION,
            parameter(evidence=EvidenceClass.INFERRED, sources=(), source_ids=()),
            "requires source provenance",
        ),
        (
            ScenarioMode.COUNTERFACTUAL,
            parameter(),
            "labelled simulated",
        ),
        (
            ScenarioMode.ADVERSARIAL,
            parameter(evidence=EvidenceClass.SIMULATED, sources=(), source_ids=()),
            "at least two grid points",
        ),
    ],
)
def test_modes_cannot_be_mislabelled(mode: ScenarioMode, item: ShockParameter, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        program(mode, (item,))


def test_parameter_and_program_contracts_fail_on_invalid_bounds_or_duplicate_targets() -> None:
    with pytest.raises(ValidationError, match="below lower"):
        parameter(lower=2, upper=1)
    with pytest.raises(ValidationError, match="finite"):
        parameter(lower=float("nan"), upper=1)
    duplicate = parameter("different-id")
    with pytest.raises(ValidationError, match="same target variable"):
        program(ScenarioMode.OBSERVED_RECONSTRUCTION, (parameter(), duplicate))
    naive = DECISION.replace(tzinfo=None)
    value = program(ScenarioMode.OBSERVED_RECONSTRUCTION, (parameter(),)).model_dump()
    value["decision_time"] = naive
    with pytest.raises(ValidationError, match="timezone-aware"):
        ShockProgram.model_validate(value)


def test_apply_supports_set_add_multiply_without_mutating_baseline() -> None:
    parameters = (
        parameter(operation=ShockOperation.SET).model_copy(
            update={"target_id": "a", "variable": "set_value", "lower": 2.0, "upper": 2.0}
        ),
        parameter("add-value", operation=ShockOperation.ADD).model_copy(
            update={"target_id": "a", "variable": "add_value", "lower": 3.0, "upper": 3.0}
        ),
        parameter("multiply-value", operation=ShockOperation.MULTIPLY).model_copy(
            update={
                "target_id": "a",
                "variable": "multiply_value",
                "lower": 4.0,
                "upper": 4.0,
            }
        ),
    )
    compiled = ShockCompiler().compile(
        program(ScenarioMode.OBSERVED_RECONSTRUCTION, parameters)
    )
    baseline = {("a", "add_value"): 10.0, ("a", "multiply_value"): 5.0}
    result = ShockCompiler.apply(baseline, compiled.trials[0])
    assert result == {
        ("a", "set_value"): 2.0,
        ("a", "add_value"): 13.0,
        ("a", "multiply_value"): 20.0,
    }
    assert baseline == {("a", "add_value"): 10.0, ("a", "multiply_value"): 5.0}

    missing = dict(compiled.trials[0].model_dump())
    add_only = compiled.trials[0].model_copy(update={"shocks": (compiled.trials[0].shocks[1],)})
    assert missing
    with pytest.raises(ShockCompilationError, match="requires baseline"):
        ShockCompiler.apply({}, add_only)
