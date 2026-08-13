from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName, ReplayStudio
from finreplay.scenarios import (
    EIACrudeStockBoundaryInputLock,
    OfficialEventLock,
    build_eia_crude_stock_boundary_replay_spec,
    load_eia_crude_stock_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/eia-wpsr-2020/input-lock.json")
EVENT_PATH = Path("scenarios/eia-wpsr-2020/event-lock.json")
PACK_PATH = Path("verification/replaypacks/eia-wpsr-2020")
CODE_COMMIT = "77f191df5a98fdecdc67b24bbf1ca97a403a6e48"


@pytest.mark.integration
def test_eia_stock_boundary_runs_four_engines_with_exact_reported_and_inferred_values() -> None:
    lock = load_eia_crude_stock_boundary_input_lock(LOCK_PATH)
    spec = build_eia_crude_stock_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}

    assert set(artifacts) == {
        EngineName.TIMEVAULT,
        EngineName.SHOCKCOMPILER,
        EngineName.TRIALCOURT,
        EngineName.REPLAYSTUDIO,
    }
    assert spec.distinct_input_records == 2
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)
    assert len(lock.release_pdf_sha256s) == 2
    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert shock.payload["known_stocks"] == {
        "april03_stock_thousand_barrels": 484_370,
        "april10_stock_thousand_barrels": 503_618,
        "stock_lower_thousand_barrels": 484_370,
        "stock_upper_thousand_barrels": 503_618,
        "stock_range_width_thousand_barrels": 19_248,
    }
    assert shock.payload["naive_baseline"] == {
        "next_reported_commercial_crude_stocks_thousand_barrels": 503_618,
        "definition": "persistence of the latest known WPSR commercial-crude stock",
    }
    assert shock.payload["bound_construction"]["probability_assigned"] is False
    assert shock.payload["bound_construction"]["future_event_used"] is False
    trial = artifacts[EngineName.TRIALCOURT]
    assert trial.payload["decision"]["disposition"] == TrialDisposition.REJECT.value
    assert trial.payload["manifest"]["rejected_decisions"] == 1
    assert ReplayStudio().compile(spec).pack_sha256 == ReplayStudio().verify(PACK_PATH).pack_sha256


@pytest.mark.integration
def test_april17_event_is_exact_later_disjoint_and_breaches_prior_range() -> None:
    lock = load_eia_crude_stock_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-04-22"
    assert record.payload["week_ending"] == "2020-04-17"
    assert record.payload["prior_value_thousand_barrels"] == 503_618
    assert record.payload["reported_difference_thousand_barrels"] == 15_022
    assert record.payload["value_thousand_barrels"] == 518_640
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert record.payload["value_thousand_barrels"] > 503_618
    assert record.payload["value_thousand_barrels"] - 503_618 == 15_022


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived WPSR"),
        ("wrong_publisher", "publisher mismatch"),
        ("wrong_coverage", "versioned release snapshots"),
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
        ("wrong_release_date", "release-date mismatch"),
        ("wrong_week", "week-ending mismatch"),
        ("wrong_prior_week", "prior-week mismatch"),
        ("wrong_metric", "metric mismatch"),
        ("wrong_unit", "unit mismatch"),
        ("wrong_table", "table mismatch"),
        ("arithmetic_flag", "arithmetic flag mismatch"),
        ("wrong_method", "availability method mismatch"),
        ("wrong_csv_modified", "CSV modification time mismatch"),
        ("wrong_pdf_modified", "PDF modification time mismatch"),
        ("wrong_pdf_url", "PDF URL mismatch"),
        ("short_pdf_hash", "PDF hash mismatch"),
        ("nonhex_pdf_hash", "PDF hash mismatch"),
        ("noninteger", "must be an integer"),
        ("boolean_value", "must be an integer"),
        ("out_of_range", "outside supported range"),
        ("bad_arithmetic", "do not reconcile"),
        ("reported_nonstring", "must be a string"),
        ("invalid_decimal", "must be decimal"),
        ("nonfinite_decimal", "does not match integer value"),
        ("decimal_mismatch", "does not match integer value"),
    ],
)
def test_eia_stock_lock_rejects_source_timing_and_value_inflation(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "eia.other"
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
        first["entity_id"] = "eia_series:other"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-04-09T04:00:01Z"
        first["interval"]["available_at"] = "2020-04-09T04:00:01Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-04-09T04:00:01Z"
    elif case == "future":
        values["decision_time"] = "2020-04-09T03:59:59Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-04-02T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-04-08T18:15:54Z"
    elif case == "wrong_source_url":
        first["source"]["url"] = first["source"]["url"].replace("2020_04_08", "2020_04_15")
    elif case == "wrong_release_date":
        first["payload"]["release_date"] = "2020-04-09"
    elif case == "wrong_week":
        first["payload"]["week_ending"] = "2020-04-02"
    elif case == "wrong_prior_week":
        first["payload"]["prior_week_ending"] = "2020-03-26"
    elif case == "wrong_metric":
        first["payload"]["metric"] = "total_crude_stocks"
    elif case == "wrong_unit":
        first["payload"]["unit"] = "Million Barrels"
    elif case == "wrong_table":
        first["payload"]["table"] = "WPSR Table 3"
    elif case == "arithmetic_flag":
        first["payload"]["arithmetic_verified"] = False
    elif case == "wrong_method":
        first["payload"]["availability_method"] = "release_time"
    elif case == "wrong_csv_modified":
        first["payload"]["csv_last_modified_at"] = "2020-04-08T12:12:04+00:00"
    elif case == "wrong_pdf_modified":
        first["payload"]["pdf_last_modified_at"] = "2020-04-08T18:15:54+00:00"
    elif case == "wrong_pdf_url":
        first["payload"]["release_pdf_url"] += "?download=1"
    elif case == "short_pdf_hash":
        first["payload"]["release_pdf_sha256"] = "a" * 63
    elif case == "nonhex_pdf_hash":
        first["payload"]["release_pdf_sha256"] = "g" * 64
    elif case == "noninteger":
        first["payload"]["value_thousand_barrels"] = 484_370.5
    elif case == "boolean_value":
        first["payload"]["value_thousand_barrels"] = True
    elif case == "out_of_range":
        first["payload"]["value_thousand_barrels"] = 10_000_001
    elif case == "bad_arithmetic":
        first["payload"]["reported_difference_thousand_barrels"] += 1
    elif case == "reported_nonstring":
        first["payload"]["reported_value_million_barrels"] = 484.370
    elif case == "invalid_decimal":
        first["payload"]["reported_value_million_barrels"] = "not-a-number"
    elif case == "nonfinite_decimal":
        first["payload"]["reported_value_million_barrels"] = "NaN"
    elif case == "decimal_mismatch":
        first["payload"]["reported_value_million_barrels"] = "484.371"
    with pytest.raises(ValidationError, match=message):
        EIACrudeStockBoundaryInputLock.model_validate(values)


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
        ("unsorted_pdf_hashes", "PDF hashes must be unique and sorted"),
        ("duplicate_pdf_hashes", "PDF hashes must be unique and sorted"),
        ("pdf_hash_set_mismatch", "PDF hashes do not match"),
        ("naive_decision_time", "decision_time must be timezone-aware"),
        ("naive_build_epoch", "build_epoch must be timezone-aware"),
    ],
)
def test_eia_stock_lock_rejects_manifest_role_and_clock_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if case == "early_build":
        values["build_epoch"] = "2020-04-16T11:59:59Z"
    elif case == "unsorted_records":
        values["records"].reverse()
    elif case == "duplicate_records":
        values["records"][1] = values["records"][0]
    elif case == "role_coverage":
        values["roles"]["april03_stock"] = "missing-record-id"
    elif case == "duplicate_roles":
        values["roles"]["april03_stock"] = values["roles"]["april10_stock"]
    elif case == "unsorted_hashes":
        values["source_response_sha256s"].reverse()
    elif case == "duplicate_hashes":
        values["source_response_sha256s"][1] = values["source_response_sha256s"][0]
    elif case == "hash_set_mismatch":
        values["source_response_sha256s"] = sorted(
            [values["source_response_sha256s"][0], "f" * 64]
        )
    elif case == "unsorted_pdf_hashes":
        values["release_pdf_sha256s"].reverse()
    elif case == "duplicate_pdf_hashes":
        values["release_pdf_sha256s"][1] = values["release_pdf_sha256s"][0]
    elif case == "pdf_hash_set_mismatch":
        values["release_pdf_sha256s"] = sorted(
            [values["release_pdf_sha256s"][0], "f" * 64]
        )
    elif case == "naive_decision_time":
        values["decision_time"] = "2020-04-16T12:00:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-13T06:50:00"
    with pytest.raises(ValidationError, match=message):
        EIACrudeStockBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_eia_stock_lock_creation_loading_hash_and_zero_width_fail_closed(
    tmp_path: Path,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = EIACrudeStockBoundaryInputLock.create(values)
    assert recreated == EIACrudeStockBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid EIA crude-stock input lock"):
        load_eia_crude_stock_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match="invalid EIA crude-stock input lock"):
        load_eia_crude_stock_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated EIA commercial-crude-stock boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        EIACrudeStockBoundaryInputLock.model_validate(tampered)

    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    april10 = next(
        record
        for record in values["records"]
        if record["record_id"] == values["roles"]["april10_stock"]
    )
    april10["payload"]["value_thousand_barrels"] = 484_370
    april10["payload"]["reported_difference_thousand_barrels"] = 0
    april10["payload"]["reported_value_million_barrels"] = "484.370"
    april10["payload"]["reported_difference_million_barrels"] = "0.000"
    zero_width = EIACrudeStockBoundaryInputLock.create(values)
    with pytest.raises(ValueError, match="must establish a nonzero range"):
        build_eia_crude_stock_boundary_replay_spec(zero_width, code_commit=CODE_COMMIT)


@pytest.mark.integration
def test_eia_stock_replay_fails_closed_if_bypassed_stock_is_not_integer() -> None:
    lock = load_eia_crude_stock_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_thousand_barrels": True}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match="must be integer thousand barrels"):
        build_eia_crude_stock_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)
