from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import ArtifactStatus, TemporalCoverage
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    BankBoundaryInputLock,
    OfficialEventLock,
    build_bank_boundary_replay_spec,
)

LOCK_PATH = Path("scenarios/pacwest-2023/input-lock.json")
EVENT_PATH = Path("scenarios/pacwest-2023/event-lock.json")
CODE_COMMIT = "b" * 40
WESTERN_ALLIANCE_LOCK_PATH = Path("scenarios/western-alliance-2023/input-lock.json")
WESTERN_ALLIANCE_EVENT_PATH = Path("scenarios/western-alliance-2023/event-lock.json")


@pytest.fixture(scope="module")
def lock() -> BankBoundaryInputLock:
    return BankBoundaryInputLock.model_validate_json(LOCK_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def event_lock() -> OfficialEventLock:
    return OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec(lock: BankBoundaryInputLock):  # type: ignore[no-untyped-def]
    return build_bank_boundary_replay_spec(lock, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_pacwest_locked_facts_are_historical_safe_and_exact(
    lock: BankBoundaryInputLock,
    event_lock: OfficialEventLock,
) -> None:
    assert len(lock.records) == 7
    assert len({record.record_id for record in lock.records}) == 7
    assert {record.payload["accn"] for record in lock.records} == {"0001628280-23-005257"}
    assert all(
        record.source.temporal_coverage is TemporalCoverage.IMMUTABLE_EVENT
        and record.interval.available_at <= lock.decision_time
        for record in lock.records
    )
    values = {record.payload["concept"]: record.payload["val"] for record in lock.records}
    assert values["Assets"] == 41_228_936_000
    assert values["Deposits"] == 33_936_334_000
    assert values["HeldToMaturitySecurities"] == 2_270_635_000
    assert values["HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss"] == 158_671_000
    assert event_lock.decision_time == lock.decision_time
    assert all(record.interval.available_at > lock.decision_time for record in event_lock.records)
    assert not (
        {record.record_id for record in event_lock.records}
        & {record.record_id for record in lock.records}
    )


@pytest.mark.integration
def test_western_alliance_locked_facts_and_event_boundary_are_exact() -> None:
    lock = BankBoundaryInputLock.model_validate_json(
        WESTERN_ALLIANCE_LOCK_PATH.read_text(encoding="utf-8")
    )
    event = OfficialEventLock.model_validate_json(
        WESTERN_ALLIANCE_EVENT_PATH.read_text(encoding="utf-8")
    )
    assert lock.selected_accession == "0001212545-23-000093"
    values = {record.payload["concept"]: record.payload["val"] for record in lock.records}
    assert values["Assets"] == 67_734_000_000
    assert values["Deposits"] == 53_644_000_000
    assert values["StockholdersEquity"] == 5_356_000_000
    assert values["HeldToMaturitySecurities"] == 1_289_000_000
    assert values["HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss"] == 177_000_000
    assert values["AvailableForSaleSecuritiesDebtSecurities"] == 7_092_000_000
    assert values["AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedLossBeforeTax"] == (
        890_000_000
    )
    assert event.records[0].payload["accessionNumber"] == "0001212545-23-000122"
    assert event.records[0].interval.available_at > lock.decision_time
    assert event.records[0].record_id not in {record.record_id for record in lock.records}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("future", "unavailable at decision_time"),
        ("latest_only", "immutable-event evidence"),
        ("wrong_accession", "selected accession"),
        ("wrong_end", "configured balance date"),
        ("negative", "positive USD values"),
    ],
)
def test_bank_lock_rejects_temporal_source_and_value_inflation(case: str, message: str) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "future":
        values["records"][0]["interval"]["available_at"] = (
            BankBoundaryInputLock.model_validate(values).decision_time + timedelta(seconds=1)
        ).isoformat()
    elif case == "latest_only":
        values["records"][0]["source"]["temporal_coverage"] = "latest_only"
        values["records"][0]["source"]["vintage_as_of"] = None
    elif case == "wrong_accession":
        values["records"][0]["payload"]["accn"] = "0000000000-23-000001"
    elif case == "wrong_end":
        values["records"][0]["payload"]["end"] = "2023-03-31"
    elif case == "negative":
        values["records"][0]["payload"]["val"] = -1
    with pytest.raises(ValidationError, match=message):
        BankBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_bank_lock_rejects_duplicate_roles_and_self_hash_tamper() -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["concepts"]["afs_loss"] = values["concepts"]["htm_loss"]
    with pytest.raises(ValidationError, match="concepts must be unique"):
        BankBoundaryInputLock.model_validate(values)

    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values["issuer_label"] = "Fabricated Bank"
    with pytest.raises(ValidationError, match="lock_sha256"):
        BankBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_bank_boundary_runs_all_engines_and_preserves_truth_boundaries(spec) -> None:  # type: ignore[no-untyped-def]
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}
    assert set(artifacts) == set(EngineName)
    assert all(artifact.status is ArtifactStatus.REPRODUCED for artifact in artifacts.values())
    assert spec.distinct_input_records == 7
    assert spec.derived_records == 13
    assert spec.elapsed_seconds == 0
    assert all(artifact.payload.get("input_lock_sha256") for artifact in artifacts.values())

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
    assert allocation["weights"]["asset:pacwest-bancorp-htm-model-exposure"] == pytest.approx(0.0)


@pytest.mark.integration
def test_bank_boundary_pack_builds_byte_identically(spec, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    studio = ReplayStudio()
    compiled = studio.compile(spec)
    first = studio.build(spec, tmp_path / "first").root
    second = studio.build(spec, tmp_path / "second").root
    assert compiled.source_set_historical_replay_eligible is True
    assert compiled.contains_simulation is True
    assert compiled.topological_artifact_ids[0] == "pacwest.timevault.query"
    assert compiled.topological_artifact_ids[-1] == "pacwest.replaystudio.render"
    assert _file_map(first) == _file_map(second)


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
