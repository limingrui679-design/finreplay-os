from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName
from finreplay.scenarios import (
    OfficialEventLock,
    WorkingGasStockBoundaryInputLock,
    build_working_gas_stock_boundary_replay_spec,
    load_working_gas_stock_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/eia-wngsr-2020/input-lock.json")
EVENT_PATH = Path("scenarios/eia-wngsr-2020/event-lock.json")
CODE_COMMIT = "0" * 40


@pytest.mark.integration
def test_working_gas_boundary_runs_four_engines_from_original_vintages() -> None:
    lock = load_working_gas_stock_boundary_input_lock(LOCK_PATH)
    spec = build_working_gas_stock_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}

    assert set(artifacts) == {
        EngineName.TIMEVAULT,
        EngineName.SHOCKCOMPILER,
        EngineName.TRIALCOURT,
        EngineName.REPLAYSTUDIO,
    }
    assert spec.distinct_input_records == 2
    assert len(lock.source_response_sha256s) == 3
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)

    timevault = artifacts[EngineName.TIMEVAULT]
    assert timevault.payload["decision_observations"] == {
        "march06_lower48_working_gas_bcf": 2_043,
        "march13_lower48_working_gas_bcf": 2_034,
        "march13_reported_net_change_bcf": -9,
    }
    assert timevault.payload["original_value_recovery_verified"] is True
    assert timevault.payload["current_history_cross_check_verified"] is True
    assert timevault.payload["source_statistical_measures_used_as_range_input"] is False
    assert timevault.payload["source_evidence_file_count"] == 3

    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert set(shock.source_hashes) == set(lock.source_response_sha256s)
    assert shock.payload["known_original_stock_levels"] == {
        "march06_working_gas_bcf": 2_043,
        "march13_working_gas_bcf": 2_034,
        "known_decline_bcf": 9,
        "lower_stock_bcf": 2_025,
        "upper_stock_bcf": 2_034,
        "range_width_bcf": 9,
    }
    variable = "next_lower_48_working_gas_stock_bcf"
    assert shock.payload["naive_baseline"] == {
        variable: 2_034,
        "definition": "persistence of the March 13 Lower 48 working-gas stock",
    }
    assert shock.payload["bound_construction"] == {
        "lower_stock_bcf": 2_025,
        "upper_stock_bcf": 2_034,
        "range_width_bcf": 9,
        "known_decline_bcf": 9,
        "endpoint_method": "latest_stock_persistence_or_repeat_one_known_decline",
        "original_vintage_values_only": True,
        "source_statistical_measures_used": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {"lower": 2_025.0, "upper": 2_034.0}
    parameter = shock.payload["program"]["parameters"][0]
    assert parameter["unit"] == "billion_cubic_feet"
    assert parameter["lower"] == 2_025.0
    assert parameter["upper"] == 2_034.0

    by_week = {record.payload["week_ending"]: record for record in lock.records}
    assert by_week["2020-03-06"].payload["value_bcf"] == 2_043
    assert by_week["2020-03-06"].payload["reported_net_change_bcf"] == -48
    assert by_week["2020-03-06"].payload["net_change_standard_error_bcf_lower_48"] == "0.6"
    assert by_week["2020-03-13"].payload["value_bcf"] == 2_034
    assert by_week["2020-03-13"].payload["reported_net_change_bcf"] == -9
    assert by_week["2020-03-13"].payload["five_region_rounding_difference_bcf"] == -1
    assert by_week["2020-03-13"].payload["net_change_standard_error_bcf_lower_48"] == "0.8"
    assert all(
        record.payload["coefficient_of_variation_percent_lower_48"] == "0.5"
        and record.payload["statistical_measures_define_finreplay_range"] is False
        and record.payload["current_history_matches_original_estimate"] is True
        for record in lock.records
    )

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
def test_march20_event_is_disjoint_and_breaches_the_fixed_lower_endpoint() -> None:
    lock = load_working_gas_stock_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-03-26"
    assert record.payload["week_ending"] == "2020-03-20"
    assert record.payload["value_bcf"] == 2_005
    assert record.payload["prior_value_bcf"] == 2_034
    assert record.payload["reported_net_change_bcf"] == -29
    assert record.payload["coefficient_of_variation_percent_lower_48"] == "0.5"
    assert record.payload["net_change_standard_error_bcf_lower_48"] == "0.8"
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert 2_025 - record.payload["value_bcf"] == 20


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only revision-safe"),
        ("wrong_publisher", "publisher mismatch"),
        ("wrong_temporal", "native original-vintage"),
        ("wrong_license", "license boundary"),
        ("wrong_redistribution", "redistribution boundary"),
        ("wrong_primary_hash", "primary revisions-workbook hash"),
        ("wrong_url", "primary source URL"),
        ("wrong_version", "source version"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("wrong_schema", "payload schema"),
        ("low_confidence", "timing must be deterministic"),
        ("wrong_rule", "availability rule"),
        ("wrong_published", "publication time"),
        ("wrong_available", "availability time"),
        ("wrong_valid", "validity time"),
        ("wrong_valid_to", "unrevised open facts"),
        ("wrong_revised", "unrevised open facts"),
        ("wrong_vintage", "source vintage"),
        ("wrong_ingested", "retrieval and ingestion"),
        ("retrieved_after_build", "after build_epoch"),
        ("extra_payload", "payload hash"),
    ],
)
def test_working_gas_lock_rejects_source_timing_and_payload_corruption(
    case: str,
    message: str,
) -> None:
    values = _lock_values()
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "eia.wngsr.other"
    elif case == "wrong_publisher":
        first["source"]["publisher"] = "Other publisher"
    elif case == "wrong_temporal":
        first["source"]["temporal_coverage"] = "versioned_snapshot"
    elif case == "wrong_license":
        first["source"]["license_class"] = "redistributable"
    elif case == "wrong_redistribution":
        first["source"]["redistribution_note"] = "fabricated permission"
    elif case == "wrong_primary_hash":
        first["source"]["sha256"] = "0" * 64
    elif case == "wrong_url":
        first["source"]["url"] = "https://ir.eia.gov/ngs/other.xls"
    elif case == "wrong_version":
        first["source"]["source_version"] = "EIA-WNGSR:wrong"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "eia_series:other"
    elif case == "wrong_schema":
        first["payload_schema_version"] = "1.1.0"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_rule":
        first["interval"]["availability_rule"] = "current response headers"
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-03-12T14:29:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-03-12T14:30:01Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-03-06T15:00:01Z"
    elif case == "wrong_valid_to":
        first["interval"]["valid_to"] = "2020-03-07T00:00:00Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = "2020-03-12T14:30:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-03-12T14:30:01Z"
    elif case == "wrong_ingested":
        first["interval"]["ingested_at"] = "2026-08-14T07:49:06Z"
    elif case == "retrieved_after_build":
        first["source"]["retrieved_at"] = "2026-08-14T08:10:01Z"
        first["interval"]["ingested_at"] = "2026-08-14T08:10:01Z"
    elif case == "extra_payload":
        first["payload"]["fabricated"] = True

    with pytest.raises((ValidationError, ValueError), match=message):
        WorkingGasStockBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_working_gas_lock_rejects_hash_roles_decision_and_self_hash_corruption() -> None:
    wrong_hashes = _lock_values()
    wrong_hashes["source_response_sha256s"][0] = "0" * 64
    with pytest.raises(ValidationError, match="three official responses"):
        WorkingGasStockBoundaryInputLock.model_validate(wrong_hashes)

    duplicate_roles = _lock_values()
    duplicate_roles["roles"]["march13_decision_release"] = duplicate_roles["roles"][
        "march06_release"
    ]
    with pytest.raises(ValidationError, match="role record IDs must be unique"):
        WorkingGasStockBoundaryInputLock.model_validate(duplicate_roles)

    wrong_decision = _lock_values()
    wrong_decision["decision_time"] = "2020-03-19T14:31:00Z"
    with pytest.raises(ValidationError, match="must equal the March 19 release"):
        WorkingGasStockBoundaryInputLock.model_validate(wrong_decision)

    wrong_self_hash = _lock_values()
    wrong_self_hash["lock_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="lock_sha256"):
        WorkingGasStockBoundaryInputLock.model_validate(wrong_self_hash)


def _lock_values() -> dict[str, Any]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
