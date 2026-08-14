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
    TradeDeficitLevelBoundaryInputLock,
    build_trade_deficit_level_boundary_replay_spec,
    load_trade_deficit_level_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/census-ft900-2020/input-lock.json")
EVENT_PATH = Path("scenarios/census-ft900-2020/event-lock.json")
CODE_COMMIT = "0" * 40


@pytest.mark.integration
def test_trade_deficit_boundary_runs_four_engines_from_one_decision_snapshot() -> None:
    lock = load_trade_deficit_level_boundary_input_lock(LOCK_PATH)
    spec = build_trade_deficit_level_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}

    assert set(artifacts) == {
        EngineName.TIMEVAULT,
        EngineName.SHOCKCOMPILER,
        EngineName.TRIALCOURT,
        EngineName.REPLAYSTUDIO,
    }
    assert spec.distinct_input_records == 2
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)
    assert len(lock.source_response_sha256s) == 4

    timevault = artifacts[EngineName.TIMEVAULT]
    assert timevault.payload["decision_snapshot"] == {
        "revised_january_deficit_million_dollars": 45_482,
        "initial_february_deficit_million_dollars": 39_932,
    }
    assert timevault.payload["january_initial_release_retained_for_revision_lineage"] is True
    assert timevault.payload["paired_pdf_xls_crosscheck_verified"] is True
    assert timevault.payload["source_evidence_file_count"] == 4
    assert timevault.payload["current_archive_byte_identity_at_release_claimed"] is False

    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert set(shock.source_hashes) == set(lock.source_response_sha256s)
    assert shock.payload["known_decision_snapshot_levels"] == {
        "january_initial_release_deficit_million_dollars": 45_338,
        "decision_snapshot_revised_january_deficit_million_dollars": 45_482,
        "january_revision_delta_known_at_decision_million_dollars": 144,
        "february_initial_deficit_million_dollars": 39_932,
        "known_decision_snapshot_decline_million_dollars": 5_550,
        "lower_level_million_dollars": 34_382,
        "upper_level_million_dollars": 39_932,
        "range_width_million_dollars": 5_550,
    }
    variable = "next_goods_services_deficit_level_million_dollars"
    assert shock.payload["naive_baseline"] == {
        variable: 39_932,
        "definition": "persistence of the February initial FT-900 deficit level",
    }
    assert shock.payload["bound_construction"] == {
        "lower_level_million_dollars": 34_382,
        "upper_level_million_dollars": 39_932,
        "range_width_million_dollars": 5_550,
        "known_decision_snapshot_decline_million_dollars": 5_550,
        "endpoint_method": (
            "latest_initial_level_persistence_or_repeat_same_release_snapshot_decline"
        ),
        "basis_is_single_february_release_snapshot": True,
        "january_initial_release_used_as_numeric_endpoint_input": False,
        "official_confidence_interval_used": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {"lower": 34_382.0, "upper": 39_932.0}
    parameter = shock.payload["program"]["parameters"][0]
    assert parameter["unit"] == "million_us_dollars_seasonally_adjusted_deficit"
    assert parameter["lower"] == 34_382.0
    assert parameter["upper"] == 39_932.0

    january = next(
        record for record in lock.records if record.record_id == lock.roles.january_release_snapshot
    )
    february = next(
        record
        for record in lock.records
        if record.record_id == lock.roles.february_decision_snapshot
    )
    assert january.payload["value_million_dollars"] == 45_338
    assert january.payload["release_timezone_abbreviation"] == "EST"
    assert january.payload["release_xls_zip_sha256"] in lock.source_response_sha256s
    assert february.payload["value_million_dollars"] == 39_932
    assert february.payload["prior_month_revised_deficit_million_dollars"] == 45_482
    assert february.payload["release_snapshot_deficit_million_dollars"] == {
        "2020-01": 45_482,
        "2020-02": 39_932,
    }
    assert february.payload["release_snapshot_revision_delta_million_dollars"] == {
        "2020-01": 144,
        "2020-02": None,
    }
    for record in lock.records:
        assert record.payload["seasonally_adjusted"] is True
        assert record.payload["adjusted_for_price_changes"] is False
        assert record.payload["goods_data_subject_to_sampling_error"] is False
        assert record.payload["headline_statistical_significance_applicable_or_measurable"] is (
            False
        )
        assert record.payload["nonsampling_errors_possible"] is True
        assert record.payload["current_archive_byte_identity_at_release_claimed"] is False

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
def test_march_event_is_disjoint_revised_and_breaches_fixed_upper_endpoint() -> None:
    lock = load_trade_deficit_level_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-05-05"
    assert record.payload["reference_month"] == "2020-03"
    assert record.payload["value_million_dollars"] == 44_415
    assert record.payload["signed_balance_million_dollars"] == -44_415
    assert record.payload["release_snapshot_deficit_million_dollars"] == {
        "2020-01": 45_482,
        "2020-02": 39_810,
        "2020-03": 44_415,
    }
    assert record.payload["release_snapshot_previous_deficit_million_dollars"] == {
        "2020-01": 45_482,
        "2020-02": 39_932,
        "2020-03": None,
    }
    assert record.payload["release_snapshot_revision_delta_million_dollars"] == {
        "2020-01": 0,
        "2020-02": -122,
        "2020-03": None,
    }
    assert record.payload["covid_publication_standard_statement_present"] is True
    assert record.payload["adjusted_for_price_changes"] is False
    assert record.payload["current_archive_byte_identity_at_release_claimed"] is False
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert record.payload["value_million_dollars"] - 39_932 == 4_483


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived joint trade facts"),
        ("wrong_publisher", "source publisher mismatch"),
        ("wrong_temporal", "versioned release snapshots"),
        ("wrong_license", "license boundary mismatch"),
        ("wrong_redistribution", "redistribution boundary mismatch"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("wrong_schema", "payload schema mismatch"),
        ("low_confidence", "timing must be deterministic"),
        ("wrong_rule", "availability rule mismatch"),
        ("wrong_published", "publication time mismatch"),
        ("wrong_available", "availability time mismatch"),
        ("wrong_revised", "initial monthly-release records"),
        ("wrong_valid_to", "open valid-time intervals"),
        ("wrong_valid", "valid time mismatch"),
        ("wrong_vintage", "source vintage mismatch"),
        ("wrong_ingested", "retrieval and ingestion times must agree"),
        ("retrieved_before_release", "ingested_at must not precede historical availability"),
        ("retrieved_after_build", "retrieval cannot occur after build_epoch"),
        ("wrong_source_url", "source URL mismatch"),
        ("wrong_source_version", "source version mismatch"),
        ("extra_payload", "payload hash mismatch"),
    ],
)
def test_trade_deficit_lock_rejects_source_timing_and_payload_corruption(
    case: str,
    message: str,
) -> None:
    values = _lock_values()
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "census.bea.ft900.other"
    elif case == "wrong_publisher":
        first["source"]["publisher"] = "Other publisher"
    elif case == "wrong_temporal":
        first["source"]["temporal_coverage"] = "immutable_event"
    elif case == "wrong_license":
        first["source"]["license_class"] = "redistributable"
    elif case == "wrong_redistribution":
        first["source"]["redistribution_note"] = "fabricated permission"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "census_bea_ft900:other"
    elif case == "wrong_schema":
        first["payload_schema_version"] = "1.1.0"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_rule":
        first["interval"]["availability_rule"] = "current response headers"
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-03-06T13:29:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-03-06T13:30:01Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = "2020-03-06T13:30:00Z"
    elif case == "wrong_valid_to":
        first["interval"]["valid_to"] = "2020-02-01T00:00:00Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-01-02T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-03-06T13:30:01Z"
    elif case == "wrong_ingested":
        first["interval"]["ingested_at"] = "2026-08-14T06:19:02Z"
    elif case == "retrieved_before_release":
        first["source"]["retrieved_at"] = "2020-03-06T13:29:59Z"
        first["interval"]["ingested_at"] = "2020-03-06T13:29:59Z"
    elif case == "retrieved_after_build":
        first["source"]["retrieved_at"] = "2026-08-14T07:00:01Z"
        first["interval"]["ingested_at"] = "2026-08-14T07:00:01Z"
    elif case == "wrong_source_url":
        first["source"]["url"] = "https://www.census.gov/foreign-trade/other.pdf"
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "CENSUS-BEA-FT900:wrong"
    elif case == "extra_payload":
        first["payload"]["fabricated"] = True

    with pytest.raises((ValidationError, ValueError), match=message):
        TradeDeficitLevelBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_trade_deficit_lock_rejects_hash_role_and_decision_snapshot_corruption() -> None:
    wrong_hashes = _lock_values()
    wrong_hashes["source_response_sha256s"][0] = "0" * 64
    with pytest.raises(ValidationError, match="source hash set"):
        TradeDeficitLevelBoundaryInputLock.model_validate(wrong_hashes)

    swapped_roles = _lock_values()
    roles = swapped_roles["roles"]
    roles["january_release_snapshot"], roles["february_decision_snapshot"] = (
        roles["february_decision_snapshot"],
        roles["january_release_snapshot"],
    )
    with pytest.raises(ValidationError, match="publication time mismatch"):
        TradeDeficitLevelBoundaryInputLock.model_validate(swapped_roles)

    revised_january = _lock_values()
    february = revised_january["records"][1]
    assert february["payload"]["reference_month"] == "2020-02"
    february["payload"]["prior_month_revised_deficit_million_dollars"] = 45_338
    with pytest.raises(ValidationError, match=r"prior_month_revised.* mismatch"):
        TradeDeficitLevelBoundaryInputLock.model_validate(revised_january)

    wrong_lock_hash = _lock_values()
    wrong_lock_hash["title"] = f"{wrong_lock_hash['title']} changed"
    with pytest.raises(ValidationError, match="lock_sha256"):
        TradeDeficitLevelBoundaryInputLock.model_validate(wrong_lock_hash)


@pytest.mark.integration
def test_trade_deficit_lock_create_is_deterministic_and_self_hashing() -> None:
    values = _lock_values()
    original_hash = values.pop("lock_sha256")
    rebuilt = TradeDeficitLevelBoundaryInputLock.create(values)
    assert rebuilt.lock_sha256 == original_hash
    assert rebuilt.model_dump(mode="json") == _lock_values()


def _lock_values() -> dict[str, Any]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
