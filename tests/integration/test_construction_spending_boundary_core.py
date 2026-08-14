from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName
from finreplay.scenarios import (
    ConstructionSpendingBoundaryInputLock,
    OfficialEventLock,
    build_construction_spending_boundary_replay_spec,
    load_construction_spending_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/census-c30-2020/input-lock.json")
EVENT_PATH = Path("scenarios/census-c30-2020/event-lock.json")
CODE_COMMIT = "36fbc8a000000000000000000000000000000000"


@pytest.mark.integration
def test_construction_spending_boundary_runs_four_engines_with_exact_values() -> None:
    lock = load_construction_spending_boundary_input_lock(LOCK_PATH)
    spec = build_construction_spending_boundary_replay_spec(
        lock,
        code_commit=CODE_COMMIT,
    )
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
        "exact preliminary current-month Table 1 levels"
    )
    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert len(shock.source_hashes) == 4
    assert set(shock.source_hashes) == set(lock.source_response_sha256s)
    assert shock.payload["known_initial_release_levels"] == {
        "january_initial_level_million_dollars": 1_369_223,
        "february_initial_level_million_dollars": 1_366_697,
        "known_initial_decline_million_dollars": 2_526,
        "lower_level_million_dollars": 1_364_171,
        "upper_level_million_dollars": 1_366_697,
        "range_width_million_dollars": 2_526,
    }
    assert shock.payload["naive_baseline"] == {
        "next_total_construction_saar_level_million_dollars": 1_366_697,
        "definition": "persistence of the February preliminary C30 Table 1 level",
    }
    assert shock.payload["bound_construction"] == {
        "lower_level_million_dollars": 1_364_171,
        "upper_level_million_dollars": 1_366_697,
        "range_width_million_dollars": 2_526,
        "known_initial_decline_million_dollars": 2_526,
        "endpoint_method": (
            "latest_preliminary_level_persistence_or_repeat_known_initial_decline"
        ),
        "basis_is_initial_release_levels_not_official_monthly_change": True,
        "official_sampling_confidence_interval_used": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {
        "lower": 1_364_171.0,
        "upper": 1_366_697.0,
    }
    january = next(
        record
        for record in lock.records
        if record.record_id == lock.roles.january_headline_level
    )
    february = next(
        record
        for record in lock.records
        if record.record_id == lock.roles.february_headline_level
    )
    assert january.payload["value_million_dollars"] == 1_369_223
    assert january.payload["reported_current_month_change_percent"] == "1.8"
    assert february.payload["value_million_dollars"] == 1_366_697
    assert february.payload["reported_current_month_change_percent"] == "-1.3"
    assert february.payload[
        "reported_prior_month_revised_total_saar_million_dollars"
    ] == 1_384_486
    assert february.payload["release_snapshot_revision_delta_million_dollars"] == {
        "2020-01": 15_263,
        "2020-02": None,
    }
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
def test_march_event_is_disjoint_revised_and_breaches_fixed_lower_bound() -> None:
    lock = load_construction_spending_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-05-01"
    assert record.payload["reference_month"] == "2020-03"
    assert record.payload["release_number"] == "CB20-68"
    assert record.payload["value_million_dollars"] == 1_360_512
    assert record.payload["reported_current_month_change_percent"] == "0.9"
    assert record.payload["reported_current_month_margin_90_percent"] == "0.8"
    assert record.payload["release_snapshot_total_construction_saar_million_dollars"] == {
        "2020-01": 1_382_963,
        "2020-02": 1_348_386,
        "2020-03": 1_360_512,
    }
    assert record.payload["release_snapshot_revision_delta_million_dollars"] == {
        "2020-01": -1_523,
        "2020-02": -18_311,
        "2020-03": None,
    }
    assert record.payload["covid_publication_standard_statement_present"] is True
    assert record.payload["future_imputation_revision_notice_present"] is True
    assert record.payload["pdf_xlsx_crosscheck_verified"] is True
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert 1_364_171 - record.payload["value_million_dollars"] == 3_659
    february = next(
        item
        for item in lock.records
        if item.record_id == lock.roles.february_headline_level
    )
    assert february.payload["value_million_dollars"] == 1_366_697
    assert (
        record.payload["release_snapshot_total_construction_saar_million_dollars"][
            "2020-02"
        ]
        == 1_348_386
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived C30 facts"),
        ("wrong_publisher", "publisher mismatch"),
        ("wrong_license", "license boundary mismatch"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("wrong_schema", "payload schema mismatch"),
        ("low_confidence", "timing must be deterministic"),
        ("wrong_published", "publication time mismatch"),
        ("wrong_available", "availability time mismatch"),
        ("wrong_revised", "must be initial monthly releases"),
        ("wrong_valid", "valid time mismatch"),
        ("wrong_vintage", "source vintage mismatch"),
        ("wrong_source_url", "source URL mismatch"),
        ("wrong_source_version", "source version mismatch"),
        ("extra_payload", "payload field set mismatch"),
        ("release_date", "release_date mismatch"),
        ("release_reference_month", "release_reference_month mismatch"),
        ("reference_month", "reference_month mismatch"),
        ("release_number", "release_number mismatch"),
        ("release_series", "release_series mismatch"),
        ("metric", "metric mismatch"),
        ("value_million_dollars", "value_million_dollars mismatch"),
        ("estimate_status", "estimate_status mismatch"),
        (
            "reported_current_month_total_saar_million_dollars",
            "reported_current_month_total_saar_million_dollars mismatch",
        ),
        (
            "reported_prior_month_revised_total_saar_million_dollars",
            "reported_prior_month_revised_total_saar_million_dollars mismatch",
        ),
        (
            "reported_current_month_change_percent",
            "reported_current_month_change_percent mismatch",
        ),
        (
            "reported_current_month_change_basis_points",
            "reported_current_month_change_basis_points mismatch",
        ),
        (
            "reported_current_month_margin_90_percent",
            "reported_current_month_margin_90_percent mismatch",
        ),
        (
            "reported_year_over_year_change_percent",
            "reported_year_over_year_change_percent mismatch",
        ),
        ("reported_private_saar_million_dollars", "reported_private_saar"),
        ("reported_public_saar_million_dollars", "reported_public_saar"),
        (
            "table2_year_to_date_current_million_dollars",
            "table2_year_to_date_current_million_dollars mismatch",
        ),
        (
            "table3_total_monthly_estimate_cv_percent",
            "table3_total_monthly_estimate_cv_percent mismatch",
        ),
        (
            "table4_annual_total_current_million_dollars",
            "table4_annual_total_current_million_dollars mismatch",
        ),
        (
            "release_snapshot_total_construction_saar_million_dollars",
            "release_snapshot_total_construction_saar_million_dollars mismatch",
        ),
        ("release_time_local", "release_time_local mismatch"),
        ("release_timezone", "release_timezone mismatch"),
        ("release_timezone_abbreviation", "release_timezone_abbreviation mismatch"),
        ("official_release_at", "official_release_at mismatch"),
        (
            "data_adjusted_seasonally_but_not_for_price_changes",
            "data_adjusted_seasonally_but_not_for_price_changes mismatch",
        ),
        ("unit", "unit mismatch"),
        ("snapshot_semantics", "snapshot_semantics mismatch"),
        ("pdf_xlsx_crosscheck_verified", "pdf_xlsx_crosscheck_verified mismatch"),
        ("release_pdf_sha256", "paired hashes do not match"),
        ("release_xlsx_sha256", "paired hashes do not match"),
        ("release_xlsx_sheet_names", "release_xlsx_sheet_names mismatch"),
        ("availability_method", "availability_method mismatch"),
    ],
)
def test_construction_spending_lock_rejects_source_timing_and_value_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "census.other"
    elif case == "wrong_publisher":
        first["source"]["publisher"] = "Other publisher"
    elif case == "wrong_license":
        first["source"]["license_class"] = "redistributable"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "census_c30:other"
    elif case == "wrong_schema":
        first["payload_schema_version"] = "1.0.0"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-03-02T14:59:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-03-02T15:00:01Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = "2020-03-02T15:00:00Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-01-02T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-03-02T15:00:01Z"
    elif case == "wrong_source_url":
        first["source"]["url"] = "https://www.census.gov/construction/c30/pdf/other.pdf"
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "CENSUS-C30:fabricated"
    elif case == "extra_payload":
        first["payload"]["fabricated"] = True
    else:
        first["payload"][case] = _corrupt(first["payload"][case])
    with pytest.raises(ValidationError, match=message):
        ConstructionSpendingBoundaryInputLock.model_validate(values)


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
        ("unsorted_hashes", "source hashes must be unique and sorted"),
        ("duplicate_hashes", "source hashes must be unique and sorted"),
        ("wrong_hash_set", "source hash set does not match releases"),
        ("record_hash_mismatch", "PDF hashes do not match locked records"),
        ("paired_hash_mismatch", "paired hashes do not match locked records"),
        ("naive_decision_time", "decision_time must be timezone-aware"),
        ("naive_build_epoch", "build_epoch must be timezone-aware"),
    ],
)
def test_construction_spending_lock_rejects_manifest_role_and_clock_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2020-04-01T13:59:59Z"
    elif case == "wrong_decision":
        values["decision_time"] = "2020-04-01T14:00:01Z"
    elif case == "unsorted_records":
        values["records"].reverse()
    elif case == "duplicate_records":
        values["records"][1] = values["records"][0]
    elif case == "role_coverage":
        values["roles"]["january_headline_level"] = "missing-record-id"
    elif case == "duplicate_roles":
        values["roles"]["january_headline_level"] = values["roles"][
            "february_headline_level"
        ]
    elif case == "unsorted_hashes":
        values["source_response_sha256s"].reverse()
    elif case == "duplicate_hashes":
        values["source_response_sha256s"][1] = values["source_response_sha256s"][0]
    elif case == "wrong_hash_set":
        values["source_response_sha256s"][-1] = "f" * 64
        values["source_response_sha256s"].sort()
    elif case == "record_hash_mismatch":
        values["records"][0]["source"]["sha256"] = values["records"][1]["source"][
            "sha256"
        ]
    elif case == "paired_hash_mismatch":
        values["records"][0]["payload"]["release_xlsx_sha256"] = values["records"][
            1
        ]["payload"]["release_xlsx_sha256"]
    elif case == "naive_decision_time":
        values["decision_time"] = "2020-04-01T14:00:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-14T03:30:00"
    with pytest.raises(ValidationError, match=message):
        ConstructionSpendingBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_construction_spending_lock_creation_loading_hash_and_runtime_guards(
    tmp_path: Path,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = ConstructionSpendingBoundaryInputLock.create(values)
    assert recreated == ConstructionSpendingBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Census construction-spending input lock"):
        load_construction_spending_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match="invalid Census construction-spending input lock"):
        load_construction_spending_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated Census construction-spending boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        ConstructionSpendingBoundaryInputLock.model_validate(tampered)

    lock = load_construction_spending_boundary_input_lock(LOCK_PATH)
    first, second = lock.records
    same_value = second.model_copy(
        update={
            "payload": {
                **second.payload,
                "value_million_dollars": first.payload["value_million_dollars"],
            }
        }
    )
    bypassed = lock.model_copy(update={"records": (first, same_value)})
    with pytest.raises(ValueError, match="must establish a positive known decline"):
        build_construction_spending_boundary_replay_spec(
            bypassed,
            code_commit=CODE_COMMIT,
        )


@pytest.mark.integration
@pytest.mark.parametrize("invalid_value", [True, 1_369_223.5, 0, 10_000_001])
def test_construction_spending_replay_fails_closed_for_invalid_bypassed_level(
    invalid_value: object,
) -> None:
    lock = load_construction_spending_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_million_dollars": invalid_value}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match=r"must be integer|outside supported range"):
        build_construction_spending_boundary_replay_spec(
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
