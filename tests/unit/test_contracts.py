from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pydantic import HttpUrl, ValidationError

from finreplay.contracts import (
    ArtifactStatus,
    BitemporalInterval,
    BitemporalRecord,
    CostModel,
    EdgeEvidence,
    EvidenceClass,
    LicenseClass,
    ReplayPackManifest,
    ScenarioMode,
    ScenarioSpec,
    SourceReference,
    TemporalCoverage,
    TrialSpec,
)

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def source() -> SourceReference:
    return SourceReference(
        source_id="official.test.fixture",
        publisher="Official Test Publisher",
        url=HttpUrl("https://example.gov/source.csv"),
        retrieved_at=NOW,
        source_version="fixture-v1",
        sha256="0" * 64,
        license_class=LicenseClass.REDISTRIBUTABLE,
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        vintage_as_of=NOW,
        redistribution_note="Test fixture only.",
    )


def test_bitemporal_interval_accepts_ordered_aware_clocks() -> None:
    interval = BitemporalInterval(
        valid_from=NOW - timedelta(days=100),
        published_at=NOW - timedelta(days=10),
        available_at=NOW - timedelta(days=9),
        revised_at=NOW - timedelta(days=3),
        ingested_at=NOW,
        availability_rule="Official publication timestamp plus one-day conservative delay.",
        availability_confidence=0.95,
    )
    assert interval.available_at > interval.published_at


@pytest.mark.parametrize(
    ("published", "available", "ingested", "match"),
    [
        (NOW, NOW - timedelta(seconds=1), NOW, "available_at"),
        (NOW - timedelta(days=2), NOW, NOW - timedelta(seconds=1), "ingested_at"),
    ],
)
def test_bitemporal_interval_rejects_impossible_clock_order(
    published: datetime, available: datetime, ingested: datetime, match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        BitemporalInterval(
            valid_from=NOW - timedelta(days=20),
            published_at=published,
            available_at=available,
            ingested_at=ingested,
            availability_rule="Official timestamp.",
            availability_confidence=1.0,
        )


def test_bitemporal_interval_rejects_naive_datetimes() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        BitemporalInterval(
            valid_from=NOW.replace(tzinfo=None),
            published_at=NOW - timedelta(days=2),
            available_at=NOW - timedelta(days=1),
            ingested_at=NOW,
            availability_rule="Fixture with one naive timestamp.",
            availability_confidence=1.0,
        )


def test_bitemporal_interval_rejects_reversed_validity_and_revision() -> None:
    with pytest.raises(ValidationError, match="valid_to"):
        BitemporalInterval(
            valid_from=NOW,
            valid_to=NOW - timedelta(days=1),
            published_at=NOW - timedelta(days=3),
            available_at=NOW - timedelta(days=2),
            ingested_at=NOW,
            availability_rule="Official timestamp.",
            availability_confidence=1.0,
        )

    with pytest.raises(ValidationError, match="revised_at"):
        BitemporalInterval(
            valid_from=NOW - timedelta(days=20),
            published_at=NOW - timedelta(days=3),
            available_at=NOW - timedelta(days=2),
            revised_at=NOW - timedelta(days=4),
            ingested_at=NOW,
            availability_rule="Official timestamp.",
            availability_confidence=1.0,
        )


def test_cost_model_rejects_frictionless_strategy() -> None:
    with pytest.raises(ValidationError, match="trading friction"):
        CostModel(
            commission_bps=0,
            half_spread_bps=0,
            market_impact_bps=0,
            borrow_bps_annual=0,
            max_participation_rate=0.1,
        )


def test_trial_requires_declared_attempts_and_nonzero_cost() -> None:
    trial = TrialSpec(
        trial_id="svb.duration.signal.v1",
        hypothesis="Public duration and uninsured-deposit signals precede acute funding stress.",
        economic_mechanism=(
            "Rate shocks lower securities values while concentrated deposits can leave quickly."
        ),
        preregistered_at=NOW,
        holdout_start=date(2022, 1, 1),
        holdout_end=date(2023, 3, 10),
        purge_days=30,
        embargo_days=30,
        declared_attempts=1,
        primary_metric="lead-time adjusted precision-recall",
        expected_direction="positive",
        cost_model=CostModel(
            commission_bps=0.1,
            half_spread_bps=1.0,
            market_impact_bps=2.0,
            borrow_bps_annual=50.0,
            max_participation_rate=0.05,
        ),
    )
    assert trial.declared_attempts == 1


def test_observed_graph_edge_requires_source() -> None:
    with pytest.raises(ValidationError, match="require a source"):
        EdgeEvidence(
            edge_id="edge.1",
            source_node="bank:1",
            target_node="security:1",
            relation="reported_holding",
            evidence_class=EvidenceClass.REPORTED,
            confidence=1.0,
        )

    edge = EdgeEvidence(
        edge_id="edge.2",
        source_node="bank:1",
        target_node="security:1",
        relation="reported_holding",
        evidence_class=EvidenceClass.REPORTED,
        confidence=1.0,
        source=source(),
    )
    assert edge.source is not None


def test_contracts_forbid_unknown_fields() -> None:
    values = source().model_dump()
    values["unsupported_claim"] = "production proven"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SourceReference.model_validate(values)


def test_source_temporal_coverage_cannot_overclaim_vintage() -> None:
    latest_values = source().model_dump()
    latest_values.update(
        temporal_coverage=TemporalCoverage.LATEST_ONLY,
        vintage_as_of=NOW,
    )
    with pytest.raises(ValidationError, match="cannot claim"):
        SourceReference.model_validate(latest_values)

    versioned_values = source().model_dump()
    versioned_values["vintage_as_of"] = None
    with pytest.raises(ValidationError, match="require vintage_as_of"):
        SourceReference.model_validate(versioned_values)


def test_record_scenario_and_replay_manifest_round_trip() -> None:
    interval = BitemporalInterval(
        valid_from=NOW - timedelta(days=100),
        published_at=NOW - timedelta(days=10),
        available_at=NOW - timedelta(days=9),
        ingested_at=NOW,
        availability_rule="Official timestamp plus conservative delay.",
        availability_confidence=0.9,
    )
    record = BitemporalRecord(
        record_id="fdic:24735:2022-12-31:asset",
        entity_id="fdic_cert:24735",
        source=source(),
        interval=interval,
        evidence_class=EvidenceClass.REPORTED,
        payload_schema_version="1.0.0",
        payload={"metric": "ASSET", "value": 211_793_000},
    )
    scenario = ScenarioSpec(
        scenario_id="svb-2023-point-in-time",
        title="SVB 2023 point-in-time reconstruction",
        version="0.1.0",
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        event_start=NOW - timedelta(days=600),
        event_end=NOW - timedelta(days=500),
        decision_time=NOW - timedelta(days=510),
        source_ids=(source().source_id,),
        observed_inputs=(record.record_id,),
        bounded_inputs=("deposit-flight-speed",),
        simulated_inputs=("counterfactual-hedge-ratio",),
        limitations=("Quarterly bank data cannot reconstruct intraday deposit flight.",),
    )
    manifest = ReplayPackManifest(
        replay_id="svb-2023-point-in-time.0.1.0.demo",
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.version,
        created_at=NOW,
        code_commit="uncommitted",
        input_manifest_sha256="1" * 64,
        output_manifest_sha256="2" * 64,
        distinct_input_records=1,
        derived_records=2,
        compressed_input_bytes=128,
        elapsed_seconds=0.01,
        status=ArtifactStatus.FIXTURE_VALIDATED,
    )
    assert BitemporalRecord.model_validate_json(record.model_dump_json()) == record
    assert ScenarioSpec.model_validate_json(scenario.model_dump_json()) == scenario
    assert ReplayPackManifest.model_validate_json(manifest.model_dump_json()) == manifest


def test_scenario_rejects_bad_timeline_and_mixed_evidence_labels() -> None:
    common: dict[str, Any] = {
        "scenario_id": "invalid-scenario",
        "title": "Invalid scenario fixture",
        "version": "0.1.0",
        "mode": ScenarioMode.BOUNDED_RECONSTRUCTION,
        "decision_time": NOW,
        "source_ids": ("official.test.fixture",),
        "observed_inputs": ("same-input",),
        "bounded_inputs": (),
        "simulated_inputs": ("same-input",),
        "limitations": ("Intentional invalid fixture.",),
    }
    with pytest.raises(ValidationError, match="event_end"):
        ScenarioSpec(event_start=NOW, event_end=NOW - timedelta(days=1), **common)

    with pytest.raises(ValidationError, match="both observed and simulated"):
        ScenarioSpec(event_start=NOW, event_end=NOW + timedelta(days=1), **common)
