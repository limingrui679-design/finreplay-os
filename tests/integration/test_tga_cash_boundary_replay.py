from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    TGACashBoundaryInputLock,
    build_tga_cash_boundary_replay_spec,
    load_tga_cash_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/treasury-tga-2023/input-lock.json")
EVENT_PATH = Path("scenarios/treasury-tga-2023/event-lock.json")
PACK_PATH = Path("verification/replaypacks/treasury-tga-2023")
CODE_COMMIT = "bb015ea9b753f4348baa11ec15bdba64a452cac7"


@pytest.mark.integration
def test_tga_boundary_runs_four_engines_with_exact_reported_and_inferred_values() -> None:
    lock = load_tga_cash_boundary_input_lock(LOCK_PATH)
    spec = build_tga_cash_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
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
    assert shock.payload["known_balances"] == {
        "may31_closing_balance_millions": 48_512,
        "june01_closing_balance_millions": 22_892,
        "balance_lower_millions": 22_892,
        "balance_upper_millions": 48_512,
        "balance_range_width_millions": 25_620,
    }
    assert shock.payload["naive_baseline"] == {
        "next_reported_tga_closing_balance_millions": 22_892,
        "definition": "persistence of the latest known reported TGA closing balance",
    }
    assert shock.payload["bound_construction"]["probability_assigned"] is False
    assert shock.payload["bound_construction"]["future_event_used"] is False
    trial = artifacts[EngineName.TRIALCOURT]
    assert trial.payload["decision"]["disposition"] == TrialDisposition.REJECT.value
    assert trial.payload["manifest"]["rejected_decisions"] == 1
    assert ReplayStudio().compile(spec).pack_sha256 == ReplayStudio().verify(PACK_PATH).pack_sha256


@pytest.mark.integration
def test_june02_event_is_exact_later_disjoint_and_inside_prior_range() -> None:
    lock = load_tga_cash_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["report_date"] == "2023-06-02"
    assert record.payload["publication_business_date"] == "2023-06-05"
    assert record.payload["value_millions"] == 23_368
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert 22_892 <= record.payload["value_millions"] <= 48_512
    assert record.payload["value_millions"] - 22_892 == 476


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived Treasury DTS"),
        ("latest_only", "versioned report snapshots"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("low_confidence", "timing must be deterministic"),
        ("wrong_published", "publication time mismatch"),
        ("wrong_available", "availability time mismatch"),
        ("future", "post-decision input"),
        ("wrong_valid", "valid time mismatch"),
        ("wrong_vintage", "source vintage mismatch"),
        ("wrong_report_date", "report-date mismatch"),
        ("wrong_publication_date", "publication-date mismatch"),
        ("wrong_metric", "metric mismatch"),
        ("wrong_unit", "unit mismatch"),
        ("wrong_table", "table mismatch"),
        ("wrong_method", "availability method mismatch"),
        ("arithmetic_flag", "arithmetic flag mismatch"),
        ("noninteger", "must be an integer"),
        ("out_of_range", "outside supported range"),
        ("zero_closing", "closing balance must be positive"),
        ("bad_arithmetic", "balances do not reconcile"),
    ],
)
def test_tga_lock_rejects_temporal_source_and_balance_inflation(case: str, message: str) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "treasury.other"
    elif case == "latest_only":
        first["source"]["temporal_coverage"] = "latest_only"
        first["source"]["vintage_as_of"] = None
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "us_treasury:other"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2023-06-01T20:00:01Z"
        first["interval"]["available_at"] = "2023-06-01T20:00:01Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2023-06-01T20:00:01Z"
    elif case == "future":
        values["decision_time"] = "2023-05-31T00:00:00Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2023-05-30T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2023-05-30T00:00:00Z"
    elif case == "wrong_report_date":
        first["payload"]["report_date"] = "2023-05-30"
    elif case == "wrong_publication_date":
        first["payload"]["publication_business_date"] = "2023-06-02"
    elif case == "wrong_metric":
        first["payload"]["metric"] = "cash"
    elif case == "wrong_unit":
        first["payload"]["unit"] = "Dollars"
    elif case == "wrong_table":
        first["payload"]["table"] = "DTS Table II"
    elif case == "wrong_method":
        first["payload"]["availability_method"] = "date_only"
    elif case == "arithmetic_flag":
        first["payload"]["arithmetic_verified"] = False
    elif case == "noninteger":
        first["payload"]["value_millions"] = 48_512.5
    elif case == "out_of_range":
        first["payload"]["value_millions"] = 10_000_001
    elif case == "zero_closing":
        first["payload"]["value_millions"] = 0
        first["payload"]["withdrawals_millions"] = (
            first["payload"]["opening_balance_millions"]
            + first["payload"]["deposits_millions"]
        )
    elif case == "bad_arithmetic":
        first["payload"]["withdrawals_millions"] += 1
    with pytest.raises(ValidationError, match=message):
        TGACashBoundaryInputLock.model_validate(values)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("early_build", "build_epoch cannot precede"),
        ("unsorted_records", "records must be unique and sorted"),
        ("role_coverage", "roles must cover"),
        ("duplicate_roles", "role record IDs must be unique"),
        ("unsorted_hashes", "source hashes must be unique and sorted"),
        ("hash_set_mismatch", "source hashes do not match"),
        ("naive_decision_time", "decision_time must be timezone-aware"),
    ],
)
def test_tga_lock_rejects_manifest_role_and_clock_corruption(case: str, message: str) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2023-06-02T20:59:59Z"
    elif case == "unsorted_records":
        values["records"].reverse()
    elif case == "role_coverage":
        values["roles"]["may31_closing"] = "missing-record-id"
    elif case == "duplicate_roles":
        values["roles"]["may31_closing"] = values["roles"]["june01_closing"]
    elif case == "unsorted_hashes":
        values["source_response_sha256s"].reverse()
    elif case == "hash_set_mismatch":
        values["source_response_sha256s"] = sorted(
            [values["source_response_sha256s"][0], "a" * 64]
        )
    elif case == "naive_decision_time":
        values["decision_time"] = "2023-06-02T21:00:00"
    with pytest.raises(ValidationError, match=message):
        TGACashBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_tga_lock_creation_loading_hash_and_zero_width_fail_closed(tmp_path: Path) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = TGACashBoundaryInputLock.create(values)
    assert recreated == TGACashBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid TGA cash boundary input lock"):
        load_tga_cash_boundary_input_lock(invalid)

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated Treasury cash boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        TGACashBoundaryInputLock.model_validate(tampered)

    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    june01_id = values["roles"]["june01_closing"]
    june01 = next(record for record in values["records"] if record["record_id"] == june01_id)
    may31 = next(
        record
        for record in values["records"]
        if record["record_id"] == values["roles"]["may31_closing"]
    )
    june01["payload"]["value_millions"] = may31["payload"]["value_millions"]
    june01["payload"]["withdrawals_millions"] = (
        june01["payload"]["opening_balance_millions"]
        + june01["payload"]["deposits_millions"]
        - june01["payload"]["value_millions"]
    )
    zero_width = TGACashBoundaryInputLock.create(values)
    with pytest.raises(ValueError, match="must establish a nonzero range"):
        build_tga_cash_boundary_replay_spec(zero_width, code_commit=CODE_COMMIT)
