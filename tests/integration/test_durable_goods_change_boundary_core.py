from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    DurableGoodsChangeBoundaryInputLock,
    OfficialEventLock,
    build_durable_goods_change_boundary_replay_spec,
    load_durable_goods_change_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/census-m3-2020/input-lock.json")
EVENT_PATH = Path("scenarios/census-m3-2020/event-lock.json")
PACK_PATH = Path("verification/replaypacks/census-m3-2020")
CODE_COMMIT = "f31f0a8a4d53072d70e5a8597542bd1a37975165"


@pytest.mark.integration
def test_durable_goods_boundary_runs_four_engines_with_exact_values() -> None:
    lock = load_durable_goods_change_boundary_input_lock(LOCK_PATH)
    spec = build_durable_goods_change_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}

    assert set(artifacts) == {
        EngineName.TIMEVAULT,
        EngineName.SHOCKCOMPILER,
        EngineName.TRIALCOURT,
        EngineName.REPLAYSTUDIO,
    }
    assert spec.distinct_input_records == 2
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)
    timevault = artifacts[EngineName.TIMEVAULT]
    assert timevault.payload["value_semantics"].startswith(
        "total durable-goods new-orders month-over-month changes"
    )
    assert timevault.payload["release_time_rule"] == (
        "08:30 America/New_York from each dated report"
    )
    assert timevault.payload["current_pdf_byte_identity_at_release_claimed"] is False

    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert len(shock.source_hashes) == 2
    assert set(shock.source_hashes) == set(lock.source_evidence_sha256s)
    assert shock.payload["known_initial_release_changes"] == {
        "january_initial_change_basis_points": -20,
        "february_initial_change_basis_points": 120,
        "known_initial_increase_basis_points": 140,
        "lower_change_basis_points": 120,
        "upper_change_basis_points": 260,
        "range_width_basis_points": 140,
    }
    assert shock.payload["naive_baseline"] == {
        "next_total_durable_goods_new_orders_change_basis_points": 120,
        "definition": "persistence of the February initial M3 new-orders change",
    }
    assert shock.payload["bound_construction"] == {
        "lower_change_basis_points": 120,
        "upper_change_basis_points": 260,
        "range_width_basis_points": 140,
        "known_initial_increase_basis_points": 140,
        "endpoint_method": ("latest_initial_change_persistence_or_repeat_known_initial_increase"),
        "official_confidence_interval_used": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {"lower": 120.0, "upper": 260.0}

    january = next(
        record for record in lock.records if record.record_id == lock.roles.january_initial_change
    )
    february = next(
        record for record in lock.records if record.record_id == lock.roles.february_initial_change
    )
    assert january.payload["value_basis_points"] == -20
    assert january.payload["value_percent"] == "-0.2"
    assert january.payload["value_million_dollars"] == 246_199
    assert january.payload["release_timezone_abbreviation"] == "EST"
    assert february.payload["value_basis_points"] == 120
    assert february.payload["value_percent"] == "1.2"
    assert february.payload["value_million_dollars"] == 249_409
    assert february.payload["release_snapshot_revision_delta_basis_points"] == {
        "2020-01": 30,
        "2020-02": None,
    }
    for record in lock.records:
        assert record.payload["probability_sample"] is False
        assert record.payload["confidence_intervals_computable"] is False
        assert record.payload["adjusted_for_price_changes"] is False
        assert record.payload["current_pdf_byte_identity_at_release_claimed"] is False

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
    compiled = ReplayStudio().compile(spec)
    verified = ReplayStudio().verify(PACK_PATH)
    assert compiled.pack_sha256 == verified.pack_sha256
    assert verified.replay_manifest.code_commit == CODE_COMMIT
    assert compiled.spec.code_commit == CODE_COMMIT


@pytest.mark.integration
def test_march_event_is_disjoint_revised_and_breaches_fixed_lower_bound() -> None:
    lock = load_durable_goods_change_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-04-24"
    assert record.payload["reference_month"] == "2020-03"
    assert record.payload["value_basis_points"] == -1_440
    assert record.payload["value_percent"] == "-14.4"
    assert record.payload["value_million_dollars"] == 213_184
    assert record.payload["release_snapshot_change_basis_points"] == {
        "2020-01": 10,
        "2020-02": 110,
        "2020-03": -1_440,
    }
    assert record.payload["release_snapshot_previous_change_basis_points"] == {
        "2020-01": 10,
        "2020-02": 120,
        "2020-03": None,
    }
    assert record.payload["release_snapshot_revision_delta_basis_points"] == {
        "2020-01": 0,
        "2020-02": -10,
        "2020-03": None,
    }
    assert record.payload["covid_publication_standard_statement_present"] is True
    assert record.payload["probability_sample"] is False
    assert record.payload["report_pdf_metadata_modified_after_release"] is True
    assert record.payload["report_pdf_metadata_modification_date"] == ("D:20200527104843-04'00'")
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert 120 - record.payload["value_basis_points"] == 1_560


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived M3 facts"),
        ("wrong_publisher", "publisher mismatch"),
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
        ("wrong_revised", "initial monthly-release facts"),
        ("wrong_valid_to", "open valid-time intervals"),
        ("wrong_valid", "valid time mismatch"),
        ("wrong_vintage", "source vintage mismatch"),
        ("wrong_ingested", "retrieval and ingestion times must agree"),
        ("retrieved_before_release", "ingested_at must not precede historical availability"),
        ("wrong_source_url", "source URL mismatch"),
        ("wrong_source_version", "source version mismatch"),
        ("extra_payload", "payload field set mismatch"),
    ],
)
def test_durable_goods_lock_rejects_source_and_timing_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "census.m3.other"
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
        first["entity_id"] = "census_m3:other"
    elif case == "wrong_schema":
        first["payload_schema_version"] = "1.1.0"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_rule":
        first["interval"]["availability_rule"] = "current headers"
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-02-27T13:29:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-02-27T13:30:01Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = "2020-02-27T13:30:00Z"
    elif case == "wrong_valid_to":
        first["interval"]["valid_to"] = "2020-02-01T00:00:00Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-01-02T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-02-27T13:30:01Z"
    elif case == "wrong_ingested":
        first["interval"]["ingested_at"] = "2026-08-14T05:20:37Z"
    elif case == "retrieved_before_release":
        first["source"]["retrieved_at"] = "2020-02-27T13:29:59Z"
        first["interval"]["ingested_at"] = "2020-02-27T13:29:59Z"
    elif case == "wrong_source_url":
        first["source"]["url"] = "https://www.census.gov/manufacturing/m3/other.pdf"
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "CENSUS-M3-DURABLE:fabricated"
    elif case == "extra_payload":
        first["payload"]["fabricated"] = True
    with pytest.raises(ValidationError, match=message):
        DurableGoodsChangeBoundaryInputLock.model_validate(values)


@pytest.mark.integration
@pytest.mark.parametrize(
    "field",
    [
        "adjusted_for_price_changes",
        "annual_benchmark_notice_present",
        "availability_method",
        "confidence_intervals_computable",
        "covid_publication_standard_statement_present",
        "current_pdf_byte_identity_at_release_claimed",
        "excluding_defense_change_basis_points",
        "excluding_transportation_change_basis_points",
        "full_report_release_date",
        "full_report_release_time_label",
        "inventories_change_basis_points",
        "inventories_value_million_dollars",
        "metric",
        "new_and_unfilled_orders_exclude_semiconductor_manufacturing",
        "next_advance_release_date",
        "next_advance_release_time_label",
        "official_release_at",
        "older_month_change_basis_points",
        "older_month_value_million_dollars",
        "pdf_table_snapshot_verified",
        "prior_month",
        "prior_month_revised_change_basis_points",
        "prior_month_revised_value_million_dollars",
        "probability_sample",
        "reference_month",
        "release_code",
        "release_date",
        "release_number",
        "release_series",
        "release_snapshot_change_basis_points",
        "release_snapshot_level_revision_delta_million_dollars",
        "release_snapshot_new_orders_million_dollars",
        "release_snapshot_previous_change_basis_points",
        "release_snapshot_previous_new_orders_million_dollars",
        "release_snapshot_revision_delta_basis_points",
        "release_time_local",
        "release_timezone",
        "release_timezone_abbreviation",
        "report_pdf_metadata_creation_date",
        "report_pdf_metadata_modification_date",
        "report_pdf_metadata_modified_after_release",
        "report_pdf_page_dimensions_points",
        "report_pdf_page_rotations",
        "report_pdf_pages",
        "report_pdf_sha256",
        "report_pdf_url",
        "reported_headline_delta_billion_dollars",
        "reported_rounded_value_billion_dollars",
        "sampling_error_measurable",
        "seasonally_adjusted",
        "shipments_change_basis_points",
        "shipments_value_million_dollars",
        "snapshot_semantics",
        "statistical_significance_measurable",
        "text_describes_not_adjusted_for_inflation",
        "transportation_equipment_change_basis_points",
        "transportation_equipment_rounded_level_million_dollars",
        "unfilled_orders_change_basis_points",
        "unfilled_orders_value_million_dollars",
        "unit",
        "value_basis_points",
        "value_million_dollars",
        "value_percent",
    ],
)
def test_durable_goods_lock_rejects_every_payload_field_corruption(field: str) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    payload = values["records"][0]["payload"]
    payload[field] = _corrupt(payload[field])
    with pytest.raises(ValidationError, match=field):
        DurableGoodsChangeBoundaryInputLock.model_validate(values)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("early_build", "build_epoch cannot precede"),
        ("wrong_decision", "decision_time must equal the February-data release"),
        ("unsorted_records", "records must be unique and sorted"),
        ("duplicate_records", "records must be unique and sorted"),
        ("role_coverage", "roles must cover"),
        ("duplicate_roles", "role record IDs must be unique"),
        ("unsorted_hashes", "evidence hashes must be unique and sorted"),
        ("duplicate_hashes", "evidence hashes must be unique and sorted"),
        ("wrong_hash_set", "evidence hash set does not match"),
        ("record_hash_mismatch", "PDF hashes do not match locked records"),
        ("naive_decision_time", "decision_time must be timezone-aware"),
        ("naive_build_epoch", "build_epoch must be timezone-aware"),
    ],
)
def test_durable_goods_lock_rejects_manifest_role_and_clock_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2020-03-25T12:29:59Z"
    elif case == "wrong_decision":
        values["decision_time"] = "2020-03-25T12:30:01Z"
    elif case == "unsorted_records":
        values["records"].reverse()
    elif case == "duplicate_records":
        values["records"][1] = values["records"][0]
    elif case == "role_coverage":
        values["roles"]["january_initial_change"] = "missing-record-id"
    elif case == "duplicate_roles":
        values["roles"]["january_initial_change"] = values["roles"]["february_initial_change"]
    elif case == "unsorted_hashes":
        values["source_evidence_sha256s"].reverse()
    elif case == "duplicate_hashes":
        values["source_evidence_sha256s"][1] = values["source_evidence_sha256s"][0]
    elif case == "wrong_hash_set":
        values["source_evidence_sha256s"][-1] = "f" * 64
        values["source_evidence_sha256s"].sort()
    elif case == "record_hash_mismatch":
        values["records"][0]["source"]["sha256"] = values["records"][1]["source"]["sha256"]
    elif case == "naive_decision_time":
        values["decision_time"] = "2020-03-25T12:30:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-14T05:30:00"
    with pytest.raises(ValidationError, match=message):
        DurableGoodsChangeBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_durable_goods_lock_creation_loading_hash_and_runtime_guards(
    tmp_path: Path,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = DurableGoodsChangeBoundaryInputLock.create(values)
    assert recreated == DurableGoodsChangeBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Census M3 durable-goods input lock"):
        load_durable_goods_change_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match="invalid Census M3 durable-goods input lock"):
        load_durable_goods_change_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated Census M3 durable-goods boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        DurableGoodsChangeBoundaryInputLock.model_validate(tampered)

    lock = load_durable_goods_change_boundary_input_lock(LOCK_PATH)
    first, second = lock.records
    same_value = second.model_copy(
        update={
            "payload": {
                **second.payload,
                "value_basis_points": first.payload["value_basis_points"],
            }
        }
    )
    bypassed = lock.model_copy(update={"records": (first, same_value)})
    with pytest.raises(ValueError, match="must establish a positive known increase"):
        build_durable_goods_change_boundary_replay_spec(
            bypassed,
            code_commit=CODE_COMMIT,
        )


@pytest.mark.integration
@pytest.mark.parametrize("invalid_value", [True, 30.5, -5_001, 5_001])
def test_durable_goods_replay_fails_closed_for_invalid_bypassed_change(
    invalid_value: object,
) -> None:
    lock = load_durable_goods_change_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_basis_points": invalid_value}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match=r"must be integer|outside the supported range"):
        build_durable_goods_change_boundary_replay_spec(
            bypassed,
            code_commit=CODE_COMMIT,
        )


def _corrupt(value: Any) -> Any:
    if value is None:
        return 1
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return f"{value}-corrupt"
    if isinstance(value, list):
        return [*value, "corrupt"]
    if isinstance(value, dict):
        return {**value, "corrupt": 1}
    raise AssertionError(f"unsupported test value: {value!r}")
