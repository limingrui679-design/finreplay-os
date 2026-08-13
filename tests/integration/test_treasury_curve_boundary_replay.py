from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import ArtifactStatus, TemporalCoverage
from finreplay.engines import EngineName, ReplayPackSpec, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    TreasuryCurveBoundaryInputLock,
    build_treasury_curve_boundary_replay_spec,
)

LOCK_PATH = Path("scenarios/treasury-curve-2023/input-lock.json")
EVENT_PATH = Path("scenarios/treasury-curve-2023/event-lock.json")
CODE_COMMIT = "e" * 40


@pytest.fixture(scope="module")
def lock() -> TreasuryCurveBoundaryInputLock:
    return TreasuryCurveBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def event_lock() -> OfficialEventLock:
    return OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec(lock: TreasuryCurveBoundaryInputLock) -> ReplayPackSpec:
    return build_treasury_curve_boundary_replay_spec(lock, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_yield_inputs_and_post_decision_pair_are_exact_and_disjoint(
    lock: TreasuryCurveBoundaryInputLock,
    event_lock: OfficialEventLock,
) -> None:
    assert len(lock.records) == 4
    assert all(
        record.source.temporal_coverage is TemporalCoverage.VINTAGE_NATIVE
        and record.interval.available_at <= lock.decision_time
        for record in lock.records
    )
    by_id = {record.record_id: record for record in lock.records}
    assert by_id[lock.roles.march08_two_year].payload["value_basis_points"] == 505
    assert by_id[lock.roles.march08_ten_year].payload["value_basis_points"] == 398
    assert by_id[lock.roles.march13_two_year].payload["value_basis_points"] == 403
    assert by_id[lock.roles.march13_ten_year].payload["value_basis_points"] == 355

    assert len(event_lock.records) == 2
    event_by_series = {
        str(record.payload["series_id"]): record for record in event_lock.records
    }
    assert event_by_series["DGS2"].payload["value_basis_points"] == 393
    assert event_by_series["DGS10"].payload["value_basis_points"] == 351
    assert all(
        record.interval.available_at > lock.decision_time for record in event_lock.records
    )
    assert not (
        {record.record_id for record in event_lock.records}
        & {record.record_id for record in lock.records}
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("future", "publication time mismatch"),
        ("latest_only", "native ALFRED vintages"),
        ("observed", "must remain reported"),
        ("wrong_series", "series mismatch"),
        ("wrong_observation", "observation-date mismatch"),
        ("wrong_vintage", "vintage-date mismatch"),
        ("wrong_maturity", "maturity mismatch"),
        ("wrong_availability_method", "availability method mismatch"),
        ("wrong_unit", "unit mismatch"),
        ("out_of_range", "yield is outside"),
    ],
)
def test_curve_lock_rejects_temporal_source_and_value_inflation(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "future":
        future = (
            TreasuryCurveBoundaryInputLock.model_validate(values).decision_time
            + timedelta(seconds=1)
        ).isoformat()
        first["interval"]["published_at"] = future
        first["interval"]["available_at"] = future
    elif case == "latest_only":
        first["source"]["temporal_coverage"] = "latest_only"
        first["source"]["vintage_as_of"] = None
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_series":
        first["payload"]["series_id"] = "DGS30"
    elif case == "wrong_observation":
        first["payload"]["observation_date"] = "2023-03-07"
    elif case == "wrong_vintage":
        first["payload"]["vintage_date"] = "2023-03-10"
    elif case == "wrong_maturity":
        first["payload"]["maturity_years"] = 30
    elif case == "wrong_availability_method":
        first["payload"]["availability_method"] = "date_only"
    elif case == "wrong_unit":
        first["payload"]["unit"] = "Percent"
    elif case == "out_of_range":
        first["payload"]["value_basis_points"] = 10_001
    with pytest.raises(ValidationError, match=message):
        TreasuryCurveBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_curve_lock_rejects_role_and_self_hash_tamper() -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["roles"]["march08_ten_year"] = values["roles"]["march08_two_year"]
    with pytest.raises(ValidationError, match="must be unique"):
        TreasuryCurveBoundaryInputLock.model_validate(values)

    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["title"] = "Fabricated Treasury-curve boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        TreasuryCurveBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_curve_boundary_runs_relevant_engines_and_preserves_failure_boundary(
    spec: ReplayPackSpec,
) -> None:
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}
    assert set(artifacts) == {
        EngineName.TIMEVAULT,
        EngineName.SHOCKCOMPILER,
        EngineName.TRIALCOURT,
        EngineName.REPLAYSTUDIO,
    }
    assert all(artifact.status is ArtifactStatus.REPRODUCED for artifact in artifacts.values())
    assert spec.require_all_engines is False
    assert spec.distinct_input_records == 4
    assert spec.derived_records == 6
    assert all(artifact.payload.get("input_lock_sha256") for artifact in artifacts.values())

    shock = artifacts[EngineName.SHOCKCOMPILER].payload
    assert shock["known_yields_and_derived_spreads"] == {
        "march08_two_year_basis_points": 505,
        "march08_ten_year_basis_points": 398,
        "march08_spread_basis_points": -107,
        "march13_two_year_basis_points": 403,
        "march13_ten_year_basis_points": 355,
        "march13_spread_basis_points": -48,
        "spread_lower_basis_points": -107,
        "spread_upper_basis_points": -48,
        "spread_range_width": 59,
    }
    assert shock["naive_baseline"] == {
        "next_dgs10_minus_dgs2_spread_basis_points": -48,
        "definition": "persistence of the latest known derived DGS10-minus-DGS2 spread",
    }
    assert shock["bound_construction"] == {
        "lower_spread_basis_points": -107,
        "upper_spread_basis_points": -48,
        "range_width_basis_points": 59,
        "endpoint_method": "minimum_and_maximum_of_two_known_derived_spreads",
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock["applied_endpoints"] == {"lower": -107.0, "upper": -48.0}
    assert len(shock["compiled"]["trials"]) == 2

    trial = artifacts[EngineName.TRIALCOURT].payload
    assert trial["decision"]["disposition"] == "reject"
    assert len(trial["decision"]["findings"]) == 6
    assert trial["manifest"]["rejected_decisions"] == 1


@pytest.mark.integration
def test_curve_boundary_pack_builds_byte_identically(
    spec: ReplayPackSpec,
    tmp_path: Path,
) -> None:
    studio = ReplayStudio()
    compiled = studio.compile(spec)
    first = studio.build(spec, tmp_path / "first").root
    second = studio.build(spec, tmp_path / "second").root
    assert compiled.source_set_historical_replay_eligible is True
    assert compiled.contains_simulation is True
    assert compiled.topological_artifact_ids[0] == "treasury-curve.timevault.vintage-query"
    assert compiled.topological_artifact_ids[-1] == "treasury-curve.replaystudio.render"
    assert _file_map(first) == _file_map(second)


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
