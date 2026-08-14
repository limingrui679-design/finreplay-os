from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName
from finreplay.scenarios import (
    NewHomeSalesLevelBoundaryInputLock,
    OfficialEventLock,
    build_new_home_sales_level_boundary_replay_spec,
    load_new_home_sales_level_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/census-nrs-2020/input-lock.json")
EVENT_PATH = Path("scenarios/census-nrs-2020/event-lock.json")
CODE_COMMIT = "0" * 40


@pytest.mark.integration
def test_new_home_sales_boundary_runs_four_engines_from_one_decision_snapshot() -> None:
    lock = load_new_home_sales_level_boundary_input_lock(LOCK_PATH)
    spec = build_new_home_sales_level_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    artifacts = {artifact.engine: artifact for artifact in spec.artifacts}

    assert set(artifacts) == {
        EngineName.TIMEVAULT,
        EngineName.SHOCKCOMPILER,
        EngineName.TRIALCOURT,
        EngineName.REPLAYSTUDIO,
    }
    assert spec.distinct_input_records == 2
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)
    assert len(lock.source_response_sha256s) == 2

    timevault = artifacts[EngineName.TIMEVAULT]
    assert timevault.payload["decision_snapshot"] == {
        "revised_january_sales_units_saar": 800_000,
        "initial_february_sales_units_saar": 765_000,
    }
    assert timevault.payload["january_initial_release_retained_for_revision_lineage"] is True
    assert timevault.payload["january_initial_release_used_as_endpoint_input"] is False
    assert timevault.payload["official_sampling_interval_used_as_range_input"] is False
    assert timevault.payload["source_evidence_file_count"] == 2

    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert set(shock.source_hashes) == set(lock.source_response_sha256s)
    assert shock.payload["known_decision_snapshot_levels"] == {
        "january_initial_release_sales_units_saar": 764_000,
        "decision_snapshot_revised_january_sales_units_saar": 800_000,
        "january_revision_delta_known_at_decision_units_saar": 36_000,
        "february_initial_sales_units_saar": 765_000,
        "known_decision_snapshot_decline_units_saar": 35_000,
        "lower_level_units_saar": 730_000,
        "upper_level_units_saar": 765_000,
        "range_width_units_saar": 35_000,
    }
    variable = "next_new_single_family_houses_sold_level_units_saar"
    assert shock.payload["naive_baseline"] == {
        variable: 765_000,
        "definition": "persistence of the February initial NRS sales level",
    }
    assert shock.payload["bound_construction"] == {
        "lower_level_units_saar": 730_000,
        "upper_level_units_saar": 765_000,
        "range_width_units_saar": 35_000,
        "known_decision_snapshot_decline_units_saar": 35_000,
        "endpoint_method": (
            "latest_initial_level_persistence_or_repeat_same_release_snapshot_decline"
        ),
        "basis_is_single_february_release_snapshot": True,
        "january_initial_release_used_as_numeric_endpoint_input": False,
        "official_sampling_interval_used": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {"lower": 730_000.0, "upper": 765_000.0}
    parameter = shock.payload["program"]["parameters"][0]
    assert parameter["unit"] == "houses_at_seasonally_adjusted_annual_rate"
    assert parameter["lower"] == 730_000.0
    assert parameter["upper"] == 765_000.0

    january = next(
        record for record in lock.records if record.record_id == lock.roles.january_release_snapshot
    )
    february = next(
        record
        for record in lock.records
        if record.record_id == lock.roles.february_decision_snapshot
    )
    assert january.payload["value_units"] == 764_000
    assert january.payload["release_timezone_abbreviation"] == "EST"
    assert february.payload["value_units"] == 765_000
    assert february.payload["prior_month_revised_value_units"] == 800_000
    assert february.payload["prior_month_value_in_previous_release_units"] == 764_000
    assert february.payload["prior_month_revision_delta_units"] == 36_000
    assert february.payload["reported_monthly_margin_90_percent"] == "14.8"
    assert february.payload["reported_monthly_ci_includes_zero"] is True
    for record in lock.records:
        assert record.payload["release_pdf_pages"] == 5
        assert record.payload["pdf_table_snapshot_verified"] is True
        assert record.payload["sale_definition_boundary"].startswith("deposit taken")

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
def test_march_event_is_disjoint_revised_and_breaches_fixed_lower_endpoint() -> None:
    lock = load_new_home_sales_level_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-04-23"
    assert record.payload["reference_month"] == "2020-03"
    assert record.payload["value_units"] == 627_000
    assert record.payload["prior_month_revised_value_units"] == 741_000
    assert record.payload["prior_month_value_in_previous_release_units"] == 765_000
    assert record.payload["prior_month_revision_delta_units"] == -24_000
    assert record.payload["reported_monthly_change_percent"] == "-15.4"
    assert record.payload["reported_monthly_margin_90_percent"] == "14.8"
    assert record.payload["reported_monthly_ci_includes_zero"] is False
    assert record.payload["covid_publication_standard_statement_present"] is True
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert 730_000 - record.payload["value_units"] == 103_000


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only archived new-home-sales facts"),
        ("wrong_publisher", "source publisher mismatch"),
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
        ("wrong_revised", "initial monthly-release records"),
        ("wrong_valid_to", "open valid-time intervals"),
        ("wrong_valid", "valid time mismatch"),
        ("wrong_vintage", "source vintage mismatch"),
        ("wrong_ingested", "retrieval and ingestion times must agree"),
        ("retrieved_before_release", "ingested_at must not precede historical availability"),
        ("retrieved_after_build", "retrieval cannot occur after build_epoch"),
        ("wrong_source_url", "source URL mismatch"),
        ("wrong_source_version", "source version mismatch"),
        ("extra_payload", "payload hash mismatch"),
    ],
)
def test_new_home_sales_lock_rejects_source_timing_and_payload_corruption(
    case: str,
    message: str,
) -> None:
    values = _lock_values()
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "census.hud.nrs.other"
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
        first["entity_id"] = "census_hud_nrs:other"
    elif case == "wrong_schema":
        first["payload_schema_version"] = "1.1.0"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.5
    elif case == "wrong_rule":
        first["interval"]["availability_rule"] = "current response headers"
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-02-26T14:59:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-02-26T15:00:01Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = "2020-02-26T15:00:00Z"
    elif case == "wrong_valid_to":
        first["interval"]["valid_to"] = "2020-02-01T00:00:00Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-01-02T00:00:00Z"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-02-26T15:00:01Z"
    elif case == "wrong_ingested":
        first["interval"]["ingested_at"] = "2026-08-14T07:06:49Z"
    elif case == "retrieved_before_release":
        first["source"]["retrieved_at"] = "2020-02-26T14:59:59Z"
        first["interval"]["ingested_at"] = "2020-02-26T14:59:59Z"
    elif case == "retrieved_after_build":
        first["source"]["retrieved_at"] = "2026-08-14T07:15:01Z"
        first["interval"]["ingested_at"] = "2026-08-14T07:15:01Z"
    elif case == "wrong_source_url":
        first["source"]["url"] = "https://www.census.gov/construction/nrs/pdf/other.pdf"
    elif case == "wrong_source_version":
        first["source"]["source_version"] = "CENSUS-HUD-NRS:wrong"
    elif case == "extra_payload":
        first["payload"]["fabricated"] = True

    with pytest.raises((ValidationError, ValueError), match=message):
        NewHomeSalesLevelBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_new_home_sales_lock_rejects_hash_role_and_decision_snapshot_corruption() -> None:
    wrong_hashes = _lock_values()
    wrong_hashes["source_response_sha256s"][0] = "0" * 64
    with pytest.raises(ValidationError, match="source hash set"):
        NewHomeSalesLevelBoundaryInputLock.model_validate(wrong_hashes)

    swapped_roles = _lock_values()
    roles = swapped_roles["roles"]
    roles["january_release_snapshot"], roles["february_decision_snapshot"] = (
        roles["february_decision_snapshot"],
        roles["january_release_snapshot"],
    )
    with pytest.raises(ValidationError, match="publication time mismatch"):
        NewHomeSalesLevelBoundaryInputLock.model_validate(swapped_roles)

    stale_january = _lock_values()
    february = stale_january["records"][1]
    assert february["payload"]["reference_month"] == "2020-02"
    february["payload"]["prior_month_revised_value_units"] = 764_000
    with pytest.raises(ValidationError, match=r"prior_month_revised_value_units mismatch"):
        NewHomeSalesLevelBoundaryInputLock.model_validate(stale_january)

    wrong_lock_hash = _lock_values()
    wrong_lock_hash["title"] = f"{wrong_lock_hash['title']} changed"
    with pytest.raises(ValidationError, match="lock_sha256"):
        NewHomeSalesLevelBoundaryInputLock.model_validate(wrong_lock_hash)


@pytest.mark.integration
def test_new_home_sales_lock_create_is_deterministic_and_self_hashing() -> None:
    values = _lock_values()
    original_hash = values.pop("lock_sha256")
    rebuilt = NewHomeSalesLevelBoundaryInputLock.create(values)
    assert rebuilt.lock_sha256 == original_hash
    assert rebuilt.model_dump(mode="json") == _lock_values()


def _lock_values() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(LOCK_PATH.read_text(encoding="utf-8")))
