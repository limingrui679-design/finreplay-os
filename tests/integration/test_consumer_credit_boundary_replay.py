from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    ConsumerCreditBoundaryInputLock,
    OfficialEventLock,
    build_consumer_credit_boundary_replay_spec,
    load_consumer_credit_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/fed-g19-2020/input-lock.json")
EVENT_PATH = Path("scenarios/fed-g19-2020/event-lock.json")
PACK_PATH = Path("verification/replaypacks/fed-g19-2020")
CODE_COMMIT = "16c3446142e5b1647becd36066f7f46f4553e9cf"


@pytest.mark.integration
def test_consumer_credit_boundary_runs_four_engines_with_exact_values() -> None:
    lock = load_consumer_credit_boundary_input_lock(LOCK_PATH)
    spec = build_consumer_credit_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}

    assert set(artifacts) == {
        EngineName.TIMEVAULT,
        EngineName.SHOCKCOMPILER,
        EngineName.TRIALCOURT,
        EngineName.REPLAYSTUDIO,
    }
    assert spec.distinct_input_records == 2
    assert spec.derived_records == 6
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)
    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert shock.payload["known_changes"] == {
        "january_revised_change_basis_points": -270,
        "february_preliminary_change_basis_points": 460,
        "known_increase_basis_points": 730,
        "lower_change_basis_points": 460,
        "upper_change_basis_points": 1_190,
        "range_width_basis_points": 730,
    }
    assert shock.payload["naive_baseline"] == {
        "next_revolving_credit_change_annual_rate_basis_points": 460,
        "definition": "persistence of the latest April 7 G.19 table value",
    }
    assert shock.payload["bound_construction"] == {
        "lower_change_basis_points": 460,
        "upper_change_basis_points": 1_190,
        "range_width_basis_points": 730,
        "known_increase_basis_points": 730,
        "endpoint_method": "latest_persistence_or_repeat_known_increase",
        "table_values_not_rounded_headline_fractions": True,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {"lower": 460.0, "upper": 1_190.0}
    january = next(
        record for record in lock.records if record.record_id == lock.roles.january_revised_change
    )
    february = next(
        record
        for record in lock.records
        if record.record_id == lock.roles.february_preliminary_change
    )
    assert january.payload["estimate_status"] == "revised"
    assert january.payload["previous_release_same_reference_revolving_change_basis_points"] == -330
    assert january.payload["revision_delta_basis_points"] == 60
    assert february.payload["estimate_status"] == "preliminary"
    assert february.payload["previous_release_same_reference_revolving_change_basis_points"] is None
    trial = artifacts[EngineName.TRIALCOURT]
    assert trial.payload["decision"]["disposition"] == TrialDisposition.REJECT.value
    assert len(trial.payload["decision"]["findings"]) == 6
    assert trial.payload["manifest"]["rejected_decisions"] == 1
    assert ReplayStudio().compile(spec).pack_sha256 == ReplayStudio().verify(PACK_PATH).pack_sha256


@pytest.mark.integration
def test_march_event_is_later_disjoint_revised_and_breaches_lower_bound() -> None:
    lock = load_consumer_credit_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-05-07"
    assert record.payload["reference_month"] == "2020-03"
    assert record.payload["release_series"] == "G.19 Consumer Credit"
    assert record.payload["value_basis_points"] == -3_090
    assert record.payload["reported_revolving_change_percent"] == "-30.9"
    assert record.payload["estimate_status"] == "preliminary"
    assert record.payload["release_snapshot_revolving_change_basis_points"] == {
        "2020-01": -370,
        "2020-02": 360,
        "2020-03": -3_090,
    }
    assert record.payload["release_snapshot_estimate_statuses"] == {
        "2020-01": "revised",
        "2020-02": "revised",
        "2020-03": "preliminary",
    }
    assert record.payload["release_snapshot_previous_release_same_reference_basis_points"] == {
        "2020-01": -270,
        "2020-02": 460,
        "2020-03": None,
    }
    assert record.payload["release_snapshot_revision_delta_basis_points"] == {
        "2020-01": -100,
        "2020-02": -100,
        "2020-03": None,
    }
    assert record.payload["simple_annual_rate_from_unrounded_data"] is True
    assert record.payload["pdf_table_snapshot_verified"] is True
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert 460 - record.payload["value_basis_points"] == 3_550
    january = next(
        item for item in lock.records if item.record_id == lock.roles.january_revised_change
    )
    february = next(
        item for item in lock.records if item.record_id == lock.roles.february_preliminary_change
    )
    assert january.payload["value_basis_points"] == -270
    assert february.payload["value_basis_points"] == 460


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived consumer-credit facts"),
        ("wrong_publisher", "publisher mismatch"),
        ("wrong_license", "license boundary mismatch"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("wrong_payload_schema", "payload schema mismatch"),
        ("low_confidence", "timing must be deterministic"),
        ("wrong_published", "publication time mismatch"),
        ("wrong_available", "availability time mismatch"),
        ("wrong_revised", "revision clock mismatch"),
        ("wrong_valid", "valid time mismatch"),
        ("wrong_vintage", "source vintage mismatch"),
        ("wrong_source_hash", "source hashes do not match records"),
        ("wrong_source_url", "source URL mismatch"),
        ("wrong_source_version", "source version mismatch"),
        ("release_date", "release_date mismatch"),
        ("release_reference_month", "release_reference_month mismatch"),
        ("reference_month", "reference_month mismatch"),
        ("release_series", "release_series mismatch"),
        ("metric", "metric mismatch"),
        ("value_basis_points", "value_basis_points mismatch"),
        ("reported_total_change_percent", "reported_total_change_percent mismatch"),
        ("reported_revolving_change_percent", "reported_revolving_change_percent mismatch"),
        ("reported_nonrevolving_change_percent", "reported_nonrevolving_change_percent mismatch"),
        (
            "reported_total_flow_annual_rate_billion_dollars",
            "reported_total_flow_annual_rate_billion_dollars mismatch",
        ),
        (
            "reported_revolving_flow_annual_rate_billion_dollars",
            "reported_revolving_flow_annual_rate_billion_dollars mismatch",
        ),
        (
            "reported_nonrevolving_flow_annual_rate_billion_dollars",
            "reported_nonrevolving_flow_annual_rate_billion_dollars mismatch",
        ),
        (
            "reported_total_outstanding_billion_dollars",
            "reported_total_outstanding_billion_dollars mismatch",
        ),
        (
            "reported_revolving_outstanding_billion_dollars",
            "reported_revolving_outstanding_billion_dollars mismatch",
        ),
        (
            "reported_nonrevolving_outstanding_billion_dollars",
            "reported_nonrevolving_outstanding_billion_dollars mismatch",
        ),
        ("total_flow_tenths_billion_dollars", "total_flow_tenths_billion_dollars mismatch"),
        (
            "revolving_flow_tenths_billion_dollars",
            "revolving_flow_tenths_billion_dollars mismatch",
        ),
        (
            "nonrevolving_flow_tenths_billion_dollars",
            "nonrevolving_flow_tenths_billion_dollars mismatch",
        ),
        (
            "total_outstanding_tenths_billion_dollars",
            "total_outstanding_tenths_billion_dollars mismatch",
        ),
        (
            "revolving_outstanding_tenths_billion_dollars",
            "revolving_outstanding_tenths_billion_dollars mismatch",
        ),
        (
            "nonrevolving_outstanding_tenths_billion_dollars",
            "nonrevolving_outstanding_tenths_billion_dollars mismatch",
        ),
        ("estimate_status", "estimate_status mismatch"),
        ("status_marker", "status_marker mismatch"),
        (
            "previous_release_same_reference_revolving_change_basis_points",
            "previous_release_same_reference_revolving_change_basis_points mismatch",
        ),
        ("revision_delta_basis_points", "revision_delta_basis_points mismatch"),
        (
            "release_snapshot_revolving_change_basis_points",
            "release_snapshot_revolving_change_basis_points mismatch",
        ),
        (
            "release_snapshot_estimate_statuses",
            "release_snapshot_estimate_statuses mismatch",
        ),
        (
            "release_snapshot_previous_release_same_reference_basis_points",
            "release_snapshot_previous_release_same_reference_basis_points mismatch",
        ),
        (
            "release_snapshot_revision_delta_basis_points",
            "release_snapshot_revision_delta_basis_points mismatch",
        ),
        ("release_time_local", "release_time_local mismatch"),
        ("release_timezone", "release_timezone mismatch"),
        ("release_timezone_abbreviation", "release_timezone_abbreviation mismatch"),
        ("official_release_at", "official_release_at mismatch"),
        ("unit", "unit mismatch"),
        ("snapshot_semantics", "snapshot_semantics mismatch"),
        (
            "simple_annual_rate_from_unrounded_data",
            "simple_annual_rate_from_unrounded_data mismatch",
        ),
        ("pdf_table_snapshot_verified", "pdf_table_snapshot_verified mismatch"),
        ("release_pdf_url", "release_pdf_url mismatch"),
        ("release_pdf_sha256", "release_pdf_sha256 mismatch"),
        ("release_pdf_pages", "release_pdf_pages mismatch"),
        (
            "release_pdf_page_rotation_degrees",
            "release_pdf_page_rotation_degrees mismatch",
        ),
        ("availability_method", "availability_method mismatch"),
    ],
)
def test_consumer_credit_lock_rejects_source_timing_and_value_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "federalreserve.other"
    elif case == "wrong_publisher":
        first["source"]["publisher"] = "Other publisher"
    elif case == "wrong_license":
        first["source"]["license_class"] = "redistributable"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "federal_reserve_g19:other"
    elif case == "wrong_payload_schema":
        first["payload_schema_version"] = "1.0.0"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-04-07T18:59:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-04-07T19:00:01Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = None
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-01-02T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-04-07T19:00:01Z"
    elif case == "wrong_source_hash":
        first["source"]["sha256"] = "f" * 64
    elif case == "wrong_source_url":
        first["source"]["url"] = "https://www.federalreserve.gov/releases/g19/other.pdf"
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "FED-G19:fabricated"
    else:
        first["payload"][case] = _corrupt(first["payload"][case])
    with pytest.raises(ValidationError, match=message):
        ConsumerCreditBoundaryInputLock.model_validate(values)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("early_build", "build_epoch cannot precede"),
        ("wrong_decision", "decision_time must equal the April release"),
        ("unsorted_records", "records must be unique and sorted"),
        ("duplicate_records", "records must be unique and sorted"),
        ("role_coverage", "roles must cover"),
        ("duplicate_roles", "role record IDs must be unique"),
        ("wrong_source_response_hash", "source hash set does not match April PDF"),
        ("naive_decision_time", "decision_time must be timezone-aware"),
        ("naive_build_epoch", "build_epoch must be timezone-aware"),
    ],
)
def test_consumer_credit_lock_rejects_manifest_role_and_clock_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2020-04-07T18:59:59Z"
    elif case == "wrong_decision":
        values["decision_time"] = "2020-04-07T19:00:01Z"
    elif case == "unsorted_records":
        values["records"].reverse()
    elif case == "duplicate_records":
        values["records"][1] = values["records"][0]
    elif case == "role_coverage":
        values["roles"]["january_revised_change"] = "missing-record-id"
    elif case == "duplicate_roles":
        values["roles"]["january_revised_change"] = values["roles"][
            "february_preliminary_change"
        ]
    elif case == "wrong_source_response_hash":
        values["source_response_sha256s"] = ["f" * 64]
    elif case == "naive_decision_time":
        values["decision_time"] = "2020-04-07T19:00:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-14T02:00:00"
    with pytest.raises(ValidationError, match=message):
        ConsumerCreditBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_consumer_credit_lock_creation_loading_hash_and_runtime_guards(
    tmp_path: Path,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = ConsumerCreditBoundaryInputLock.create(values)
    assert recreated == ConsumerCreditBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match=r"invalid G\.19 consumer-credit input lock"):
        load_consumer_credit_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match=r"invalid G\.19 consumer-credit input lock"):
        load_consumer_credit_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated Federal Reserve G.19 consumer-credit boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        ConsumerCreditBoundaryInputLock.model_validate(tampered)

    lock = load_consumer_credit_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    second = lock.records[1]
    same_value = second.model_copy(
        update={"payload": {**second.payload, "value_basis_points": -270}}
    )
    bypassed = lock.model_copy(update={"records": (first, same_value)})
    with pytest.raises(ValueError, match="must establish a positive change step"):
        build_consumer_credit_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


@pytest.mark.integration
@pytest.mark.parametrize("invalid_value", [True, -270.5, -100_001, 100_001])
def test_consumer_credit_replay_fails_closed_for_invalid_bypassed_change(
    invalid_value: object,
) -> None:
    lock = load_consumer_credit_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_basis_points": invalid_value}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match=r"change must be integer|outside supported range"):
        build_consumer_credit_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


def _corrupt(value: Any) -> Any:
    if value is None:
        return 1
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return f"{value}-corrupt"
    if isinstance(value, dict):
        changed = dict(value)
        key = sorted(changed)[0]
        changed[key] = _corrupt(changed[key])
        return changed
    raise AssertionError(f"unsupported test value: {value!r}")
