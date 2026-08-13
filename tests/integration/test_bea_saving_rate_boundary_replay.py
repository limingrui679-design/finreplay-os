from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    BEASavingRateBoundaryInputLock,
    OfficialEventLock,
    build_bea_saving_rate_boundary_replay_spec,
    load_bea_saving_rate_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/bea-pio-2020/input-lock.json")
EVENT_PATH = Path("scenarios/bea-pio-2020/event-lock.json")
PACK_PATH = Path("verification/replaypacks/bea-pio-2020")
CODE_COMMIT = "1fade495626f4f14e667fa6b2ab7ddd49e051bc4"


@pytest.mark.integration
def test_bea_saving_rate_boundary_runs_four_engines_with_exact_values() -> None:
    lock = load_bea_saving_rate_boundary_input_lock(LOCK_PATH)
    spec = build_bea_saving_rate_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
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
        "january_saving_rate_basis_points": 790,
        "february_saving_rate_basis_points": 820,
        "known_monthly_increase_basis_points": 30,
        "saving_rate_lower_basis_points": 820,
        "saving_rate_upper_basis_points": 850,
        "saving_rate_range_width_basis_points": 30,
    }
    assert shock.payload["naive_baseline"] == {
        "next_personal_saving_rate_basis_points": 820,
        "definition": "persistence of the latest known BEA saving-rate snapshot",
    }
    assert shock.payload["bound_construction"] == {
        "lower_rate_basis_points": 820,
        "upper_rate_basis_points": 850,
        "range_width_basis_points": 30,
        "known_monthly_increase_basis_points": 30,
        "endpoint_method": "latest_persistence_or_repeat_known_monthly_increase",
        "probability_assigned": False,
        "future_event_used": False,
    }
    trial = artifacts[EngineName.TRIALCOURT]
    assert trial.payload["decision"]["disposition"] == TrialDisposition.REJECT.value
    assert len(trial.payload["decision"]["findings"]) == 6
    assert trial.payload["manifest"]["rejected_decisions"] == 1
    assert ReplayStudio().compile(spec).pack_sha256 == ReplayStudio().verify(PACK_PATH).pack_sha256


@pytest.mark.integration
def test_march_event_is_later_disjoint_revised_and_breaches_upper_bound() -> None:
    lock = load_bea_saving_rate_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-04-30"
    assert record.payload["reference_month"] == "2020-03"
    assert record.payload["release_number"] == "BEA 20-20"
    assert record.payload["value_basis_points"] == 1_310
    assert record.payload["reported_saving_rate_percent"] == "13.1"
    assert record.payload["personal_saving_trillion_dollars"] == "2.17"
    assert record.payload["prior_month_rate_in_current_release_basis_points"] == 800
    assert record.payload["prior_month_rate_in_previous_release_basis_points"] == 820
    assert record.payload["prior_month_revision_delta_basis_points"] == -20
    assert record.payload["html_pdf_crosscheck_verified"] is True
    assert record.payload["table1_snapshot_verified"] is True
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert record.payload["value_basis_points"] - 850 == 460
    february = next(
        item for item in lock.records if item.record_id == lock.roles.february_saving_rate
    )
    assert february.payload["value_basis_points"] == 820
    assert february.payload["prior_month_revision_delta_basis_points"] == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived PIO release facts"),
        ("wrong_publisher", "publisher mismatch"),
        ("wrong_license", "license boundary mismatch"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("low_confidence", "timing must be deterministic"),
        ("wrong_published", "publication time mismatch"),
        ("wrong_available", "availability time mismatch"),
        ("future", "post-decision input"),
        ("wrong_valid", "valid time mismatch"),
        ("wrong_vintage", "source vintage mismatch"),
        ("wrong_pdf_hash", "PDF hash mismatch"),
        ("wrong_source_url", "source URL mismatch"),
        ("wrong_source_version", "source version mismatch"),
        ("release_date", "release_date mismatch"),
        ("reference_month", "reference_month mismatch"),
        ("release_number", "release_number mismatch"),
        ("metric", "metric mismatch"),
        ("value_basis_points", "value_basis_points mismatch"),
        ("reported_saving_rate_percent", "reported_saving_rate_percent mismatch"),
        ("personal_saving_trillion_dollars", "personal_saving_trillion_dollars mismatch"),
        (
            "prior_month_rate_in_current_release_basis_points",
            "prior_month_rate_in_current_release_basis_points mismatch",
        ),
        (
            "prior_month_rate_in_previous_release_basis_points",
            "prior_month_rate_in_previous_release_basis_points mismatch",
        ),
        (
            "prior_month_revision_delta_basis_points",
            "prior_month_revision_delta_basis_points mismatch",
        ),
        (
            "personal_income_monthly_change_percent",
            "personal_income_monthly_change_percent mismatch",
        ),
        (
            "disposable_income_monthly_change_percent",
            "disposable_income_monthly_change_percent mismatch",
        ),
        ("pce_monthly_change_percent", "pce_monthly_change_percent mismatch"),
        ("real_pce_monthly_change_percent", "real_pce_monthly_change_percent mismatch"),
        ("release_time_local", "release_time_local mismatch"),
        ("release_timezone_abbreviation", "release_timezone_abbreviation mismatch"),
        ("release_timezone", "release_timezone mismatch"),
        ("official_release_at", "official_release_at mismatch"),
        ("unit", "unit mismatch"),
        ("snapshot_semantics", "snapshot_semantics mismatch"),
        ("html_pdf_crosscheck_verified", "html_pdf_crosscheck_verified mismatch"),
        ("table1_snapshot_verified", "table1_snapshot_verified mismatch"),
        ("release_html_url", "release_html_url mismatch"),
        ("release_html_sha256", "release_html_sha256 mismatch"),
        ("release_pdf_url", "release_pdf_url mismatch"),
        ("release_pdf_sha256", "release_pdf_sha256 mismatch"),
        ("release_pdf_pages", "release_pdf_pages mismatch"),
        ("availability_method", "availability_method mismatch"),
    ],
)
def test_bea_saving_rate_lock_rejects_source_timing_and_value_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "bea.other"
    elif case == "wrong_publisher":
        first["source"]["publisher"] = "Other publisher"
    elif case == "wrong_license":
        first["source"]["license_class"] = "redistributable"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "bea_pio:other"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-02-28T13:29:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-02-28T13:30:01Z"
    elif case == "future":
        values["decision_time"] = "2020-02-28T13:29:59Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-01-02T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-02-28T13:30:01Z"
    elif case == "wrong_pdf_hash":
        first["source"]["sha256"] = "f" * 64
        first["payload"]["release_pdf_sha256"] = "f" * 64
        values["source_response_sha256s"] = sorted(
            ["f" * 64, values["records"][1]["source"]["sha256"]]
        )
    elif case == "wrong_source_url":
        first["source"]["url"] = "https://www.bea.gov/sites/default/files/other.pdf"
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "BEA-PIO:fabricated"
    else:
        first["payload"][case] = _corrupt(first["payload"][case])
    with pytest.raises(ValidationError, match=message):
        BEASavingRateBoundaryInputLock.model_validate(values)


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
def test_bea_saving_rate_lock_rejects_manifest_role_and_clock_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2020-04-01T11:59:59Z"
    elif case == "unsorted_records":
        values["records"].reverse()
    elif case == "duplicate_records":
        values["records"][1] = values["records"][0]
    elif case == "role_coverage":
        values["roles"]["january_saving_rate"] = "missing-record-id"
    elif case == "duplicate_roles":
        values["roles"]["january_saving_rate"] = values["roles"]["february_saving_rate"]
    elif case == "unsorted_hashes":
        values["source_response_sha256s"].reverse()
    elif case == "duplicate_hashes":
        values["source_response_sha256s"][1] = values["source_response_sha256s"][0]
    elif case == "hash_set_mismatch":
        values["source_response_sha256s"] = sorted([values["source_response_sha256s"][0], "f" * 64])
    elif case == "naive_decision_time":
        values["decision_time"] = "2020-04-01T12:00:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-13T08:35:00"
    with pytest.raises(ValidationError, match=message):
        BEASavingRateBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_bea_saving_rate_lock_creation_loading_hash_and_runtime_guards(
    tmp_path: Path,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = BEASavingRateBoundaryInputLock.create(values)
    assert recreated == BEASavingRateBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid BEA saving-rate input lock"):
        load_bea_saving_rate_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match="invalid BEA saving-rate input lock"):
        load_bea_saving_rate_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated BEA saving-rate boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        BEASavingRateBoundaryInputLock.model_validate(tampered)

    lock = load_bea_saving_rate_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    second = lock.records[1]
    same_value = second.model_copy(
        update={"payload": {**second.payload, "value_basis_points": 790}}
    )
    bypassed = lock.model_copy(update={"records": (first, same_value)})
    with pytest.raises(ValueError, match="must establish a positive saving-rate increase"):
        build_bea_saving_rate_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


@pytest.mark.integration
@pytest.mark.parametrize("invalid_value", [True, 790.5, -1, 100_001])
def test_bea_saving_rate_replay_fails_closed_for_invalid_bypassed_rate(
    invalid_value: object,
) -> None:
    lock = load_bea_saving_rate_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_basis_points": invalid_value}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match=r"rate must be integer|outside supported range"):
        build_bea_saving_rate_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


def _corrupt(value: Any) -> Any:
    if value is None:
        return 1
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return f"{value}-corrupt"
    raise AssertionError(f"unsupported test value: {value!r}")
