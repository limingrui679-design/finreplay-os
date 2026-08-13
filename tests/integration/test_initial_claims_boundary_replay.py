from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    InitialClaimsBoundaryInputLock,
    OfficialEventLock,
    build_initial_claims_boundary_replay_spec,
    load_initial_claims_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/dol-ui-2020/input-lock.json")
EVENT_PATH = Path("scenarios/dol-ui-2020/event-lock.json")
PACK_PATH = Path("verification/replaypacks/dol-ui-2020")
CODE_COMMIT = "7190b14574c69b24884145ede97abc5135c637c8"


@pytest.mark.integration
def test_initial_claims_boundary_runs_four_engines_with_exact_values() -> None:
    lock = load_initial_claims_boundary_input_lock(LOCK_PATH)
    spec = build_initial_claims_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}

    assert set(artifacts) == {
        EngineName.TIMEVAULT,
        EngineName.SHOCKCOMPILER,
        EngineName.TRIALCOURT,
        EngineName.REPLAYSTUDIO,
    }
    assert spec.distinct_input_records == 2
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)
    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert shock.payload["known_claims"] == {
        "march07_claims_persons": 211_000,
        "march14_claims_persons": 281_000,
        "known_weekly_increase_persons": 70_000,
        "claims_lower_persons": 281_000,
        "claims_upper_persons": 351_000,
        "claims_range_width_persons": 70_000,
    }
    assert shock.payload["naive_baseline"] == {
        "next_reported_seasonally_adjusted_initial_claims_persons": 281_000,
        "definition": "persistence of the latest known DOL initial-claims value",
    }
    assert shock.payload["bound_construction"] == {
        "lower_claims_persons": 281_000,
        "upper_claims_persons": 351_000,
        "range_width_persons": 70_000,
        "known_weekly_increase_persons": 70_000,
        "endpoint_method": "latest_persistence_or_repeat_known_weekly_increase",
        "probability_assigned": False,
        "future_event_used": False,
    }
    trial = artifacts[EngineName.TRIALCOURT]
    assert trial.payload["decision"]["disposition"] == TrialDisposition.REJECT.value
    assert trial.payload["manifest"]["rejected_decisions"] == 1
    assert ReplayStudio().compile(spec).pack_sha256 == ReplayStudio().verify(
        PACK_PATH
    ).pack_sha256


@pytest.mark.integration
def test_march21_event_is_exact_later_disjoint_revised_and_breaches_range() -> None:
    lock = load_initial_claims_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-03-26"
    assert record.payload["week_ending"] == "2020-03-21"
    assert record.payload["value_persons"] == 3_283_000
    assert record.payload["prior_level_persons"] == 282_000
    assert record.payload["reported_change_persons"] == 3_001_000
    assert record.payload["prior_level_revision_old_persons"] == 281_000
    assert record.payload["prior_level_revision_new_persons"] == 282_000
    assert record.payload["prior_level_revision_delta_persons"] == 1_000
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert record.payload["value_persons"] - 351_000 == 2_932_000
    march14 = next(
        item for item in lock.records if item.record_id == lock.roles.march14_claims
    )
    assert march14.payload["value_persons"] == 281_000
    assert march14.payload["prior_level_revision_new_persons"] is None


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived release facts"),
        ("wrong_publisher", "publisher mismatch"),
        ("wrong_coverage", "versioned snapshots"),
        ("wrong_license", "license boundary mismatch"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("low_confidence", "timing must be deterministic"),
        ("wrong_published", "publication time mismatch"),
        ("wrong_available", "availability time mismatch"),
        ("future", "post-decision input"),
        ("wrong_valid", "valid time mismatch"),
        ("wrong_vintage", "source vintage mismatch"),
        ("wrong_source_url", "source URL mismatch"),
        ("wrong_source_version", "source version mismatch"),
        ("wrong_release_date", "release-date mismatch"),
        ("wrong_week", "week-ending mismatch"),
        ("wrong_metric", "metric mismatch"),
        ("wrong_unit", "unit mismatch"),
        ("arithmetic_flag", "arithmetic flag mismatch"),
        ("wrong_method", "availability method mismatch"),
        ("wrong_pdf_modified", "PDF modification time mismatch"),
        ("wrong_release_number", "release number mismatch"),
        ("wrong_release_time", "release time mismatch"),
        ("wrong_snapshot", "snapshot semantics mismatch"),
        ("wrong_value", "value_persons mismatch"),
        ("wrong_prior", "prior_level_persons mismatch"),
        ("wrong_change", "reported_change_persons mismatch"),
        ("wrong_direction", "reported_direction mismatch"),
        ("wrong_prior_status", "prior_level_status mismatch"),
        ("wrong_revision_old", "prior_level_revision_old_persons mismatch"),
        ("wrong_revision_new", "prior_level_revision_new_persons mismatch"),
        ("wrong_revision_delta", "prior_level_revision_delta_persons mismatch"),
        ("wrong_annual_revision", "annual_revision_release mismatch"),
        ("noninteger", "value_persons mismatch"),
        ("boolean_value", "value_persons mismatch"),
        ("out_of_range", "value_persons mismatch"),
    ],
)
def test_initial_claims_lock_rejects_source_timing_revision_and_value_inflation(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "dol.other"
    elif case == "wrong_publisher":
        first["source"]["publisher"] = "Other publisher"
    elif case == "wrong_coverage":
        first["source"]["temporal_coverage"] = "latest_only"
        first["source"]["vintage_as_of"] = None
    elif case == "wrong_license":
        first["source"]["license_class"] = "redistributable"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "dol_ui_claims:other"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-03-12T12:30:01Z"
        first["interval"]["available_at"] = "2020-03-12T12:30:10Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-03-12T12:30:11Z"
    elif case == "future":
        values["decision_time"] = "2020-03-12T12:30:09Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-03-06T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-03-12T12:30:09Z"
    elif case == "wrong_source_url":
        first["source"]["url"] = first["source"]["url"].replace(
            "eta20200432.pdf", "20200480.pdf"
        )
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "DOL-UI:fabricated"
    elif case == "wrong_release_date":
        first["payload"]["release_date"] = "2020-03-13"
    elif case == "wrong_week":
        first["payload"]["week_ending"] = "2020-03-06"
    elif case == "wrong_metric":
        first["payload"]["metric"] = "continued_claims"
    elif case == "wrong_unit":
        first["payload"]["unit"] = "Thousands of Persons"
    elif case == "arithmetic_flag":
        first["payload"]["arithmetic_verified"] = False
    elif case == "wrong_method":
        first["payload"]["availability_method"] = "embargo_only"
    elif case == "wrong_pdf_modified":
        first["payload"]["pdf_last_modified_at"] = "2020-03-12T12:30:09+00:00"
    elif case == "wrong_release_number":
        first["payload"]["release_number"] = "USDL 20-999-NAT"
    elif case == "wrong_release_time":
        first["payload"]["release_time_eastern"] = "08:29:59"
    elif case == "wrong_snapshot":
        first["payload"]["snapshot_semantics"] = "current revised value"
    elif case == "wrong_value":
        first["payload"]["value_persons"] = 211_001
    elif case == "wrong_prior":
        first["payload"]["prior_level_persons"] = 214_999
    elif case == "wrong_change":
        first["payload"]["reported_change_persons"] = -3_999
    elif case == "wrong_direction":
        first["payload"]["reported_direction"] = "increase"
    elif case == "wrong_prior_status":
        first["payload"]["prior_level_status"] = "unrevised"
    elif case == "wrong_revision_old":
        first["payload"]["prior_level_revision_old_persons"] = 215_999
    elif case == "wrong_revision_new":
        first["payload"]["prior_level_revision_new_persons"] = 215_001
    elif case == "wrong_revision_delta":
        first["payload"]["prior_level_revision_delta_persons"] = -999
    elif case == "wrong_annual_revision":
        first["payload"]["annual_revision_release"] = True
    elif case == "noninteger":
        first["payload"]["value_persons"] = 211_000.5
    elif case == "boolean_value":
        first["payload"]["value_persons"] = True
    elif case == "out_of_range":
        first["payload"]["value_persons"] = 100_000_001
    with pytest.raises(ValidationError, match=message):
        InitialClaimsBoundaryInputLock.model_validate(values)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("early_build", "build_epoch cannot precede"),
        ("unsorted_records", "records must be unique and sorted"),
        ("duplicate_records", "records must be unique and sorted"),
        ("role_coverage", "roles must cover"),
        ("duplicate_roles", "role record IDs must be unique"),
        ("unsorted_hashes", "source hashes must be unique and sorted"),
        ("duplicate_hashes", "source hashes must be unique and sorted"),
        ("hash_set_mismatch", "source hashes do not match"),
        ("naive_decision_time", "decision_time must be timezone-aware"),
        ("naive_build_epoch", "build_epoch must be timezone-aware"),
    ],
)
def test_initial_claims_lock_rejects_manifest_role_and_clock_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2020-03-20T11:59:59Z"
    elif case == "unsorted_records":
        values["records"].reverse()
    elif case == "duplicate_records":
        values["records"][1] = values["records"][0]
    elif case == "role_coverage":
        values["roles"]["march07_claims"] = "missing-record-id"
    elif case == "duplicate_roles":
        values["roles"]["march07_claims"] = values["roles"]["march14_claims"]
    elif case == "unsorted_hashes":
        values["source_response_sha256s"].reverse()
    elif case == "duplicate_hashes":
        values["source_response_sha256s"][1] = values["source_response_sha256s"][0]
    elif case == "hash_set_mismatch":
        values["source_response_sha256s"] = sorted(
            [values["source_response_sha256s"][0], "f" * 64]
        )
    elif case == "naive_decision_time":
        values["decision_time"] = "2020-03-20T12:00:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-13T07:15:00"
    with pytest.raises(ValidationError, match=message):
        InitialClaimsBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_initial_claims_lock_creation_loading_hash_and_runtime_guards(
    tmp_path: Path,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = InitialClaimsBoundaryInputLock.create(values)
    assert recreated == InitialClaimsBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid DOL initial-claims input lock"):
        load_initial_claims_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match="invalid DOL initial-claims input lock"):
        load_initial_claims_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated DOL initial-claims boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        InitialClaimsBoundaryInputLock.model_validate(tampered)

    lock = load_initial_claims_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    second = lock.records[1]
    same_value = second.model_copy(
        update={"payload": {**second.payload, "value_persons": 211_000}}
    )
    bypassed = lock.model_copy(update={"records": (first, same_value)})
    with pytest.raises(ValueError, match="must establish a positive weekly increase"):
        build_initial_claims_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_initial_claims_replay_fails_closed_if_bypassed_value_is_not_integer() -> None:
    lock = load_initial_claims_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_persons": True}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match="must be integer persons"):
        build_initial_claims_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)
