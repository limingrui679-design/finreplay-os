from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import ArtifactStatus, TemporalCoverage
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    MacroRevisionInputLock,
    OfficialEventLock,
    build_macro_revision_replay_spec,
)

LOCK_PATH = Path("scenarios/gdp-revision-2022q4/input-lock.json")
EVENT_PATH = Path("scenarios/gdp-revision-2022q4/event-lock.json")
CODE_COMMIT = "c" * 40


@pytest.fixture(scope="module")
def lock() -> MacroRevisionInputLock:
    return MacroRevisionInputLock.model_validate_json(LOCK_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def event_lock() -> OfficialEventLock:
    return OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec(lock: MacroRevisionInputLock):  # type: ignore[no-untyped-def]
    return build_macro_revision_replay_spec(lock, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_locked_vintages_and_future_event_are_exact_and_disjoint(
    lock: MacroRevisionInputLock,
    event_lock: OfficialEventLock,
) -> None:
    assert len(lock.records) == 4
    assert all(
        record.source.temporal_coverage is TemporalCoverage.VINTAGE_NATIVE
        and record.interval.available_at <= lock.decision_time
        for record in lock.records
    )
    by_id = {record.record_id: record for record in lock.records}
    assert by_id[lock.roles.q3_advance].payload["value"] == "25663.289"
    assert by_id[lock.roles.q3_second].payload["value"] == "25698.960"
    assert by_id[lock.roles.q3_predecision].payload["value"] == "25723.941"
    assert by_id[lock.roles.q4_advance].payload["value"] == "26132.458"
    assert event_lock.records[0].payload["value"] == "26144.956"
    assert event_lock.records[0].interval.available_at > lock.decision_time
    assert event_lock.records[0].record_id not in {record.record_id for record in lock.records}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("future", "post-decision input"),
        ("latest_only", "native-vintage evidence"),
        ("observed", "must remain reported"),
        ("wrong_vintage", "vintage mismatch"),
        ("wrong_observation", "observation mismatch"),
        ("wrong_unit", "unit mismatch"),
        ("negative", "positive decimal"),
    ],
)
def test_macro_lock_rejects_temporal_source_and_value_inflation(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "future":
        first["interval"]["available_at"] = (
            MacroRevisionInputLock.model_validate(values).decision_time + timedelta(seconds=1)
        ).isoformat()
    elif case == "latest_only":
        first["source"]["temporal_coverage"] = "latest_only"
        first["source"]["vintage_as_of"] = None
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_vintage":
        first["payload"]["vintage_date"] = "2022-10-28"
    elif case == "wrong_observation":
        first["payload"]["observation_date"] = "2022-04-01"
    elif case == "wrong_unit":
        first["payload"]["unit"] = "Percent"
    elif case == "negative":
        first["payload"]["value"] = "-1"
    with pytest.raises(ValidationError, match=message):
        MacroRevisionInputLock.model_validate(values)


@pytest.mark.integration
def test_macro_lock_rejects_role_and_self_hash_tamper() -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["roles"]["q3_second"] = values["roles"]["q3_advance"]
    with pytest.raises(ValidationError, match="must be unique"):
        MacroRevisionInputLock.model_validate(values)

    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["title"] = "Fabricated macro revision title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        MacroRevisionInputLock.model_validate(values)


@pytest.mark.integration
def test_macro_revision_runs_only_relevant_engines_and_preserves_boundaries(spec) -> None:  # type: ignore[no-untyped-def]
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
    assert spec.elapsed_seconds == 0
    assert all(artifact.payload.get("input_lock_sha256") for artifact in artifacts.values())

    shock = artifacts[EngineName.SHOCKCOMPILER].payload
    assert shock["known_q3_revision_path_billions"] == {
        "advance_to_second": "35.671",
        "second_to_predecision": "24.981",
        "advance_to_predecision": "60.652",
    }
    assert shock["naive_baseline"] == {
        "revision_billions": 0.0,
        "q4_gdp_billions": "26132.458",
    }
    assert shock["bound_construction"]["magnitude_billions"] == "60.652"
    assert shock["bound_construction"]["probability_assigned"] is False
    assert shock["bound_construction"]["future_event_used"] is False
    assert shock["candidate_q4_gdp_billions"] == {
        "lower": "26071.806",
        "upper": "26193.110",
    }
    assert len(shock["compiled"]["trials"]) == 2

    trial = artifacts[EngineName.TRIALCOURT].payload
    assert trial["decision"]["disposition"] == "reject"
    assert len(trial["decision"]["findings"]) == 6
    assert trial["manifest"]["rejected_decisions"] == 1


@pytest.mark.integration
def test_macro_revision_pack_builds_byte_identically(spec, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    studio = ReplayStudio()
    compiled = studio.compile(spec)
    first = studio.build(spec, tmp_path / "first").root
    second = studio.build(spec, tmp_path / "second").root
    assert compiled.source_set_historical_replay_eligible is True
    assert compiled.contains_simulation is True
    assert compiled.topological_artifact_ids[0] == "gdp-revision.timevault.vintage-query"
    assert compiled.topological_artifact_ids[-1] == "gdp-revision.replaystudio.render"
    assert _file_map(first) == _file_map(second)


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
