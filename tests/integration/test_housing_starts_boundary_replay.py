from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName
from finreplay.scenarios import (
    HousingStartsBoundaryInputLock,
    OfficialEventLock,
    build_housing_starts_boundary_replay_spec,
    load_housing_starts_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/census-nrc-2020/input-lock.json")
EVENT_PATH = Path("scenarios/census-nrc-2020/event-lock.json")
CODE_COMMIT = "0" * 40


@pytest.mark.integration
def test_housing_starts_boundary_runs_four_engines_with_exact_values() -> None:
    lock = load_housing_starts_boundary_input_lock(LOCK_PATH)
    spec = build_housing_starts_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
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
    assert shock.payload["known_headline_levels"] == {
        "january_headline_units": 1_567_000,
        "february_headline_units": 1_599_000,
        "known_headline_increase_units": 32_000,
        "lower_level_units": 1_599_000,
        "upper_level_units": 1_631_000,
        "range_width_units": 32_000,
    }
    assert shock.payload["naive_baseline"] == {
        "next_total_housing_starts_saar_units": 1_599_000,
        "definition": "persistence of the latest preliminary NRC headline level",
    }
    assert shock.payload["bound_construction"] == {
        "lower_level_units": 1_599_000,
        "upper_level_units": 1_631_000,
        "range_width_units": 32_000,
        "known_headline_increase_units": 32_000,
        "endpoint_method": (
            "latest_headline_persistence_or_repeat_known_headline_increase"
        ),
        "basis_is_release_headline_levels_not_official_monthly_change": True,
        "official_sampling_confidence_interval_used": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {
        "lower": 1_599_000.0,
        "upper": 1_631_000.0,
    }
    january = next(
        record for record in lock.records if record.record_id == lock.roles.january_headline_level
    )
    february = next(
        record for record in lock.records if record.record_id == lock.roles.february_headline_level
    )
    assert january.payload["reported_monthly_change_percent"] == "-3.6"
    assert february.payload["reported_monthly_change_percent"] == "-1.5"
    assert february.payload["prior_month_revised_value_units"] == 1_624_000
    assert february.payload["prior_month_value_in_previous_release_units"] == 1_567_000
    assert february.payload["prior_month_revision_delta_units"] == 57_000
    trial = artifacts[EngineName.TRIALCOURT]
    assert trial.payload["decision"]["disposition"] == TrialDisposition.REJECT.value
    assert len(trial.payload["decision"]["findings"]) == 6
    assert trial.payload["manifest"]["rejected_decisions"] == 1


@pytest.mark.integration
def test_march_event_is_later_disjoint_revised_and_breaches_lower_bound() -> None:
    lock = load_housing_starts_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-04-16"
    assert record.payload["reference_month"] == "2020-03"
    assert record.payload["release_number"] == "CB20-61"
    assert record.payload["value_units"] == 1_216_000
    assert record.payload["value_thousand_units"] == 1_216
    assert record.payload["reported_monthly_change_percent"] == "-22.3"
    assert record.payload["reported_monthly_margin_90_percent"] == "12.2"
    assert record.payload["reported_monthly_change_significant_at_90_percent"] is True
    assert record.payload["prior_month_revised_value_units"] == 1_564_000
    assert record.payload["prior_month_value_in_previous_release_units"] == 1_599_000
    assert record.payload["prior_month_revision_delta_units"] == -35_000
    assert record.payload["covid_publication_standard_statement_present"] is True
    assert record.payload["pdf_table_snapshot_verified"] is True
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert 1_599_000 - record.payload["value_units"] == 383_000
    february = next(
        item for item in lock.records if item.record_id == lock.roles.february_headline_level
    )
    assert february.payload["value_units"] == 1_599_000
    assert february.payload["prior_month_revised_value_units"] == 1_624_000


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived NRC facts"),
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
        ("release_series", "release_series mismatch"),
        ("metric", "metric mismatch"),
        ("value_units", "value_units mismatch"),
        ("value_thousand_units", "value_thousand_units mismatch"),
        ("reported_monthly_change_percent", "reported_monthly_change_percent mismatch"),
        ("reported_monthly_margin_90_percent", "reported_monthly_margin_90_percent mismatch"),
        ("reported_monthly_ci_includes_zero", "reported_monthly_ci_includes_zero mismatch"),
        (
            "reported_monthly_change_significant_at_90_percent",
            "reported_monthly_change_significant_at_90_percent mismatch",
        ),
        (
            "reported_year_over_year_change_percent",
            "reported_year_over_year_change_percent mismatch",
        ),
        (
            "reported_year_over_year_margin_90_percent",
            "reported_year_over_year_margin_90_percent mismatch",
        ),
        ("prior_month", "prior_month mismatch"),
        ("prior_month_revised_value_units", "prior_month_revised_value_units mismatch"),
        (
            "prior_month_revised_value_thousand_units",
            "prior_month_revised_value_thousand_units mismatch",
        ),
        (
            "prior_month_value_in_previous_release_units",
            "prior_month_value_in_previous_release_units mismatch",
        ),
        ("prior_month_revision_delta_units", "prior_month_revision_delta_units mismatch"),
        ("single_family_starts_units", "single_family_starts_units mismatch"),
        (
            "single_family_monthly_change_percent",
            "single_family_monthly_change_percent mismatch",
        ),
        (
            "single_family_monthly_margin_90_percent",
            "single_family_monthly_margin_90_percent mismatch",
        ),
        ("five_units_or_more_starts_units", "five_units_or_more_starts_units mismatch"),
        ("table3_average_rse_percent", "table3_average_rse_percent mismatch"),
        (
            "reported_average_preliminary_revision_leq_percent",
            "reported_average_preliminary_revision_leq_percent mismatch",
        ),
        ("release_time_local", "release_time_local mismatch"),
        ("release_timezone", "release_timezone mismatch"),
        ("release_timezone_abbreviation", "release_timezone_abbreviation mismatch"),
        ("official_release_at", "official_release_at mismatch"),
        (
            "covid_publication_standard_statement_present",
            "covid_publication_standard_statement_present mismatch",
        ),
        ("unit", "unit mismatch"),
        ("snapshot_semantics", "snapshot_semantics mismatch"),
        ("pdf_table_snapshot_verified", "pdf_table_snapshot_verified mismatch"),
        ("release_pdf_url", "release_pdf_url mismatch"),
        ("release_pdf_sha256", "release_pdf_sha256 mismatch"),
        ("release_pdf_pages", "release_pdf_pages mismatch"),
        ("availability_method", "availability_method mismatch"),
    ],
)
def test_housing_starts_lock_rejects_source_timing_and_value_corruption(
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
        first["entity_id"] = "census_hud_nrc:other"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-02-19T13:29:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-02-19T13:30:01Z"
    elif case == "future":
        values["decision_time"] = "2020-02-19T13:29:59Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-01-02T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-02-19T13:30:01Z"
    elif case == "wrong_pdf_hash":
        first["source"]["sha256"] = "f" * 64
        first["payload"]["release_pdf_sha256"] = "f" * 64
        values["source_response_sha256s"] = sorted(
            ["f" * 64, values["records"][1]["source"]["sha256"]]
        )
    elif case == "wrong_source_url":
        first["source"]["url"] = (
            "https://www.census.gov/construction/nrc/pdf/other.pdf"
        )
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "CENSUS-HUD-NRC:fabricated"
    else:
        first["payload"][case] = _corrupt(first["payload"][case])
    with pytest.raises(ValidationError, match=message):
        HousingStartsBoundaryInputLock.model_validate(values)


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
def test_housing_starts_lock_rejects_manifest_role_and_clock_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2020-03-19T11:59:59Z"
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
    elif case == "hash_set_mismatch":
        values["source_response_sha256s"] = sorted(
            [values["source_response_sha256s"][0], "f" * 64]
        )
    elif case == "naive_decision_time":
        values["decision_time"] = "2020-03-19T12:00:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-13T14:00:00"
    with pytest.raises(ValidationError, match=message):
        HousingStartsBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_housing_starts_lock_creation_loading_hash_and_runtime_guards(
    tmp_path: Path,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = HousingStartsBoundaryInputLock.create(values)
    assert recreated == HousingStartsBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Census/HUD housing-starts input lock"):
        load_housing_starts_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match="invalid Census/HUD housing-starts input lock"):
        load_housing_starts_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated Census/HUD housing-starts boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        HousingStartsBoundaryInputLock.model_validate(tampered)

    lock = load_housing_starts_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    second = lock.records[1]
    same_value = second.model_copy(
        update={
            "payload": {
                **second.payload,
                "value_units": 1_567_000,
                "value_thousand_units": 1_567,
            }
        }
    )
    bypassed = lock.model_copy(update={"records": (first, same_value)})
    with pytest.raises(ValueError, match="must establish a positive increase"):
        build_housing_starts_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


@pytest.mark.integration
@pytest.mark.parametrize("invalid_value", [True, 1_567_000.5, 0, 100_000_001])
def test_housing_starts_replay_fails_closed_for_invalid_bypassed_level(
    invalid_value: object,
) -> None:
    lock = load_housing_starts_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_units": invalid_value}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match=r"must be integer|outside supported range"):
        build_housing_starts_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_housing_starts_replay_fails_closed_for_inconsistent_units() -> None:
    lock = load_housing_starts_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_thousand_units": 1_568}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match="do not reconcile"):
        build_housing_starts_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


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
