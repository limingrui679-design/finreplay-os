from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName
from finreplay.scenarios import (
    CFTCOpenInterestBoundaryInputLock,
    OfficialEventLock,
    build_cftc_open_interest_boundary_replay_spec,
    load_cftc_open_interest_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/cftc-tff-2026/input-lock.json")
EVENT_PATH = Path("scenarios/cftc-tff-2026/event-lock.json")
CODE_COMMIT = "0" * 40


@pytest.mark.integration
def test_cftc_boundary_runs_four_engines_from_five_artifact_evidence() -> None:
    lock = load_cftc_open_interest_boundary_input_lock(LOCK_PATH)
    spec = build_cftc_open_interest_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}

    assert set(artifacts) == {
        EngineName.TIMEVAULT,
        EngineName.SHOCKCOMPILER,
        EngineName.TRIALCOURT,
        EngineName.REPLAYSTUDIO,
    }
    assert spec.distinct_input_records == 2
    assert spec.derived_records == 6
    assert len(lock.source_response_sha256s) == 5
    assert lock.supporting_receipt_sha256 == (
        "ea85ba99ecf5a7d77871e066673d55b0bfde2ebd1aff9e4e86472e366f87da9c"
    )
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)

    timevault = artifacts[EngineName.TIMEVAULT]
    assert timevault.payload["decision_observations_contracts"] == {
        "july14_open_interest": 4_465_199,
        "july21_open_interest": 4_335_075,
        "july21_reported_weekly_change": -130_124,
    }
    assert timevault.payload["api_annual_crosscheck_verified"] is True
    assert timevault.payload["schedule_self_describes_as_tentative"] is True
    assert timevault.payload["actual_row_publication_log_available"] is False
    assert timevault.payload["source_auxiliary_positions_used_as_range_input"] is False
    assert timevault.payload["contract_face_value_notional_conversion_performed"] is False
    assert timevault.payload["source_response_file_count"] == 5

    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert set(shock.source_hashes) == set(lock.source_response_sha256s)
    assert shock.payload["known_open_interest_levels"] == {
        "july14_open_interest_contracts": 4_465_199,
        "july21_open_interest_contracts": 4_335_075,
        "known_decline_contracts": 130_124,
        "lower_level_contracts": 4_204_951,
        "upper_level_contracts": 4_335_075,
        "range_width_contracts": 130_124,
    }
    variable = "next_ust_2y_tff_open_interest_contracts"
    assert shock.payload["naive_baseline"] == {
        variable: 4_335_075,
        "definition": "persistence of the July 21 total open-interest level",
    }
    assert shock.payload["bound_construction"] == {
        "lower_level_contracts": 4_204_951,
        "upper_level_contracts": 4_335_075,
        "range_width_contracts": 130_124,
        "known_decline_contracts": 130_124,
        "endpoint_method": "latest_level_persistence_or_repeat_one_known_decline",
        "total_open_interest_only": True,
        "category_positions_used": False,
        "trader_counts_used": False,
        "contract_face_value_used": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {
        "lower": 4_204_951.0,
        "upper": 4_335_075.0,
    }
    parameter = shock.payload["program"]["parameters"][0]
    assert parameter["unit"] == "futures_contracts"
    assert parameter["lower"] == 4_204_951.0
    assert parameter["upper"] == 4_335_075.0

    trial = artifacts[EngineName.TRIALCOURT]
    assert trial.payload["decision"]["disposition"] == TrialDisposition.REJECT.value
    assert len(trial.payload["decision"]["findings"]) == 6
    assert trial.payload["manifest"]["rejected_decisions"] == 1
    assert {claim.evidence_class for claim in spec.claims} == {
        EvidenceClass.REPORTED,
        EvidenceClass.INFERRED,
        EvidenceClass.SIMULATED,
        EvidenceClass.EXTRACTED,
    }


@pytest.mark.integration
def test_july28_event_is_disjoint_and_exceeds_fixed_upper_endpoint() -> None:
    lock = load_cftc_open_interest_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["report_date"] == "2026-07-28"
    assert record.payload["official_scheduled_release_date"] == "2026-07-31"
    assert record.payload["open_interest_contracts"] == 4_406_588
    assert record.payload["reported_change_from_prior_week_contracts"] == 71_513
    assert record.interval.availability_confidence == 0.98
    assert record.payload["schedule_self_describes_as_tentative"] is True
    assert record.payload["actual_row_publication_log_available"] is False
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert record.payload["open_interest_contracts"] - 4_335_075 == 71_513

    spec = build_cftc_open_interest_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    shock = next(
        artifact for artifact in spec.artifacts if artifact.engine is EngineName.SHOCKCOMPILER
    )
    assert shock.payload["bound_construction"]["lower_level_contracts"] == 4_204_951
    assert shock.payload["bound_construction"]["upper_level_contracts"] == 4_335_075
    assert shock.payload["bound_construction"]["future_event_used"] is False


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only scheduled UST 2Y"),
        ("wrong_publisher", "publisher mismatch"),
        ("wrong_temporal", "immutable-event coverage"),
        ("wrong_license", "license boundary"),
        ("wrong_redistribution", "redistribution boundary"),
        ("wrong_primary_hash", "API hash"),
        ("wrong_url", "source URL"),
        ("wrong_version", "source version"),
        ("wrong_vintage", "composite source vintage"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("wrong_schema", "payload schema"),
        ("wrong_confidence", "confidence must remain 0.98"),
        ("wrong_rule", "availability rule"),
        ("wrong_published", "scheduled publication time"),
        ("wrong_available", "scheduled availability time"),
        ("wrong_valid", "valid time"),
        ("wrong_valid_to", "open events"),
        ("wrong_revised", "open events"),
        ("wrong_ingested", "retrieval and ingestion"),
        ("retrieved_after_build", "after build_epoch"),
        ("extra_payload", "payload hash"),
    ],
)
def test_cftc_lock_rejects_source_timing_and_payload_corruption(
    case: str,
    message: str,
) -> None:
    values = _lock_values()
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "cftc.cot.other"
    elif case == "wrong_publisher":
        first["source"]["publisher"] = "Other publisher"
    elif case == "wrong_temporal":
        first["source"]["temporal_coverage"] = "latest_only"
        first["source"]["vintage_as_of"] = None
    elif case == "wrong_license":
        first["source"]["license_class"] = "download_only"
    elif case == "wrong_redistribution":
        first["source"]["redistribution_note"] = "fabricated permission"
    elif case == "wrong_primary_hash":
        first["source"]["sha256"] = "0" * 64
    elif case == "wrong_url":
        first["source"]["url"] = "https://publicreporting.cftc.gov/resource/other.json"
    elif case == "wrong_version":
        first["source"]["source_version"] = "CFTC-TFF:wrong"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2026-07-17T19:30:00Z"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "cftc_contract:other"
    elif case == "wrong_schema":
        first["payload_schema_version"] = "1.1.0"
    elif case == "wrong_confidence":
        first["interval"]["availability_confidence"] = 0.97
    elif case == "wrong_rule":
        first["interval"]["availability_rule"] = "generic Friday schedule"
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2026-07-17T19:29:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2026-07-17T19:30:01Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2026-07-14T00:00:01Z"
    elif case == "wrong_valid_to":
        first["interval"]["valid_to"] = "2026-07-18T00:00:00Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = "2026-07-17T19:30:00Z"
    elif case == "wrong_ingested":
        first["interval"]["ingested_at"] = "2026-08-14T09:18:36Z"
    elif case == "retrieved_after_build":
        first["source"]["retrieved_at"] = "2026-08-14T09:30:01Z"
        first["interval"]["ingested_at"] = "2026-08-14T09:30:01Z"
    elif case == "extra_payload":
        first["payload"]["fabricated"] = True

    with pytest.raises((ValidationError, ValueError), match=message):
        CFTCOpenInterestBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_cftc_lock_rejects_hash_roles_decision_and_self_hash_corruption() -> None:
    wrong_hashes = _lock_values()
    wrong_hashes["source_response_sha256s"][0] = "0" * 64
    with pytest.raises(ValidationError, match="five official responses"):
        CFTCOpenInterestBoundaryInputLock.model_validate(wrong_hashes)

    wrong_receipt = _lock_values()
    wrong_receipt["supporting_receipt_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="supporting receipt"):
        CFTCOpenInterestBoundaryInputLock.model_validate(wrong_receipt)

    duplicate_roles = _lock_values()
    duplicate_roles["roles"]["july21_decision_release"] = duplicate_roles["roles"]["july14_release"]
    with pytest.raises(ValidationError, match="role record IDs must be unique"):
        CFTCOpenInterestBoundaryInputLock.model_validate(duplicate_roles)

    wrong_decision = _lock_values()
    wrong_decision["decision_time"] = "2026-07-24T19:31:00Z"
    with pytest.raises(ValidationError, match="July 24 scheduled release"):
        CFTCOpenInterestBoundaryInputLock.model_validate(wrong_decision)

    wrong_self_hash = _lock_values()
    wrong_self_hash["lock_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="lock_sha256"):
        CFTCOpenInterestBoundaryInputLock.model_validate(wrong_self_hash)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("availability_confidence", 0.97),
        ("schedule_self_describes_as_tentative", False),
        ("actual_row_publication_log_available", True),
        ("availability_method", "generic schedule"),
    ],
)
def test_event_lock_low_confidence_exception_is_narrow(field: str, value: Any) -> None:
    event = cast(dict[str, Any], json.loads(EVENT_PATH.read_text(encoding="utf-8")))
    if field == "availability_confidence":
        event["records"][0]["interval"][field] = value
    else:
        event["records"][0]["payload"][field] = value
    with pytest.raises(ValidationError, match="qualified CFTC schedule boundary"):
        OfficialEventLock.model_validate(event)


def _lock_values() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(LOCK_PATH.read_text(encoding="utf-8")))
