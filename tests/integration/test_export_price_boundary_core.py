from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName
from finreplay.scenarios import (
    ExportPriceBoundaryInputLock,
    OfficialEventLock,
    build_export_price_boundary_replay_spec,
    load_export_price_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/bls-export-prices-2020/input-lock.json")
EVENT_PATH = Path("scenarios/bls-export-prices-2020/event-lock.json")
CODE_COMMIT = "0" * 40


@pytest.mark.integration
def test_export_price_boundary_runs_four_engines_from_archived_release_pairs() -> None:
    lock = load_export_price_boundary_input_lock(LOCK_PATH)
    spec = build_export_price_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}

    assert set(artifacts) == {
        EngineName.TIMEVAULT,
        EngineName.SHOCKCOMPILER,
        EngineName.TRIALCOURT,
        EngineName.REPLAYSTUDIO,
    }
    assert spec.distinct_input_records == 2
    assert spec.derived_records == 6
    assert len(lock.source_response_sha256s) == 4
    assert lock.supporting_receipt_sha256 == (
        "744153523c39d1b8df64900dad2544aec0d30c00c669f01ddc358cd64f5c630c"
    )
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)

    timevault = artifacts[EngineName.TIMEVAULT]
    assert timevault.payload["decision_observations_basis_points"] == {
        "january_all_exports_monthly_change": 70,
        "february_all_exports_monthly_change": -110,
        "february_prior_january_change": 60,
        "january_revision_delta_basis_points": -10,
    }
    assert timevault.payload["html_pdf_crosscheck_verified"] is True
    assert timevault.payload["adjacent_prior_value_crosscheck_verified"] is True
    assert timevault.payload["revision_window_months"] == 3
    assert timevault.payload["seasonally_adjusted"] is False
    assert timevault.payload["source_auxiliary_measures_used_as_range_input"] is False
    assert timevault.payload["source_response_file_count"] == 4

    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert set(shock.source_hashes) == set(lock.source_response_sha256s)
    assert shock.payload["known_release_changes"] == {
        "january_change_basis_points": 70,
        "february_change_basis_points": -110,
        "known_decline_basis_points": 180,
        "lower_change_basis_points": -290,
        "upper_change_basis_points": -110,
        "range_width_basis_points": 180,
    }
    variable = "next_all_exports_monthly_change_basis_points"
    assert shock.payload["naive_baseline"] == {
        variable: -110,
        "definition": "persistence of the February all-export monthly change",
    }
    assert shock.payload["bound_construction"] == {
        "lower_change_basis_points": -290,
        "upper_change_basis_points": -110,
        "range_width_basis_points": 180,
        "known_decline_basis_points": 180,
        "endpoint_method": "latest_change_persistence_or_repeat_one_known_decline",
        "original_release_values_only": True,
        "source_auxiliary_measures_used": False,
        "prior_revision_used_as_endpoint": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {"lower": -290.0, "upper": -110.0}
    parameter = shock.payload["program"]["parameters"][0]
    assert parameter["unit"] == "basis_points"
    assert parameter["lower"] == -290.0
    assert parameter["upper"] == -110.0

    by_month = {record.payload["reference_month"]: record for record in lock.records}
    january = by_month["2020-01"]
    february = by_month["2020-02"]
    assert january.payload["release_number"] == "USDL-20-0247"
    assert january.payload["value_basis_points"] == 70
    assert january.payload["release_pdf_pages"] == 18
    assert january.source.sha256 == (
        "186c6a60276ac896bdf37e1db97e7c6a313dd5e2cd2087e592b2ae8a76323327"
    )
    assert january.payload["release_html_sha256"] == (
        "dcac2c1daecc12c2bce0769999b467e25b4a4c6dea66af3538feb88fe72247ce"
    )
    assert february.payload["release_number"] == "USDL-20-0405"
    assert february.payload["value_basis_points"] == -110
    assert february.payload["prior_month_change_tenths_percent"] == 6
    assert february.payload["prior_month_value_in_previous_release_tenths_percent"] == 7
    assert february.payload["prior_month_revision_delta_tenths_percent"] == -1
    assert february.payload["release_pdf_pages"] == 18
    assert february.source.sha256 == (
        "e0167a9ec66bc0b884d0f58c5e7de42ddc8fd849f150bf438f9590f4be7fbbf9"
    )
    assert february.payload["release_html_sha256"] == (
        "1b196f0ebed0fdd41d27a7696f956a5e962b1178b0687eade2ce06f845db15ae"
    )

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
def test_march_event_is_disjoint_and_inside_the_fixed_range_without_success_claim() -> None:
    lock = load_export_price_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-04-14"
    assert record.payload["reference_month"] == "2020-03"
    assert record.payload["release_number"] == "USDL-20-0610"
    assert record.payload["value_tenths_percent"] == -16
    assert record.payload["value_basis_points"] == -160
    assert record.payload["prior_month_change_tenths_percent"] == -11
    assert record.payload["prior_month_value_in_previous_release_tenths_percent"] == -11
    assert record.payload["prior_month_revision_delta_tenths_percent"] == 0
    assert record.payload["release_pdf_pages"] == 18
    assert record.source.sha256 == (
        "215974814451294a33cfae984599752e5c9c5d1dc0e432031d8d49b484b6e382"
    )
    assert record.payload["release_html_sha256"] == (
        "b5433f3a694f72261a14801e922459eb74cebea96c00ae1f6b2610ce5e786ae5"
    )
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert record.payload["value_basis_points"] - (-290) == 130
    assert -110 - record.payload["value_basis_points"] == 50
    assert "does not become forecast success" in event.claim_boundary

    spec = build_export_price_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    shock = next(
        artifact for artifact in spec.artifacts if artifact.engine is EngineName.SHOCKCOMPILER
    )
    assert shock.payload["bound_construction"]["lower_change_basis_points"] == -290
    assert shock.payload["bound_construction"]["upper_change_basis_points"] == -110
    assert shock.payload["bound_construction"]["future_event_used"] is False
    assert shock.payload["bound_construction"]["prior_revision_used_as_endpoint"] is False


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only paired archived release facts"),
        ("wrong_publisher", "publisher mismatch"),
        ("wrong_temporal", "versioned release snapshots"),
        ("wrong_license", "license boundary"),
        ("wrong_redistribution", "redistribution boundary"),
        ("wrong_primary_hash", "primary PDF hash"),
        ("wrong_url", "primary source URL"),
        ("wrong_version", "source version"),
        ("wrong_vintage", "source vintage"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("wrong_schema", "payload schema"),
        ("low_confidence", "timing must be deterministic"),
        ("wrong_rule", "availability rule"),
        ("wrong_published", "publication time"),
        ("wrong_available", "availability time"),
        ("wrong_valid", "validity time"),
        ("wrong_valid_to", "open snapshots"),
        ("wrong_revised", "open snapshots"),
        ("wrong_ingested", "retrieval and ingestion"),
        ("retrieved_after_build", "after build_epoch"),
        ("wrong_value", "value_basis_points mismatch"),
        ("extra_payload", "payload hash"),
    ],
)
def test_export_price_lock_rejects_source_timing_and_payload_corruption(
    case: str,
    message: str,
) -> None:
    values = _lock_values()
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "bls.export_prices.other"
    elif case == "wrong_publisher":
        first["source"]["publisher"] = "Other publisher"
    elif case == "wrong_temporal":
        first["source"]["temporal_coverage"] = "latest_only"
        first["source"]["vintage_as_of"] = None
    elif case == "wrong_license":
        first["source"]["license_class"] = "download_only"
    elif case == "wrong_redistribution":
        first["source"]["redistribution_note"] = "fabricated permission"
    elif case == "wrong_primary_hash":
        first["source"]["sha256"] = "0" * 64
    elif case == "wrong_url":
        first["source"]["url"] = "https://www.bls.gov/news.release/archives/ximpim_other.pdf"
    elif case == "wrong_version":
        first["source"]["source_version"] = "BLS-MXP:wrong"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-02-14T13:30:01Z"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "bls_export_price_index:other"
    elif case == "wrong_schema":
        first["payload_schema_version"] = "1.1.0"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_rule":
        first["interval"]["availability_rule"] = "current response headers"
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-02-14T13:29:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-02-14T13:30:01Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-01-01T00:00:01Z"
    elif case == "wrong_valid_to":
        first["interval"]["valid_to"] = "2020-02-15T00:00:00Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = "2020-02-14T13:30:00Z"
    elif case == "wrong_ingested":
        first["interval"]["ingested_at"] = "2026-08-14T11:06:45Z"
    elif case == "retrieved_after_build":
        first["source"]["retrieved_at"] = "2026-08-14T12:20:01Z"
        first["interval"]["ingested_at"] = "2026-08-14T12:20:01Z"
    elif case == "wrong_value":
        first["payload"]["value_basis_points"] = 1
    elif case == "extra_payload":
        first["payload"]["fabricated"] = True

    with pytest.raises((ValidationError, ValueError), match=message):
        ExportPriceBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_export_price_lock_rejects_hash_roles_decision_and_self_hash_corruption() -> None:
    wrong_hashes = _lock_values()
    wrong_hashes["source_response_sha256s"][0] = "0" * 64
    with pytest.raises(ValidationError, match="four official responses"):
        ExportPriceBoundaryInputLock.model_validate(wrong_hashes)

    wrong_receipt = _lock_values()
    wrong_receipt["supporting_receipt_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="supporting receipt"):
        ExportPriceBoundaryInputLock.model_validate(wrong_receipt)

    duplicate_roles = _lock_values()
    duplicate_roles["roles"]["february_decision_release"] = duplicate_roles["roles"][
        "january_release"
    ]
    with pytest.raises(ValidationError, match="role record IDs must be unique"):
        ExportPriceBoundaryInputLock.model_validate(duplicate_roles)

    wrong_decision = _lock_values()
    wrong_decision["decision_time"] = "2020-03-13T12:31:00Z"
    with pytest.raises(ValidationError, match="March 13 embargo end"):
        ExportPriceBoundaryInputLock.model_validate(wrong_decision)

    wrong_self_hash = _lock_values()
    wrong_self_hash["lock_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="lock_sha256"):
        ExportPriceBoundaryInputLock.model_validate(wrong_self_hash)


@pytest.mark.integration
def test_export_price_event_lock_rejects_inexact_timing() -> None:
    event = cast(dict[str, Any], json.loads(EVENT_PATH.read_text(encoding="utf-8")))
    event["records"][0]["interval"]["availability_confidence"] = 0.99
    with pytest.raises(ValidationError, match="exact or satisfy the qualified CFTC"):
        OfficialEventLock.model_validate(event)


def _lock_values() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(LOCK_PATH.read_text(encoding="utf-8")))
