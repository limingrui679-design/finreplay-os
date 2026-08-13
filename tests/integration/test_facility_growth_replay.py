from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import ArtifactStatus, TemporalCoverage
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    FacilityGrowthInputLock,
    OfficialEventLock,
    build_facility_growth_replay_spec,
)

LOCK_PATH = Path("scenarios/btfp-growth-2023/input-lock.json")
EVENT_PATH = Path("scenarios/btfp-growth-2023/event-lock.json")
CODE_COMMIT = "d" * 40


@pytest.fixture(scope="module")
def lock() -> FacilityGrowthInputLock:
    return FacilityGrowthInputLock.model_validate_json(LOCK_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def event_lock() -> OfficialEventLock:
    return OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec(lock: FacilityGrowthInputLock):  # type: ignore[no-untyped-def]
    return build_facility_growth_replay_spec(lock, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_h41_inputs_and_post_decision_event_are_exact_and_disjoint(
    lock: FacilityGrowthInputLock,
    event_lock: OfficialEventLock,
) -> None:
    assert len(lock.records) == 4
    assert all(
        record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
        and record.interval.available_at <= lock.decision_time
        for record in lock.records
    )
    by_id = {record.record_id: record for record in lock.records}
    assert by_id[lock.roles.first_weekly_average].payload["value_millions"] == 2_443
    assert by_id[lock.roles.first_wednesday].payload["value_millions"] == 11_943
    assert by_id[lock.roles.second_weekly_average].payload["value_millions"] == 34_609
    assert by_id[lock.roles.second_wednesday].payload["value_millions"] == 53_669
    assert event_lock.records[0].payload["value_millions"] == 64_403
    assert event_lock.records[0].interval.available_at > lock.decision_time
    assert event_lock.records[0].record_id not in {record.record_id for record in lock.records}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("future", "post-decision input"),
        ("latest_only", "versioned release snapshots"),
        ("observed", "must remain reported"),
        ("wrong_release", "release mismatch"),
        ("wrong_week", "week-ending mismatch"),
        ("wrong_metric", "metric mismatch"),
        ("wrong_program", "program mismatch"),
        ("wrong_unit", "unit mismatch"),
        ("negative", "positive integer"),
    ],
)
def test_facility_lock_rejects_temporal_source_and_value_inflation(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "future":
        first["interval"]["available_at"] = (
            FacilityGrowthInputLock.model_validate(values).decision_time + timedelta(seconds=1)
        ).isoformat()
    elif case == "latest_only":
        first["source"]["temporal_coverage"] = "latest_only"
        first["source"]["vintage_as_of"] = None
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_release":
        first["payload"]["release_date"] = "2023-03-17"
    elif case == "wrong_week":
        first["payload"]["week_ending"] = "2023-03-14"
    elif case == "wrong_metric":
        first["payload"]["metric"] = "other"
    elif case == "wrong_program":
        first["payload"]["program"] = "Other Facility"
    elif case == "wrong_unit":
        first["payload"]["unit"] = "Billions of Dollars"
    elif case == "negative":
        first["payload"]["value_millions"] = -1
    with pytest.raises(ValidationError, match=message):
        FacilityGrowthInputLock.model_validate(values)


@pytest.mark.integration
def test_facility_lock_rejects_role_and_self_hash_tamper() -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["roles"]["first_wednesday"] = values["roles"]["first_weekly_average"]
    with pytest.raises(ValidationError, match="must be unique"):
        FacilityGrowthInputLock.model_validate(values)

    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["title"] = "Fabricated facility growth boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        FacilityGrowthInputLock.model_validate(values)


@pytest.mark.integration
def test_facility_growth_runs_relevant_engines_and_preserves_boundaries(spec) -> None:  # type: ignore[no-untyped-def]
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
    assert shock["known_growth_millions"] == {
        "first_average": 2_443,
        "second_average": 34_609,
        "average_growth": 32_166,
        "first_wednesday": 11_943,
        "second_wednesday": 53_669,
        "wednesday_growth": 41_726,
    }
    assert shock["naive_baseline"] == {
        "next_week_growth_millions": 0.0,
        "next_wednesday_balance_millions": 53_669,
    }
    assert shock["bound_construction"] == {
        "lower_growth_millions": 0,
        "upper_growth_millions": 41_726,
        "lower_balance_millions": 53_669,
        "upper_balance_millions": 95_395,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert len(shock["compiled"]["trials"]) == 2

    trial = artifacts[EngineName.TRIALCOURT].payload
    assert trial["decision"]["disposition"] == "reject"
    assert len(trial["decision"]["findings"]) == 6
    assert trial["manifest"]["rejected_decisions"] == 1


@pytest.mark.integration
def test_facility_growth_pack_builds_byte_identically(spec, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    studio = ReplayStudio()
    compiled = studio.compile(spec)
    first = studio.build(spec, tmp_path / "first").root
    second = studio.build(spec, tmp_path / "second").root
    assert compiled.source_set_historical_replay_eligible is True
    assert compiled.contains_simulation is True
    assert compiled.topological_artifact_ids[0] == "btfp-growth.timevault.release-query"
    assert compiled.topological_artifact_ids[-1] == "btfp-growth.replaystudio.render"
    assert _file_map(first) == _file_map(second)


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
