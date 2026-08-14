from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from finreplay.contracts import EvidenceClass, TrialDisposition
from finreplay.engines import EngineName
from finreplay.scenarios import (
    H41LiquiditySwapsBoundaryInputLock,
    OfficialEventLock,
    build_h41_liquidity_swaps_boundary_replay_spec,
    load_h41_liquidity_swaps_boundary_input_lock,
)

LOCK_PATH = Path("scenarios/fed-h41-liquidity-swaps-2020/input-lock.json")
EVENT_PATH = Path("scenarios/fed-h41-liquidity-swaps-2020/event-lock.json")
CODE_COMMIT = "0" * 40


@pytest.mark.integration
def test_h41_swap_boundary_runs_four_engines_from_paired_archived_releases() -> None:
    lock = load_h41_liquidity_swaps_boundary_input_lock(LOCK_PATH)
    spec = build_h41_liquidity_swaps_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
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
        "312ef4c75191536fc8241076af9f42d7e55c90db8f47fdb91a38b11cab1b9580"
    )
    assert all(record.evidence_class is EvidenceClass.REPORTED for record in lock.records)

    timevault = artifacts[EngineName.TIMEVAULT]
    assert timevault.payload["decision_observations_millions"] == {
        "march18_wednesday_outstanding": 45,
        "march25_wednesday_outstanding": 206_051,
        "known_wednesday_increase": 206_006,
    }
    assert timevault.payload["html_ascii_crosscheck_verified"] is True
    assert timevault.payload["swap_exchange_rate_measurement_boundary_retained"] is True
    assert timevault.payload["actual_server_publication_log_available"] is False
    assert timevault.payload["weekly_average_fields_used_as_range_input"] is False
    assert timevault.payload["source_response_file_count"] == 4

    shock = artifacts[EngineName.SHOCKCOMPILER]
    assert shock.evidence_counts == {EvidenceClass.INFERRED: 3}
    assert set(shock.source_hashes) == set(lock.source_response_sha256s)
    assert shock.payload["known_balance_levels"] == {
        "march18_balance_million_dollars": 45,
        "march25_balance_million_dollars": 206_051,
        "known_increase_million_dollars": 206_006,
        "lower_level_million_dollars": 206_051,
        "upper_level_million_dollars": 412_057,
        "range_width_million_dollars": 206_006,
    }
    variable = "next_wednesday_liquidity_swaps_outstanding_million_dollars"
    assert shock.payload["naive_baseline"] == {
        variable: 206_051,
        "definition": "persistence of the March 25 Wednesday balance",
    }
    assert shock.payload["bound_construction"] == {
        "lower_level_million_dollars": 206_051,
        "upper_level_million_dollars": 412_057,
        "range_width_million_dollars": 206_006,
        "known_increase_million_dollars": 206_006,
        "endpoint_method": "latest_level_persistence_or_repeat_one_known_increase",
        "wednesday_balance_only": True,
        "weekly_average_used": False,
        "year_change_used": False,
        "current_market_revaluation_performed": False,
        "probability_assigned": False,
        "future_event_used": False,
    }
    assert shock.payload["applied_endpoints"] == {
        "lower": 206_051.0,
        "upper": 412_057.0,
    }
    parameter = shock.payload["program"]["parameters"][0]
    assert parameter["unit"] == "million_dollars"
    assert parameter["lower"] == 206_051.0
    assert parameter["upper"] == 412_057.0

    by_week = {record.payload["week_ending"]: record for record in lock.records}
    march18 = by_week["2020-03-18"]
    march25 = by_week["2020-03-25"]
    assert march18.payload["value_millions"] == 45
    assert march18.payload["weekly_average_millions"] == 45
    assert march18.source.sha256 == (
        "8261da1e27e2ed08ab3671af4b94c394108e7809a256638d0a7332f8ed60519b"
    )
    assert march25.payload["value_millions"] == 206_051
    assert march25.payload["weekly_average_millions"] == 168_814
    assert march25.payload["weekly_average_change_from_prior_week_millions"] == 168_769
    assert march25.payload["weekly_average_change_from_year_ago_millions"] == 168_748
    assert march25.source.sha256 == (
        "90221fc89c30bf797806200eb6bc725f976ca314d5f9a098c587143d2fc6d540"
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
def test_april_event_is_disjoint_inside_range_and_not_retroactive() -> None:
    lock = load_h41_liquidity_swaps_boundary_input_lock(LOCK_PATH)
    event = OfficialEventLock.model_validate_json(EVENT_PATH.read_text(encoding="utf-8"))
    assert len(event.records) == 1
    record = event.records[0]
    assert record.payload["release_date"] == "2020-04-02"
    assert record.payload["week_ending"] == "2020-04-01"
    assert record.payload["value_millions"] == 348_544
    assert record.payload["weekly_average_millions"] == 327_787
    assert record.payload["official_stated_release_at"] is None
    assert record.payload["availability_method"] == (
        "release_date_following_new_york_midnight_html_ascii"
    )
    assert record.interval.available_at.isoformat() == "2020-04-03T04:00:00+00:00"
    assert record.interval.available_at > lock.decision_time
    assert record.record_id not in {item.record_id for item in lock.records}
    assert record.source.sha256 == (
        "9eb8775b0be1c637c1d58c73f1545cf97716ba313e02412fdd1e0f722dce183b"
    )
    assert 348_544 - 206_051 == 142_493
    assert 412_057 - 348_544 == 63_513

    spec = build_h41_liquidity_swaps_boundary_replay_spec(lock, code_commit=CODE_COMMIT)
    shock = next(
        artifact for artifact in spec.artifacts if artifact.engine is EngineName.SHOCKCOMPILER
    )
    assert shock.payload["bound_construction"]["lower_level_million_dollars"] == 206_051
    assert shock.payload["bound_construction"]["upper_level_million_dollars"] == 412_057
    assert shock.payload["bound_construction"]["future_event_used"] is False


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_source", "only paired release facts"),
        ("wrong_publisher", "publisher mismatch"),
        ("wrong_temporal", "versioned snapshots"),
        ("wrong_license", "license boundary"),
        ("wrong_redistribution", "redistribution boundary"),
        ("wrong_semantic_hash", "semantic hash"),
        ("wrong_url", "source URL"),
        ("wrong_version", "source version"),
        ("wrong_vintage", "source vintage"),
        ("observed", "must remain reported"),
        ("wrong_entity", "entity mismatch"),
        ("wrong_schema", "payload schema"),
        ("low_confidence", "timing must remain deterministic"),
        ("wrong_rule", "availability rule"),
        ("wrong_published", "publication boundary"),
        ("wrong_available", "availability boundary"),
        ("wrong_valid", "validity time"),
        ("wrong_valid_to", "open snapshots"),
        ("wrong_revised", "open snapshots"),
        ("wrong_ingested", "retrieval and ingestion"),
        ("retrieved_after_build", "after build_epoch"),
        ("wrong_value", "value_millions mismatch"),
        ("extra_payload", "payload hash"),
    ],
)
def test_h41_swap_lock_rejects_source_timing_and_payload_corruption(
    case: str,
    message: str,
) -> None:
    values = _lock_values()
    first = values["records"][0]
    if case == "wrong_source":
        first["source"]["source_id"] = "federal_reserve.h41.other"
    elif case == "wrong_publisher":
        first["source"]["publisher"] = "Other publisher"
    elif case == "wrong_temporal":
        first["source"]["temporal_coverage"] = "latest_only"
        first["source"]["vintage_as_of"] = None
    elif case == "wrong_license":
        first["source"]["license_class"] = "redistributable"
    elif case == "wrong_redistribution":
        first["source"]["redistribution_note"] = "fabricated permission"
    elif case == "wrong_semantic_hash":
        first["source"]["sha256"] = "0" * 64
    elif case == "wrong_url":
        first["source"]["url"] = "https://www.federalreserve.gov/releases/h41/other.htm"
    elif case == "wrong_version":
        first["source"]["source_version"] = "H41-SWAPS:wrong"
    elif case == "wrong_vintage":
        first["source"]["vintage_as_of"] = "2020-03-19T20:30:01Z"
    elif case == "observed":
        first["evidence_class"] = "observed"
    elif case == "wrong_entity":
        first["entity_id"] = "federal_reserve_facility:other"
    elif case == "wrong_schema":
        first["payload_schema_version"] = "1.1.0"
    elif case == "low_confidence":
        first["interval"]["availability_confidence"] = 0.99
    elif case == "wrong_rule":
        first["interval"]["availability_rule"] = "current response headers"
    elif case == "wrong_published":
        first["interval"]["published_at"] = "2020-03-19T20:29:59Z"
    elif case == "wrong_available":
        first["interval"]["available_at"] = "2020-03-19T20:30:01Z"
    elif case == "wrong_valid":
        first["interval"]["valid_from"] = "2020-03-18T00:00:01Z"
    elif case == "wrong_valid_to":
        first["interval"]["valid_to"] = "2020-03-20T00:00:00Z"
    elif case == "wrong_revised":
        first["interval"]["revised_at"] = "2020-03-19T20:30:00Z"
    elif case == "wrong_ingested":
        first["interval"]["ingested_at"] = "2026-08-14T10:25:46Z"
    elif case == "retrieved_after_build":
        first["source"]["retrieved_at"] = "2026-08-14T10:30:01Z"
        first["interval"]["ingested_at"] = "2026-08-14T10:30:01Z"
    elif case == "wrong_value":
        first["payload"]["value_millions"] = 46
    elif case == "extra_payload":
        first["payload"]["fabricated"] = True

    with pytest.raises((ValidationError, ValueError), match=message):
        H41LiquiditySwapsBoundaryInputLock.model_validate(values)


@pytest.mark.integration
def test_h41_swap_lock_rejects_hash_roles_decision_and_self_hash_corruption() -> None:
    wrong_hashes = _lock_values()
    wrong_hashes["source_response_sha256s"][0] = "0" * 64
    with pytest.raises(ValidationError, match="four official responses"):
        H41LiquiditySwapsBoundaryInputLock.model_validate(wrong_hashes)

    wrong_receipt = _lock_values()
    wrong_receipt["supporting_receipt_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="supporting receipt"):
        H41LiquiditySwapsBoundaryInputLock.model_validate(wrong_receipt)

    duplicate_roles = _lock_values()
    duplicate_roles["roles"]["march25_decision_release"] = duplicate_roles["roles"][
        "march18_release"
    ]
    with pytest.raises(ValidationError, match="role record IDs must be unique"):
        H41LiquiditySwapsBoundaryInputLock.model_validate(duplicate_roles)

    wrong_decision = _lock_values()
    wrong_decision["decision_time"] = "2020-03-26T20:31:00Z"
    with pytest.raises(ValidationError, match="March 26 stated release"):
        H41LiquiditySwapsBoundaryInputLock.model_validate(wrong_decision)

    wrong_self_hash = _lock_values()
    wrong_self_hash["lock_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="lock_sha256"):
        H41LiquiditySwapsBoundaryInputLock.model_validate(wrong_self_hash)


@pytest.mark.integration
def test_h41_event_lock_rejects_generic_inexact_timing() -> None:
    event = cast(dict[str, Any], json.loads(EVENT_PATH.read_text(encoding="utf-8")))
    event["records"][0]["interval"]["availability_confidence"] = 0.99
    with pytest.raises(ValidationError, match="exact or satisfy the qualified CFTC"):
        OfficialEventLock.model_validate(event)


def _lock_values() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(LOCK_PATH.read_text(encoding="utf-8")))
