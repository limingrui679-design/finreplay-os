from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName
from finreplay.scenarios import (
    OfficialEventLock,
    PPIBoundaryInputLock,
    build_ppi_boundary_replay_spec,
    load_ppi_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/bls-ppi-2020/input-lock.json")
EVENT_PATH = Path("scenarios/bls-ppi-2020/event-lock.json")
CODE_COMMIT = "0" * 40


@pytest.mark.integration
def test_ppi_boundary_runs_four_engines_from_paired_archived_releases() -> None:
    lock = load_ppi_boundary_input_lock(LOCK_PATH)
    spec = build_ppi_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
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
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)

    timevault = artifacts[EngineName.TIMEVAULT]
    assert timevault.payload["decision_observations_basis_points"] == {
        "february_final_demand_monthly_change": -60,
        "march_final_demand_monthly_change": -20,
        "march_prior_february_change": -60,
    }
    assert timevault.payload["html_pdf_crosscheck_verified"] is True
    assert timevault.payload["adjacent_prior_value_crosscheck_verified"] is True
    assert timevault.payload["source_auxiliary_measures_used_as_range_input"] is False
    assert timevault.payload["source_response_file_count"] == 4

    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert set(shock.source_hashes) == set(lock.source_response_sha256s)
    assert shock.payload["known_release_changes"] == {
        "february_change_basis_points": -60,
        "march_change_basis_points": -20,
        "known_increase_basis_points": 40,
        "lower_change_basis_points": -20,
        "upper_change_basis_points": 20,
        "range_width_basis_points": 40,
    }
    variable = "next_final_demand_monthly_change_basis_points"
    assert shock.payload["naive_baseline"] == {
        variable: -20,
        "definition": "persistence of the March final-demand monthly change",
    }
    assert shock.payload["bound_construction"] == {
        "lower_change_basis_points": -20,
        "upper_change_basis_points": 20,
        "range_width_basis_points": 40,
        "known_increase_basis_points": 40,
        "endpoint_method": "latest_change_persistence_or_repeat_one_known_increase",
        "original_release_values_only": True,
        "source_auxiliary_measures_used": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {"lower": -20.0, "upper": 20.0}
    parameter = shock.payload["program"]["parameters"][0]
    assert parameter["unit"] == "basis_points"
    assert parameter["lower"] == -20.0
    assert parameter["upper"] == 20.0

    by_month = {record.payload["reference_month"]: record for record in lock.records}
    february = by_month["2020-02"]
    march = by_month["2020-03"]
    assert february.payload["release_number"] == "USDL 20-0404"
    assert february.payload["value_tenths_percent"] == -6
    assert february.payload["value_basis_points"] == -60
    assert february.payload["release_pdf_pages"] == 32
    assert february.source.sha256 == (
        "392c9ee30d9deae5007a796917f8c332ecbc617e947a61b38562d67fc86c96b2"
    )
    assert february.payload["release_html_sha256"] == (
        "515855b318616035f7d4a9d06672f90636f3ec3e424a630a0eb6076167573ca2"
    )
    assert march.payload["release_number"] == "USDL 20-0567"
    assert march.payload["value_tenths_percent"] == -2
    assert march.payload["value_basis_points"] == -20
    assert march.payload["prior_month_change_tenths_percent"] == -6
    assert march.payload["prior_month_revision_delta_tenths_percent"] == 0
    assert march.payload["release_pdf_pages"] == 31
    assert march.source.sha256 == (
        "18540697b82c4cbb42703f24a44d808661bf2baf8883b135a2c1a385c1c6d7fb"
    )
    assert march.payload["release_html_sha256"] == (
        "318dafbdf942ea9ac3157e4369de66cc11f09994f7ff8d07de3c159cd9d3f9ec"
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
def test_april_event_is_disjoint_and_misses_the_fixed_lower_endpoint() -> None:
    lock = load_ppi_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-05-13"
    assert record.payload["reference_month"] == "2020-04"
    assert record.payload["release_number"] == "USDL 20-0920"
    assert record.payload["value_tenths_percent"] == -13
    assert record.payload["value_basis_points"] == -130
    assert record.payload["prior_month_change_tenths_percent"] == -2
    assert record.payload["prior_month_revision_delta_tenths_percent"] == 0
    assert record.payload["release_pdf_pages"] == 31
    assert record.source.sha256 == (
        "eda79108129061e29ebccc1b26bce97df55326d66d4bb01855a9fdbafc8b067c"
    )
    assert record.payload["release_html_sha256"] == (
        "f26f413c1b8aa505baaa25b995ce0ce69f280b6c30bf08b645dc24f0fdce9900"
    )
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert -20 - record.payload["value_basis_points"] == 110

    spec = build_ppi_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    shock = next(
        artifact for artifact in spec.artifacts if artifact.engine is EngineName.SHOCKCOMPILER
    )
    assert shock.payload["bound_construction"]["lower_change_basis_points"] == -20
    assert shock.payload["bound_construction"]["upper_change_basis_points"] == 20
    assert shock.payload["bound_construction"]["future_event_used"] is False


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only paired archived"),
        ("wrong_publisher", "publisher mismatch"),
        ("wrong_temporal", "versioned release snapshots"),
        ("wrong_license", "license boundary"),
        ("wrong_redistribution", "redistribution boundary"),
        ("wrong_primary_hash", "primary PDF hash"),
        ("wrong_url", "primary source URL"),
        ("wrong_version", "source version"),
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
        ("wrong_vintage", "source vintage"),
        ("wrong_ingested", "retrieval and ingestion"),
        ("retrieved_after_build", "after build_epoch"),
        ("extra_payload", "payload hash"),
    ],
)
def test_ppi_lock_rejects_source_timing_and_payload_corruption(
    case: str,
    message: str,
) -> None:
    values = _lock_values()
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "bls.ppi.other"
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
        first["source"]["url"] = "https://www.bls.gov/news.release/archives/ppi_other.pdf"
    elif case == "wrong_version":
        first["source"]["source_version"] = "BLS-PPI:wrong"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "bls_ppi:other"
    elif case == "wrong_schema":
        first["payload_schema_version"] = "1.1.0"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_rule":
        first["interval"]["availability_rule"] = "current response headers"
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-03-12T12:29:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-03-12T12:30:01Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-02-01T00:00:01Z"
    elif case == "wrong_valid_to":
        first["interval"]["valid_to"] = "2020-03-13T00:00:00Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = "2020-03-12T12:30:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-03-12T12:30:01Z"
    elif case == "wrong_ingested":
        first["interval"]["ingested_at"] = "2026-08-14T08:30:59Z"
    elif case == "retrieved_after_build":
        first["source"]["retrieved_at"] = "2026-08-14T08:40:01Z"
        first["interval"]["ingested_at"] = "2026-08-14T08:40:01Z"
    elif case == "extra_payload":
        first["payload"]["fabricated"] = True

    with pytest.raises((ValidationError, ValueError), match=message):
        PPIBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_ppi_lock_rejects_hash_roles_decision_and_self_hash_corruption() -> None:
    wrong_hashes = _lock_values()
    wrong_hashes["source_response_sha256s"][0] = "0" * 64
    with pytest.raises(ValidationError, match="four official responses"):
        PPIBoundaryInputLock.model_validate(wrong_hashes)

    duplicate_roles = _lock_values()
    duplicate_roles["roles"]["march_decision_release"] = duplicate_roles["roles"][
        "february_release"
    ]
    with pytest.raises(ValidationError, match="role record IDs must be unique"):
        PPIBoundaryInputLock.model_validate(duplicate_roles)

    wrong_decision = _lock_values()
    wrong_decision["decision_time"] = "2020-04-09T12:31:00Z"
    with pytest.raises(ValidationError, match="must equal the April 9 embargo end"):
        PPIBoundaryInputLock.model_validate(wrong_decision)

    wrong_self_hash = _lock_values()
    wrong_self_hash["lock_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="lock_sha256"):
        PPIBoundaryInputLock.model_validate(wrong_self_hash)


def _lock_values() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(LOCK_PATH.read_text(encoding="utf-8")))
