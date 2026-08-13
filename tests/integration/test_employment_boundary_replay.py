from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import ArtifactStatus, TemporalCoverage
from finreplay.engines import EngineName, ReplayPackSpec, ReplayStudio
from finreplay.scenarios import (
    EmploymentBoundaryInputLock,
    OfficialEventLock,
    build_employment_boundary_replay_spec,
)

LOCK_PATH = Path("scenarios/bls-payroll-2023/input-lock.json")
EVENT_PATH = Path("scenarios/bls-payroll-2023/event-lock.json")
CODE_COMMIT = "e" * 40


@pytest.fixture(scope="module")
def lock() -> EmploymentBoundaryInputLock:
    return EmploymentBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def event_lock() -> OfficialEventLock:
    return OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec(lock: EmploymentBoundaryInputLock) -> ReplayPackSpec:
    return build_employment_boundary_replay_spec(lock, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_bls_inputs_and_post_decision_event_are_exact_and_disjoint(
    lock: EmploymentBoundaryInputLock,
    event_lock: OfficialEventLock,
) -> None:
    assert len(lock.records) == 4
    assert all(
        record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
        and record.interval.available_at <= lock.decision_time
        for record in lock.records
    )
    by_id = {record.record_id: record for record in lock.records}
    assert by_id[lock.roles.december_payroll].payload["value_thousands"] == 223
    assert by_id[lock.roles.december_unemployment].payload["value_percent"] == 3.5
    assert by_id[lock.roles.january_payroll].payload["value_thousands"] == 517
    assert by_id[lock.roles.january_unemployment].payload["value_percent"] == 3.4
    assert event_lock.records[0].payload["value_thousands"] == 311
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
        ("wrong_period", "report-period mismatch"),
        ("wrong_metric", "metric mismatch"),
        ("wrong_availability_method", "availability method mismatch"),
        ("wrong_unit", "payroll unit mismatch"),
        ("negative", "positive integer"),
    ],
)
def test_employment_lock_rejects_temporal_source_and_value_inflation(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "future":
        future = (
            EmploymentBoundaryInputLock.model_validate(values).decision_time
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
        first["payload"]["release_date"] = "2023-01-07"
    elif case == "wrong_period":
        first["payload"]["report_period"] = "2022-11"
    elif case == "wrong_metric":
        first["payload"]["metric"] = "other"
    elif case == "wrong_availability_method":
        first["payload"]["availability_method"] = "date_only"
    elif case == "wrong_unit":
        first["payload"]["unit"] = "Persons"
    elif case == "negative":
        first["payload"]["value_thousands"] = -1
    with pytest.raises(ValidationError, match=message):
        EmploymentBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_employment_lock_rejects_role_and_self_hash_tamper() -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["roles"]["december_unemployment"] = values["roles"]["december_payroll"]
    with pytest.raises(ValidationError, match="must be unique"):
        EmploymentBoundaryInputLock.model_validate(values)

    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["title"] = "Fabricated BLS payroll release boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        EmploymentBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_employment_boundary_runs_relevant_engines_and_preserves_boundaries(
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
    assert shock["known_headlines"] == {
        "december_payroll": 223,
        "january_payroll": 517,
        "payroll_lower": 223,
        "payroll_upper": 517,
        "payroll_range_width": 294,
        "december_unemployment": 3.5,
        "january_unemployment": 3.4,
    }
    assert shock["naive_baseline"] == {
        "next_headline_payroll_change_thousands": 517,
        "definition": "persistence of the latest known headline payroll change",
    }
    assert shock["bound_construction"] == {
        "lower_payroll_change_thousands": 223,
        "upper_payroll_change_thousands": 517,
        "range_width_thousands": 294,
        "endpoint_method": "minimum_and_maximum_of_two_known_headline_values",
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock["applied_endpoints"] == {"lower": 223.0, "upper": 517.0}
    assert len(shock["compiled"]["trials"]) == 2

    trial = artifacts[EngineName.TRIALCOURT].payload
    assert trial["decision"]["disposition"] == "reject"
    assert len(trial["decision"]["findings"]) == 6
    assert trial["manifest"]["rejected_decisions"] == 1


@pytest.mark.integration
def test_employment_boundary_pack_builds_byte_identically(
    spec: ReplayPackSpec,
    tmp_path: Path,
) -> None:
    studio = ReplayStudio()
    compiled = studio.compile(spec)
    first = studio.build(spec, tmp_path / "first").root
    second = studio.build(spec, tmp_path / "second").root
    assert compiled.source_set_historical_replay_eligible is True
    assert compiled.contains_simulation is True
    assert compiled.topological_artifact_ids[0] == "bls-payroll.timevault.release-query"
    assert compiled.topological_artifact_ids[-1] == "bls-payroll.replaystudio.render"
    assert _file_map(first) == _file_map(second)


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
