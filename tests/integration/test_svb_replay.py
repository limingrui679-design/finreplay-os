from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import ArtifactStatus, TemporalCoverage
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import SVB_DECISION_TIME, SVBInputLock, build_svb_replay_spec

LOCK_PATH = Path("scenarios/svb-2023/input-lock.json")
CODE_COMMIT = "a" * 40


@pytest.fixture(scope="module")
def lock() -> SVBInputLock:
    return SVBInputLock.model_validate_json(LOCK_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec(lock: SVBInputLock):  # type: ignore[no-untyped-def]
    return build_svb_replay_spec(lock, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_locked_facts_are_historical_safe_and_exact(lock: SVBInputLock) -> None:
    assert len(lock.records) == 7
    assert len({record.record_id for record in lock.records}) == 7
    assert {record.payload["accn"] for record in lock.records} == {"0000719739-23-000021"}
    assert all(
        record.source.temporal_coverage is TemporalCoverage.IMMUTABLE_EVENT
        and record.interval.available_at <= SVB_DECISION_TIME
        for record in lock.records
    )
    values = {record.payload["concept"]: record.payload["val"] for record in lock.records}
    assert values["Assets"] == 211_793_000_000
    assert values["Deposits"] == 173_109_000_000
    assert values["HeldToMaturitySecurities"] == 91_327_000_000
    assert values["HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss"] == 15_160_000_000


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("future", "unavailable at decision_time"),
        ("latest_only", "immutable-event evidence"),
        ("wrong_source_hash", "must match source_response_sha256"),
    ],
)
def test_input_lock_rejects_temporal_or_source_inflation(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "future":
        values["records"][0]["interval"]["available_at"] = (
            SVB_DECISION_TIME + timedelta(seconds=1)
        ).isoformat()
    elif case == "latest_only":
        values["records"][0]["source"]["temporal_coverage"] = "latest_only"
        values["records"][0]["source"]["vintage_as_of"] = None
    elif case == "wrong_source_hash":
        values["records"][0]["source"]["sha256"] = "0" * 64
    with pytest.raises(ValidationError, match=message):
        SVBInputLock.model_validate(values)


@pytest.mark.integration
def test_actual_engine_flow_preserves_boundaries(spec) -> None:  # type: ignore[no-untyped-def]
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}
    assert set(artifacts) == set(EngineName)
    assert all(artifact.status is ArtifactStatus.REPRODUCED for artifact in artifacts.values())
    assert spec.distinct_input_records == 7
    assert spec.derived_records == 13
    assert spec.elapsed_seconds == 0

    trial = artifacts[EngineName.TRIALCOURT].payload["decision"]
    assert trial["disposition"] == "reject"
    assert len(trial["findings"]) == 6

    graph = artifacts[EngineName.MARKETTWIN].payload["snapshot"]
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2

    shocks = artifacts[EngineName.SHOCKCOMPILER].payload["compiled"]
    assert len(shocks["trials"]) == 2

    execution = artifacts[EngineName.EXECUTIONLAB].payload
    assert execution["observation"]["precision"] == "reference_only"
    assert execution["observation"]["evidence_class"] == "simulated"
    assert execution["envelope"]["fill_quantity_lower"] == 0

    allocation = artifacts[EngineName.CAPITALALLOCATOR].payload["result"]
    assert allocation["status"] == "optimal"
    assert allocation["cash_weight"] == pytest.approx(1.0)
    assert allocation["weights"]["asset:svb-htm-model-exposure"] == pytest.approx(0.0)


@pytest.mark.integration
def test_svb_pack_builds_byte_identically(spec, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    studio = ReplayStudio()
    compiled = studio.compile(spec)
    first = studio.build(spec, tmp_path / "first").root
    second = studio.build(spec, tmp_path / "second").root
    assert compiled.source_set_historical_replay_eligible is True
    assert compiled.contains_simulation is True
    assert compiled.topological_artifact_ids[0] == "svb.timevault.query"
    assert compiled.topological_artifact_ids[-1] == "svb.replaystudio.render"
    assert _file_map(first) == _file_map(second)


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
