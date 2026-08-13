from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import ArtifactStatus, TemporalCoverage
from finreplay.engines import EngineName, ReplayPackSpec, ReplayStudio
from finreplay.scenarios import (
    FOMCTargetBoundaryInputLock,
    OfficialEventLock,
    build_fomc_target_boundary_replay_spec,
)

LOCK_PATH = Path("scenarios/fomc-target-2023/input-lock.json")
EVENT_PATH = Path("scenarios/fomc-target-2023/event-lock.json")
CODE_COMMIT = "f" * 40


@pytest.fixture(scope="module")
def lock() -> FOMCTargetBoundaryInputLock:
    return FOMCTargetBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def event_lock() -> OfficialEventLock:
    return OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec(lock: FOMCTargetBoundaryInputLock) -> ReplayPackSpec:
    return build_fomc_target_boundary_replay_spec(lock, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_fomc_inputs_and_post_decision_event_are_exact_and_disjoint(
    lock: FOMCTargetBoundaryInputLock,
    event_lock: OfficialEventLock,
) -> None:
    assert len(lock.records) == 4
    assert all(
        record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
        and record.interval.available_at <= lock.decision_time
        for record in lock.records
    )
    by_id = {record.record_id: record for record in lock.records}
    assert by_id[lock.roles.february_lower].payload["value_basis_points"] == 450
    assert by_id[lock.roles.february_upper].payload["value_basis_points"] == 475
    assert by_id[lock.roles.march_lower].payload["value_basis_points"] == 475
    assert by_id[lock.roles.march_upper].payload["value_basis_points"] == 500
    assert event_lock.records[0].payload["value_basis_points"] == 525
    assert event_lock.records[0].interval.available_at > lock.decision_time
    assert event_lock.records[0].record_id not in {record.record_id for record in lock.records}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("future", "publication time mismatch"),
        ("latest_only", "versioned release snapshots"),
        ("observed", "must remain reported"),
        ("wrong_release", "release mismatch"),
        ("wrong_metric", "metric mismatch"),
        ("wrong_availability_method", "availability method mismatch"),
        ("wrong_policy", "policy mismatch"),
        ("wrong_unit", "unit mismatch"),
        ("wrong_width", "range width mismatch"),
        ("wrong_value", "value mismatch"),
    ],
)
def test_fomc_lock_rejects_temporal_source_and_value_inflation(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "future":
        future = (
            FOMCTargetBoundaryInputLock.model_validate(values).decision_time
            + timedelta(seconds=1)
        ).isoformat()
        first["interval"]["published_at"] = future
        first["interval"]["available_at"] = future
    elif case == "latest_only":
        first["source"]["temporal_coverage"] = "latest_only"
        first["source"]["vintage_as_of"] = None
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_release":
        first["payload"]["release_date"] = "2023-02-02"
    elif case == "wrong_metric":
        first["payload"]["metric"] = "other"
    elif case == "wrong_availability_method":
        first["payload"]["availability_method"] = "date_only"
    elif case == "wrong_policy":
        first["payload"]["policy"] = "Other policy"
    elif case == "wrong_unit":
        first["payload"]["unit"] = "Percent"
    elif case == "wrong_width":
        first["payload"]["range_width_basis_points"] = 50
    elif case == "wrong_value":
        first["payload"]["value_basis_points"] = 0
    with pytest.raises(ValidationError, match=message):
        FOMCTargetBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_fomc_lock_rejects_role_and_self_hash_tamper() -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["roles"]["february_upper"] = values["roles"]["february_lower"]
    with pytest.raises(ValidationError, match="must be unique"):
        FOMCTargetBoundaryInputLock.model_validate(values)

    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["title"] = "Fabricated FOMC target-range boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        FOMCTargetBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_fomc_target_boundary_runs_relevant_engines_and_preserves_boundaries(
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
    assert shock["known_target_ranges"] == {
        "february_lower_basis_points": 450,
        "february_upper_basis_points": 475,
        "march_lower_basis_points": 475,
        "march_upper_basis_points": 500,
        "known_lower_step_basis_points": 25,
        "known_upper_step_basis_points": 25,
        "next_upper_lower_basis_points": 500,
        "next_upper_upper_basis_points": 525,
    }
    assert shock["naive_baseline"] == {
        "next_target_range_upper_basis_points": 500,
        "definition": "persistence of the latest known upper target endpoint",
    }
    assert shock["bound_construction"] == {
        "lower_next_upper_basis_points": 500,
        "upper_next_upper_basis_points": 525,
        "known_step_basis_points": 25,
        "endpoint_method": "zero_or_one_continuation_of_known_upper_endpoint_step",
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock["applied_endpoints"] == {"lower": 500.0, "upper": 525.0}
    assert len(shock["compiled"]["trials"]) == 2

    trial = artifacts[EngineName.TRIALCOURT].payload
    assert trial["decision"]["disposition"] == "reject"
    assert len(trial["decision"]["findings"]) == 6
    assert trial["manifest"]["rejected_decisions"] == 1


@pytest.mark.integration
def test_fomc_target_boundary_pack_builds_byte_identically(
    spec: ReplayPackSpec,
    tmp_path: Path,
) -> None:
    studio = ReplayStudio()
    compiled = studio.compile(spec)
    first = studio.build(spec, tmp_path / "first").root
    second = studio.build(spec, tmp_path / "second").root
    assert compiled.source_set_historical_replay_eligible is True
    assert compiled.contains_simulation is True
    assert compiled.topological_artifact_ids[0] == "fomc-target.timevault.release-query"
    assert compiled.topological_artifact_ids[-1] == "fomc-target.replaystudio.render"
    assert _file_map(first) == _file_map(second)


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
