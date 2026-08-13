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
    TreasuryAuctionBoundaryInputLock,
    build_treasury_auction_boundary_replay_spec,
    load_treasury_auction_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/treasury-auction-2020/input-lock.json")
EVENT_PATH = Path("scenarios/treasury-auction-2020/event-lock.json")
PACK_PATH = Path("verification/replaypacks/treasury-auction-2020")
CODE_COMMIT = "3e760275660c04d10bb3b2a888bab4d87800ba18"


@pytest.mark.integration
def test_treasury_auction_boundary_runs_four_engines_with_exact_values() -> None:
    lock = load_treasury_auction_boundary_input_lock(LOCK_PATH)
    spec = build_treasury_auction_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
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
        "march09_high_rate_basis_points": 39,
        "march16_high_rate_basis_points": 29,
        "known_weekly_decline_basis_points": 10,
        "rate_lower_basis_points": 19,
        "rate_upper_basis_points": 29,
        "rate_range_width_basis_points": 10,
    }
    assert shock.payload["naive_baseline"] == {
        "next_91_day_bill_high_rate_basis_points": 29,
        "definition": "persistence of the latest known 91-day bill high rate",
    }
    assert shock.payload["bound_construction"] == {
        "lower_rate_basis_points": 19,
        "upper_rate_basis_points": 29,
        "range_width_basis_points": 10,
        "known_weekly_decline_basis_points": 10,
        "zero_floor_applied": True,
        "endpoint_method": ("latest_persistence_or_repeat_known_weekly_decline_with_zero_floor"),
        "probability_assigned": False,
        "future_event_used": False,
    }
    trial = artifacts[EngineName.TRIALCOURT]
    assert trial.payload["decision"]["disposition"] == TrialDisposition.REJECT.value
    assert len(trial.payload["decision"]["findings"]) == 6
    assert trial.payload["manifest"]["rejected_decisions"] == 1
    assert ReplayStudio().compile(spec).pack_sha256 == ReplayStudio().verify(PACK_PATH).pack_sha256


@pytest.mark.integration
def test_march23_event_is_exact_later_disjoint_and_breaches_lower_bound() -> None:
    lock = load_treasury_auction_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["auction_date"] == "2020-03-23"
    assert record.payload["cusip"] == "912796UA5"
    assert record.payload["value_basis_points"] == 0
    assert record.payload["reported_high_rate_percent"] == "0.000"
    assert record.payload["reported_price_per_100"] == "100.000000"
    assert record.payload["bid_to_cover_ratio"] == "3.11"
    assert record.payload["xml_pdf_crosscheck_verified"] is True
    assert record.payload["auction_arithmetic_verified"] is True
    assert record.payload["price_formula_verified"] is True
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert 19 - record.payload["value_basis_points"] == 19


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived result facts"),
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
        ("wrong_xml_hash", "XML hash mismatch"),
        ("wrong_source_url", "source URL mismatch"),
        ("wrong_source_version", "source version mismatch"),
        ("auction_date", "auction_date mismatch"),
        ("announcement_date", "announcement_date mismatch"),
        ("issue_date", "issue_date mismatch"),
        ("maturity_date", "maturity_date mismatch"),
        ("cusip", "cusip mismatch"),
        ("security_term", "security_term mismatch"),
        ("metric", "metric mismatch"),
        ("value_basis_points", "value_basis_points mismatch"),
        ("reported_high_rate_percent", "reported_high_rate_percent mismatch"),
        ("reported_median_rate_percent", "reported_median_rate_percent mismatch"),
        ("reported_low_rate_percent", "reported_low_rate_percent mismatch"),
        ("reported_investment_rate_percent", "reported_investment_rate_percent mismatch"),
        ("reported_price_per_100", "reported_price_per_100 mismatch"),
        ("bid_to_cover_ratio", "bid_to_cover_ratio mismatch"),
        ("competitive_tendered_dollars", "competitive_tendered_dollars mismatch"),
        ("competitive_accepted_dollars", "competitive_accepted_dollars mismatch"),
        ("subtotal_tendered_dollars", "subtotal_tendered_dollars mismatch"),
        ("subtotal_accepted_dollars", "subtotal_accepted_dollars mismatch"),
        ("total_tendered_dollars", "total_tendered_dollars mismatch"),
        ("total_accepted_dollars", "total_accepted_dollars mismatch"),
        ("official_release_time_local", "official_release_time_local mismatch"),
        ("official_release_timezone", "official_release_timezone mismatch"),
        ("official_release_at", "official_release_at mismatch"),
        ("unit", "unit mismatch"),
        ("xml_pdf_crosscheck_verified", "xml_pdf_crosscheck_verified mismatch"),
        ("auction_arithmetic_verified", "auction_arithmetic_verified mismatch"),
        ("price_formula_verified", "price_formula_verified mismatch"),
        ("release_pdf_sha256", "release_pdf_sha256 mismatch"),
        ("availability_method", "availability_method mismatch"),
        ("release_pdf_url", "PDF URL mismatch"),
    ],
)
def test_treasury_auction_lock_rejects_source_timing_and_value_corruption(
    case: str,
    message: str,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "treasury.other"
    elif case == "wrong_publisher":
        first["source"]["publisher"] = "Other publisher"
    elif case == "wrong_license":
        first["source"]["license_class"] = "redistributable"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "us_treasury_auction:other"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-03-09T15:32:01Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-03-10T04:00:01Z"
    elif case == "future":
        values["decision_time"] = "2020-03-10T03:59:59Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-03-08T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-03-09T15:32:01Z"
    elif case == "wrong_xml_hash":
        first["source"]["sha256"] = "f" * 64
        values["source_response_sha256s"] = sorted(["f" * 64, values["source_response_sha256s"][1]])
    elif case == "wrong_source_url":
        first["source"]["url"] = "https://www.treasurydirect.gov/xml/R_20200309_3.xml"
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "TreasuryAuction:fabricated"
    elif case == "release_pdf_url":
        first["payload"][case] = first["payload"][case].replace("_2.pdf", "_3.pdf")
    else:
        first["payload"][case] = _corrupt(first["payload"][case])
    with pytest.raises(ValidationError, match=message):
        TreasuryAuctionBoundaryInputLock.model_validate(values)


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
def test_treasury_auction_lock_rejects_manifest_role_and_clock_corruption(
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
        values["roles"]["march09_high_rate"] = "missing-record-id"
    elif case == "duplicate_roles":
        values["roles"]["march09_high_rate"] = values["roles"]["march16_high_rate"]
    elif case == "unsorted_hashes":
        values["source_response_sha256s"].reverse()
    elif case == "duplicate_hashes":
        values["source_response_sha256s"][1] = values["source_response_sha256s"][0]
    elif case == "hash_set_mismatch":
        values["source_response_sha256s"] = sorted([values["source_response_sha256s"][0], "f" * 64])
    elif case == "naive_decision_time":
        values["decision_time"] = "2020-03-18T12:00:00"
    elif case == "naive_build_epoch":
        values["build_epoch"] = "2026-08-13T07:50:00"
    with pytest.raises(ValidationError, match=message):
        TreasuryAuctionBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_treasury_auction_lock_creation_loading_hash_and_runtime_guards(
    tmp_path: Path,
) -> None:
    values = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    values.pop("lock_sha256")
    recreated = TreasuryAuctionBoundaryInputLock.create(values)
    assert recreated == TreasuryAuctionBoundaryInputLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    invalid = tmp_path / "invalid-lock.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Treasury auction input lock"):
        load_treasury_auction_boundary_input_lock(invalid)
    with pytest.raises(ValueError, match="invalid Treasury auction input lock"):
        load_treasury_auction_boundary_input_lock(tmp_path / "missing-lock.json")

    tampered = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "Fabricated Treasury auction boundary title"
    with pytest.raises(ValidationError, match="lock_sha256"):
        TreasuryAuctionBoundaryInputLock.model_validate(tampered)

    lock = load_treasury_auction_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    second = lock.records[1]
    same_value = second.model_copy(update={"payload": {**second.payload, "value_basis_points": 39}})
    bypassed = lock.model_copy(update={"records": (first, same_value)})
    with pytest.raises(ValueError, match="must establish a positive rate decline"):
        build_treasury_auction_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


@pytest.mark.integration
@pytest.mark.parametrize("invalid_value", [True, 39.5, -1, 10_001])
def test_treasury_auction_replay_fails_closed_for_invalid_bypassed_rate(
    invalid_value: object,
) -> None:
    lock = load_treasury_auction_boundary_input_lock(LOCK_PATH)
    first = lock.records[0]
    invalid_record = first.model_copy(
        update={"payload": {**first.payload, "value_basis_points": invalid_value}}
    )
    bypassed = lock.model_copy(update={"records": (invalid_record, lock.records[1])})
    with pytest.raises(ValueError, match=r"rate must be integer|outside supported range"):
        build_treasury_auction_boundary_replay_spec(bypassed, code_commit=CODE_COMMIT)


def _corrupt(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return f"{value}-corrupt"
    raise AssertionError(f"unsupported test value: {value!r}")
