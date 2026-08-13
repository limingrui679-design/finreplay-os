from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    SOFRBoundaryInputLock,
    build_sofr_boundary_replay_spec,
    load_sofr_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/nyfed-sofr-2019/input-lock.json")
EVENT_PATH = Path("scenarios/nyfed-sofr-2019/event-lock.json")
PACK_PATH = Path("verification/replaypacks/nyfed-sofr-2019")
CODE_COMMIT = "4fe5c75323ee32731d29431ed568c7d16e9651c2"


@pytest.mark.integration
def test_sofr_boundary_runs_four_engines_with_exact_reported_and_inferred_values() -> None:
    lock = load_sofr_boundary_input_lock(LOCK_PATH)
    spec = build_sofr_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
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
    assert shock.payload["known_rates"] == {
        "september13_rate_basis_points": 220,
        "september16_rate_basis_points": 243,
        "rate_lower_basis_points": 220,
        "rate_upper_basis_points": 243,
        "rate_range_width_basis_points": 23,
    }
    assert shock.payload["naive_baseline"] == {
        "next_final_sofr_basis_points": 243,
        "definition": "persistence of the latest known final SOFR rate",
    }
    assert shock.payload["bound_construction"]["probability_assigned"] is False
    assert shock.payload["bound_construction"]["future_event_used"] is False
    trial = artifacts[EngineName.TRIALCOURT]
    assert trial.payload["decision"]["disposition"] == TrialDisposition.REJECT.value
    assert trial.payload["manifest"]["rejected_decisions"] == 1
    assert ReplayStudio().compile(spec).pack_sha256 == ReplayStudio().verify(PACK_PATH).pack_sha256


@pytest.mark.integration
def test_september17_event_is_exact_later_disjoint_and_breaches_prior_range() -> None:
    lock = load_sofr_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["effective_date"] == "2019-09-17"
    assert record.payload["publication_business_date"] == "2019-09-18"
    assert record.payload["reported_value_percent"] == "5.25"
    assert record.payload["value_basis_points"] == 525
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert record.payload["value_basis_points"] > 243
    assert record.payload["value_basis_points"] - 243 == 282


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only final New York Fed SOFR"),
        ("wrong_coverage", "final immutable rate events"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("low_confidence", "timing must be deterministic"),
        ("wrong_published", "publication time mismatch"),
        ("wrong_available", "availability time mismatch"),
        ("future", "post-decision input"),
        ("wrong_valid", "valid time mismatch"),
        ("wrong_vintage", "source finality mismatch"),
        ("wrong_effective_date", "effective-date mismatch"),
        ("wrong_publication_date", "publication-date mismatch"),
        ("wrong_rate_type", "rate type mismatch"),
        ("wrong_unit", "unit mismatch"),
        ("wrong_revision", "revision indicator mismatch"),
        ("ancillary_true", "ancillary-statistics boundary mismatch"),
        ("wrong_method", "availability method mismatch"),
        ("noninteger", "must be integer basis points"),
        ("boolean_value", "must be integer basis points"),
        ("out_of_range", "outside supported range"),
        ("reported_nonstring", "reported percent must be a string"),
        ("invalid_decimal", "reported percent must be decimal"),
        ("nonfinite_decimal", "percent and basis points mismatch"),
        ("percent_mismatch", "percent and basis points mismatch"),
    ],
)
def test_sofr_lock_rejects_temporal_source_and_rate_inflation(case: str, message: str) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "nyfed.other"
    elif case == "wrong_coverage":
        first["source"]["temporal_coverage"] = "latest_only"
        first["source"]["vintage_as_of"] = None
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "nyfed_reference_rate:OTHER"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2019-09-16T19:00:01Z"
        first["interval"]["available_at"] = "2019-09-16T19:00:01Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2019-09-16T19:00:01Z"
    elif case == "future":
        values["decision_time"] = "2019-09-16T18:59:59Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2019-09-12T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2019-09-16T18:59:59Z"
    elif case == "wrong_effective_date":
        first["payload"]["effective_date"] = "2019-09-12"
    elif case == "wrong_publication_date":
        first["payload"]["publication_business_date"] = "2019-09-17"
    elif case == "wrong_rate_type":
        first["payload"]["rate_type"] = "TGCR"
    elif case == "wrong_unit":
        first["payload"]["unit"] = "Percent"
    elif case == "wrong_revision":
        first["payload"]["revision_indicator"] = "R"
    elif case == "ancillary_true":
        first["payload"]["ancillary_statistics_normalized"] = True
    elif case == "wrong_method":
        first["payload"]["availability_method"] = "date_only"
    elif case == "noninteger":
        first["payload"]["value_basis_points"] = 220.5
    elif case == "boolean_value":
        first["payload"]["value_basis_points"] = True
    elif case == "out_of_range":
        first["payload"]["value_basis_points"] = 10_001
    elif case == "reported_nonstring":
        first["payload"]["reported_value_percent"] = 2.20
    elif case == "invalid_decimal":
        first["payload"]["reported_value_percent"] = "not-a-number"
    elif case == "nonfinite_decimal":
        first["payload"]["reported_value_percent"] = "NaN"
    elif case == "percent_mismatch":
        first["payload"]["reported_value_percent"] = "2.21"
    with pytest.raises(ValidationError, match=message):
        SOFRBoundaryInputLock.model_validate(values)


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
def test_sofr_lock_rejects_manifest_role_and_clock_corruption(case: str, message: str) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2019-09-17T19:59:59Z"
    elif case == "unsorted_records":
        values["records"].reverse()
    elif case == "duplicate_records":
        values["records"][1] = values["records"][0]
    elif case == "role_coverage":
        values["roles"]["september13_rate"] = "missing-record-id"
    elif case == "duplicate_roles":
        values["roles"]["september13_rate"] = values["roles"]["september16_rate"]
    elif case == "unsorted_hashes":
        values["source_response_sha256s"].reverse()
    elif case == "duplicate_hashes":
        values["source_response_sha256s"][1] = values["source_response_sha256s"][0]
    elif case == "hash_set_mismatch":
        values["source_response_sha256s"] = sorted(
            [values["source_response_sha256s"][0], "f" * 64]
        )
    elif case == "naive_decision_time":
        values["decision_time"] = "2019-09-17T20:00:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-13T06:35:00"
    with pytest.raises(ValidationError, match=message):
        SOFRBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_sofr_lock_creation_loading_hash_and_zero_width_fail_closed(tmp_path: Path) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = SOFRBoundaryInputLock.create(values)
    assert recreated == SOFRBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid SOFR boundary input lock"):
        load_sofr_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match="invalid SOFR boundary input lock"):
        load_sofr_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated New York Fed SOFR boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        SOFRBoundaryInputLock.model_validate(tampered)

    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    september16 = next(
        record
        for record in values["records"]
        if record["record_id"] == values["roles"]["september16_rate"]
    )
    september16["payload"]["value_basis_points"] = 220
    september16["payload"]["reported_value_percent"] = "2.20"
    zero_width = SOFRBoundaryInputLock.create(values)
    with pytest.raises(ValueError, match="must establish a nonzero range"):
        build_sofr_boundary_replay_spec(zero_width, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_sofr_replay_fails_closed_if_bypassed_record_rate_is_not_integer() -> None:
    lock = load_sofr_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_basis_points": True}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match="must be integer basis points"):
        build_sofr_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)
