from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    OfficialEventLock,
    RetailSalesBoundaryInputLock,
    build_retail_sales_boundary_replay_spec,
    load_retail_sales_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/census-marts-2020/input-lock.json")
EVENT_PATH = Path("scenarios/census-marts-2020/event-lock.json")
PACK_PATH = Path("verification/replaypacks/census-marts-2020")
CODE_COMMIT = "922aef8d057ee3b9f51cb016f077f0c926af3fc3"


@pytest.mark.integration
def test_retail_sales_boundary_runs_four_engines_with_exact_values() -> None:
    lock = load_retail_sales_boundary_input_lock(LOCK_PATH)
    spec = build_retail_sales_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
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
    assert shock.payload["known_changes"] == {
        "january_monthly_change_basis_points": 30,
        "february_monthly_change_basis_points": -50,
        "known_decrease_basis_points": 80,
        "lower_change_basis_points": -130,
        "upper_change_basis_points": -50,
        "range_width_basis_points": 80,
    }
    assert shock.payload["naive_baseline"] == {
        "next_monthly_change_basis_points": -50,
        "definition": "persistence of the latest known MARTS headline monthly change",
    }
    assert shock.payload["bound_construction"] == {
        "lower_change_basis_points": -130,
        "upper_change_basis_points": -50,
        "range_width_basis_points": 80,
        "known_decrease_basis_points": 80,
        "endpoint_method": "repeat_known_monthly_decrease_or_latest_persistence",
        "probability_assigned": False,
        "future_event_used": False,
    }
    official_sampling_margins = {
        record.payload["reported_monthly_margin_90_percent"] for record in lock.records
    }
    assert official_sampling_margins == {"0.4"}
    assert "margin" not in shock.payload["bound_construction"]
    assert "confidence" not in shock.payload["bound_construction"]
    trial = artifacts[EngineName.TRIALCOURT]
    assert trial.payload["decision"]["disposition"] == TrialDisposition.REJECT.value
    assert len(trial.payload["decision"]["findings"]) == 6
    assert trial.payload["manifest"]["rejected_decisions"] == 1
    assert ReplayStudio().compile(spec).pack_sha256 == ReplayStudio().verify(PACK_PATH).pack_sha256


@pytest.mark.integration
def test_march_event_is_later_disjoint_revised_and_breaches_lower_bound() -> None:
    lock = load_retail_sales_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-04-15"
    assert record.payload["reference_month"] == "2020-03"
    assert record.payload["release_number"] == "CB20-56"
    assert record.payload["release_series"] == "Advance Monthly Retail Trade Survey"
    assert record.payload["value_basis_points"] == -870
    assert record.payload["reported_monthly_change_percent"] == "-8.7"
    assert record.payload["reported_sales_billion_dollars"] == "483.1"
    assert record.payload["xls_adjusted_sales_million_dollars"] == 483066
    assert record.payload["year_over_year_change_percent"] == "-6.2"
    assert record.payload["prior_month_change_in_current_release_basis_points"] == -40
    assert record.payload["prior_month_change_in_previous_release_basis_points"] == -50
    assert record.payload["prior_month_revision_delta_basis_points"] == 10
    assert record.payload["covid_publication_standard_statement_present"] is True
    assert record.payload["pdf_xls_crosscheck_verified"] is True
    assert record.payload["xls_table_snapshot_verified"] is True
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert -130 - record.payload["value_basis_points"] == 740
    february = next(
        item for item in lock.records if item.record_id == lock.roles.february_monthly_change
    )
    assert february.payload["value_basis_points"] == -50
    assert february.payload["prior_month_revision_delta_basis_points"] == 30


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived retail-sales facts"),
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
    ],
)
def test_retail_sales_lock_rejects_source_and_timing_corruption(
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
        first["entity_id"] = "census_marts:other"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-02-14T13:29:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-02-14T13:30:01Z"
    elif case == "future":
        values["decision_time"] = "2020-02-14T13:29:59Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-01-02T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-02-14T13:30:01Z"
    elif case == "wrong_pdf_hash":
        first["source"]["sha256"] = "f" * 64
        first["payload"]["release_pdf_sha256"] = "f" * 64
        values["source_response_sha256s"] = sorted(
            ["f" * 64, values["records"][1]["source"]["sha256"]]
        )
    elif case == "wrong_source_url":
        first["source"]["url"] = (
            "https://www2.census.gov/retail/releases/historical/marts/adv2003.pdf"
        )
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "CENSUS-MARTS:fabricated"
    with pytest.raises(ValidationError, match=message):
        RetailSalesBoundaryInputLock.model_validate(values)


@pytest.mark.integration
@pytest.mark.parametrize(
    "field",
    [
        "release_date",
        "reference_month",
        "release_number",
        "release_series",
        "metric",
        "value_basis_points",
        "reported_monthly_change_percent",
        "reported_monthly_margin_90_percent",
        "reported_sales_billion_dollars",
        "xls_adjusted_sales_million_dollars",
        "year_over_year_change_percent",
        "year_over_year_margin_90_percent",
        "prior_month_change_in_current_release_basis_points",
        "prior_month_change_in_previous_release_basis_points",
        "prior_month_revision_delta_basis_points",
        "prior_month_margin_90_percent",
        "prior_month_previous_margin_90_percent",
        "xls_adjusted_prior_month_sales_million_dollars",
        "table3_monthly_change_median_standard_error_percent",
        "table3_average_revision_percent",
        "table3_median_absolute_revision_percent",
        "release_time_local",
        "release_timezone_abbreviation",
        "release_timezone",
        "official_release_at",
        "scheduled_annual_revision_at",
        "covid_publication_standard_statement_present",
        "unit",
        "snapshot_semantics",
        "pdf_xls_crosscheck_verified",
        "pdf_table_snapshot_verified",
        "xls_table_snapshot_verified",
        "release_pdf_url",
        "release_pdf_sha256",
        "release_pdf_pages",
        "release_xls_url",
        "release_xls_sha256",
        "release_xls_sheet_names",
        "availability_method",
    ],
)
def test_retail_sales_lock_rejects_payload_corruption(field: str) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    first["payload"][field] = _corrupt(first["payload"][field])
    with pytest.raises(ValidationError, match=rf"{field} mismatch"):
        RetailSalesBoundaryInputLock.model_validate(values)


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
def test_retail_sales_lock_rejects_manifest_role_and_clock_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2020-03-18T11:59:59Z"
    elif case == "unsorted_records":
        values["records"].reverse()
    elif case == "duplicate_records":
        values["records"][1] = values["records"][0]
    elif case == "role_coverage":
        values["roles"]["january_monthly_change"] = "missing-record-id"
    elif case == "duplicate_roles":
        values["roles"]["january_monthly_change"] = values["roles"][
            "february_monthly_change"
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
        values["decision_time"] = "2020-03-18T12:00:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-13T12:15:00"
    with pytest.raises(ValidationError, match=message):
        RetailSalesBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_retail_sales_lock_creation_loading_hash_and_runtime_guards(tmp_path: Path) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = RetailSalesBoundaryInputLock.create(values)
    assert recreated == RetailSalesBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid MARTS retail-sales input lock"):
        load_retail_sales_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match="invalid MARTS retail-sales input lock"):
        load_retail_sales_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated Census MARTS retail-sales boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        RetailSalesBoundaryInputLock.model_validate(tampered)

    lock = load_retail_sales_boundary_input_lock(LOCK_PATH)
    first, second = lock.records
    same_value = second.model_copy(
        update={"payload": {**second.payload, "value_basis_points": 30}}
    )
    bypassed = lock.model_copy(update={"records": (first, same_value)})
    with pytest.raises(ValueError, match="must establish a negative monthly-change step"):
        build_retail_sales_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


@pytest.mark.integration
@pytest.mark.parametrize("invalid_value", [True, -50.5, -100_001, 100_001])
def test_retail_sales_replay_fails_closed_for_invalid_bypassed_change(
    invalid_value: object,
) -> None:
    lock = load_retail_sales_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_basis_points": invalid_value}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match=r"change must be integer|outside supported range"):
        build_retail_sales_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


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
        return [*value, "Corrupt sheet"]
    raise AssertionError(f"unsupported test value: {value!r}")
