from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    HousePriceChangeBoundaryInputLock,
    OfficialEventLock,
    build_house_price_change_boundary_replay_spec,
    load_house_price_change_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/fhfa-hpi-2020/input-lock.json")
EVENT_PATH = Path("scenarios/fhfa-hpi-2020/event-lock.json")
PACK_PATH = Path("verification/replaypacks/fhfa-hpi-2020")
CODE_COMMIT = "c2891ea05c93f3de2a10dbfef3578ee44f583bc2"


@pytest.mark.integration
def test_house_price_change_boundary_runs_four_engines_with_exact_values() -> None:
    lock = load_house_price_change_boundary_input_lock(LOCK_PATH)
    spec = build_house_price_change_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
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
        "national purchase-only seasonally adjusted monthly HPI changes"
    )
    assert timevault.payload["schedule_evidence"] == {
        "semantic_sha256": ("02f589a1d47ef046e87be9391a74f1d6e65fe92cdd552b87ad4144722f67cfba"),
        "url": (
            "https://www.fhfa.gov/news/news-release/"
            "fhfa-announces-2020-release-dates-for-house-price-index"
        ),
        "release_time_rule": "09:00 America/New_York",
        "raw_html_byte_identity_claimed": False,
    }
    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert len(shock.source_hashes) == 3
    assert set(shock.source_hashes) == set(lock.source_evidence_sha256s)
    assert shock.payload["known_initial_release_changes"] == {
        "january_initial_change_basis_points": 30,
        "february_initial_change_basis_points": 70,
        "known_initial_increase_basis_points": 40,
        "lower_change_basis_points": 70,
        "upper_change_basis_points": 110,
        "range_width_basis_points": 40,
    }
    assert shock.payload["naive_baseline"] == {
        "next_us_purchase_only_hpi_monthly_change_basis_points": 70,
        "definition": "persistence of the February initial FHFA national change",
    }
    assert shock.payload["bound_construction"] == {
        "lower_change_basis_points": 70,
        "upper_change_basis_points": 110,
        "range_width_basis_points": 40,
        "known_initial_increase_basis_points": 40,
        "endpoint_method": ("latest_initial_change_persistence_or_repeat_known_initial_increase"),
        "official_confidence_interval_used": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {"lower": 70.0, "upper": 110.0}

    january = next(
        record for record in lock.records if record.record_id == lock.roles.january_initial_change
    )
    february = next(
        record for record in lock.records if record.record_id == lock.roles.february_initial_change
    )
    assert january.payload["value_basis_points"] == 30
    assert january.payload["value_percent"] == "0.3"
    assert january.payload["report_footer_release_time_label"] == "9AM EST"
    assert january.payload["report_footer_time_label_differs_from_schedule_wording"] is True
    assert february.payload["value_basis_points"] == 70
    assert february.payload["value_percent"] == "0.7"
    assert february.payload["release_snapshot_revision_delta_basis_points"] == {
        "2020-01": 20,
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
    compiled = ReplayStudio().compile(spec)
    verified = ReplayStudio().verify(PACK_PATH)
    assert compiled.pack_sha256 == verified.pack_sha256
    assert verified.replay_manifest.code_commit == CODE_COMMIT
    assert compiled.spec.code_commit == CODE_COMMIT


@pytest.mark.integration
def test_march_event_is_disjoint_revised_and_breaches_fixed_lower_bound() -> None:
    lock = load_house_price_change_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-05-26"
    assert record.payload["reference_month"] == "2020-03"
    assert record.payload["report_kind"] == "quarterly_with_monthly_tables"
    assert record.payload["value_basis_points"] == 10
    assert record.payload["value_percent"] == "0.1"
    assert record.payload["reported_year_over_year_change_basis_points"] == 590
    assert record.payload["release_snapshot_monthly_change_basis_points"] == {
        "2020-01": 50,
        "2020-02": 80,
        "2020-03": 10,
    }
    assert record.payload["release_snapshot_previous_estimate_basis_points"] == {
        "2020-01": 50,
        "2020-02": 70,
        "2020-03": None,
    }
    assert record.payload["release_snapshot_revision_delta_basis_points"] == {
        "2020-01": 0,
        "2020-02": 10,
        "2020-03": None,
    }
    assert record.payload["report_pdf_pages"] == 28
    assert record.payload["report_pdf_metadata_modified_after_release"] is True
    assert record.payload["report_pdf_metadata_modification_date"] == ("D:20200615174605-04'00'")
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert 70 - record.payload["value_basis_points"] == 60


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived FHFA HPI facts"),
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
def test_house_price_change_lock_rejects_source_and_timing_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "fhfa.other"
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
        first["entity_id"] = "fhfa_hpi:other"
    elif case == "wrong_schema":
        first["payload_schema_version"] = "1.1.0"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_rule":
        first["interval"]["availability_rule"] = "current headers"
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-03-25T12:59:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-03-25T13:00:01Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = "2020-03-25T13:00:00Z"
    elif case == "wrong_valid_to":
        first["interval"]["valid_to"] = "2020-02-01T00:00:00Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-01-02T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-03-25T13:00:01Z"
    elif case == "wrong_ingested":
        first["interval"]["ingested_at"] = "2026-08-14T04:34:02Z"
    elif case == "retrieved_before_release":
        first["source"]["retrieved_at"] = "2020-03-25T12:59:59Z"
        first["interval"]["ingested_at"] = "2020-03-25T12:59:59Z"
    elif case == "wrong_source_url":
        first["source"]["url"] = "https://www.fhfa.gov/document/d/hpi/other"
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "FHFA-HPI:fabricated"
    elif case == "extra_payload":
        first["payload"]["fabricated"] = True
    with pytest.raises(ValidationError, match=message):
        HousePriceChangeBoundaryInputLock.model_validate(values)


@pytest.mark.integration
@pytest.mark.parametrize(
    "field",
    [
        "release_date",
        "reference_month",
        "release_series",
        "report_kind",
        "metric",
        "value_basis_points",
        "value_percent",
        "reported_year_over_year_change_basis_points",
        "reported_year_over_year_change_percent",
        "reported_monthly_change_by_geography_basis_points",
        "reported_current_index_by_geography",
        "release_snapshot_monthly_change_basis_points",
        "release_snapshot_previous_estimate_basis_points",
        "release_snapshot_revision_delta_basis_points",
        "release_time_local",
        "release_timezone",
        "release_timezone_abbreviation",
        "official_release_at",
        "official_schedule_url",
        "official_schedule_published_date",
        "official_schedule_conservative_knowledge_at",
        "report_footer_release_time_label",
        "report_footer_time_label_differs_from_schedule_wording",
        "purchase_only_index",
        "seasonally_adjusted",
        "index_base",
        "report_table_snapshot_verified",
        "report_revision_rows_verified",
        "covid_timing_statement_present",
        "report_pdf_url",
        "report_pdf_pages",
        "report_pdf_page_width_points",
        "report_pdf_page_height_points",
        "report_pdf_page_rotations",
        "report_pdf_metadata_creation_date",
        "report_pdf_metadata_modification_date",
        "report_pdf_metadata_modified_after_release",
        "availability_method",
        "unit",
        "snapshot_semantics",
    ],
)
def test_house_price_change_lock_rejects_payload_corruption(field: str) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    payload = values["records"][0]["payload"]
    payload[field] = _corrupt(payload[field])
    with pytest.raises(ValidationError, match=field):
        HousePriceChangeBoundaryInputLock.model_validate(values)


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
        ("schedule_hash_mismatch", "schedule semantic hash does not match"),
        ("naive_decision_time", "decision_time must be timezone-aware"),
        ("naive_build_epoch", "build_epoch must be timezone-aware"),
    ],
)
def test_house_price_change_lock_rejects_manifest_role_and_clock_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2020-04-22T12:59:59Z"
    elif case == "wrong_decision":
        values["decision_time"] = "2020-04-22T13:00:01Z"
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
    elif case == "schedule_hash_mismatch":
        values["records"][0]["payload"]["official_schedule_semantic_sha256"] = "f" * 64
    elif case == "naive_decision_time":
        values["decision_time"] = "2020-04-22T13:00:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-14T05:15:00"
    with pytest.raises(ValidationError, match=message):
        HousePriceChangeBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_house_price_change_lock_creation_loading_hash_and_runtime_guards(
    tmp_path: Path,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = HousePriceChangeBoundaryInputLock.create(values)
    assert recreated == HousePriceChangeBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid FHFA HPI input lock"):
        load_house_price_change_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match="invalid FHFA HPI input lock"):
        load_house_price_change_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated FHFA HPI boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        HousePriceChangeBoundaryInputLock.model_validate(tampered)

    lock = load_house_price_change_boundary_input_lock(LOCK_PATH)
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
        build_house_price_change_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


@pytest.mark.integration
@pytest.mark.parametrize("invalid_value", [True, 30.5, -5_001, 5_001])
def test_house_price_change_replay_fails_closed_for_invalid_bypassed_change(
    invalid_value: object,
) -> None:
    lock = load_house_price_change_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_basis_points": invalid_value}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match=r"must be integer|outside the supported range"):
        build_house_price_change_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


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
