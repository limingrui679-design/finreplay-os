"""Strict domain contracts for point-in-time financial research."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from finreplay.contracts.types import (
    ArtifactStatus,
    EvidenceClass,
    LicenseClass,
    ScenarioMode,
    TemporalCoverage,
    TrialDisposition,
)


class StrictModel(BaseModel):
    """Forbid silent schema drift in every persisted contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceReference(StrictModel):
    """Stable provenance pointer for one upstream artifact or response."""

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,79}$")
    publisher: str = Field(min_length=2, max_length=200)
    url: HttpUrl
    retrieved_at: datetime
    source_version: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    license_class: LicenseClass
    temporal_coverage: TemporalCoverage
    vintage_as_of: datetime | None = None
    redistribution_note: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_aware_retrieval_time(self) -> SourceReference:
        _require_aware(self.retrieved_at, "retrieved_at")
        if self.vintage_as_of is not None:
            _require_aware(self.vintage_as_of, "vintage_as_of")
        if (
            self.temporal_coverage is TemporalCoverage.LATEST_ONLY
            and self.vintage_as_of is not None
        ):
            raise ValueError("latest_only sources cannot claim a historical vintage_as_of")
        if (
            self.temporal_coverage is not TemporalCoverage.LATEST_ONLY
            and self.vintage_as_of is None
        ):
            raise ValueError("vintage-aware sources require vintage_as_of")
        return self


class BitemporalInterval(StrictModel):
    """Separate economic validity from historical knowledge availability."""

    valid_from: datetime
    valid_to: datetime | None = None
    published_at: datetime
    available_at: datetime
    revised_at: datetime | None = None
    ingested_at: datetime
    availability_rule: str = Field(min_length=3, max_length=500)
    availability_confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_clocks(self) -> BitemporalInterval:
        clock_fields = (
            "valid_from",
            "valid_to",
            "published_at",
            "available_at",
            "revised_at",
            "ingested_at",
        )
        for name in clock_fields:
            value = getattr(self, name)
            if value is not None:
                _require_aware(value, name)
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not precede valid_from")
        if self.available_at < self.published_at:
            raise ValueError("available_at must not precede published_at")
        if self.revised_at is not None and self.revised_at < self.published_at:
            raise ValueError("revised_at must not precede published_at")
        if self.ingested_at < self.available_at:
            raise ValueError("ingested_at must not precede historical availability")
        return self


class BitemporalRecord(StrictModel):
    """One immutable financial fact with evidence grade and both clocks."""

    record_id: str = Field(min_length=1, max_length=300)
    entity_id: str | None = Field(default=None, max_length=300)
    source: SourceReference
    interval: BitemporalInterval
    evidence_class: EvidenceClass
    payload_schema_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    payload: dict[str, Any]


class CostModel(StrictModel):
    """Non-zero execution assumptions required for any strategy claim."""

    commission_bps: float = Field(ge=0.0)
    half_spread_bps: float = Field(ge=0.0)
    market_impact_bps: float = Field(ge=0.0)
    borrow_bps_annual: float = Field(ge=0.0)
    max_participation_rate: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def reject_frictionless_claims(self) -> CostModel:
        if self.commission_bps + self.half_spread_bps + self.market_impact_bps <= 0:
            raise ValueError("at least one immediate trading friction must be positive")
        return self


class TrialSpec(StrictModel):
    """Preregistered research claim and its immutable evaluation boundaries."""

    trial_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    hypothesis: str = Field(min_length=20, max_length=2000)
    economic_mechanism: str = Field(min_length=20, max_length=4000)
    preregistered_at: datetime
    holdout_start: date
    holdout_end: date
    purge_days: int = Field(ge=0, le=3650)
    embargo_days: int = Field(ge=0, le=3650)
    declared_attempts: int = Field(ge=1)
    primary_metric: str = Field(min_length=2, max_length=200)
    expected_direction: str = Field(pattern=r"^(positive|negative|two-sided|non-inferior)$")
    cost_model: CostModel
    disposition: TrialDisposition = TrialDisposition.REVISE

    @model_validator(mode="after")
    def validate_holdout(self) -> TrialSpec:
        _require_aware(self.preregistered_at, "preregistered_at")
        if self.holdout_end <= self.holdout_start:
            raise ValueError("holdout_end must be after holdout_start")
        return self


class EdgeEvidence(StrictModel):
    """Evidence-graded relationship in the financial-system graph."""

    edge_id: str = Field(min_length=3, max_length=300)
    source_node: str = Field(min_length=1, max_length=300)
    target_node: str = Field(min_length=1, max_length=300)
    relation: str = Field(min_length=2, max_length=200)
    evidence_class: EvidenceClass
    confidence: float = Field(ge=0.0, le=1.0)
    source: SourceReference | None = None

    @model_validator(mode="after")
    def observed_edges_need_source(self) -> EdgeEvidence:
        sourced_classes = {EvidenceClass.OBSERVED, EvidenceClass.REPORTED}
        if self.evidence_class in sourced_classes and self.source is None:
            raise ValueError("observed and reported edges require a source reference")
        return self


class ScenarioSpec(StrictModel):
    """Versioned historical or boundary replay definition."""

    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    title: str = Field(min_length=5, max_length=300)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    mode: ScenarioMode
    event_start: datetime
    event_end: datetime
    decision_time: datetime
    source_ids: tuple[str, ...] = Field(min_length=1)
    observed_inputs: tuple[str, ...]
    bounded_inputs: tuple[str, ...]
    simulated_inputs: tuple[str, ...]
    limitations: tuple[str, ...] = Field(min_length=1)
    status: ArtifactStatus = ArtifactStatus.PLANNED

    @model_validator(mode="after")
    def validate_timeline_and_labels(self) -> ScenarioSpec:
        for name in ("event_start", "event_end", "decision_time"):
            _require_aware(getattr(self, name), name)
        if self.event_end <= self.event_start:
            raise ValueError("event_end must be after event_start")
        if set(self.observed_inputs) & set(self.simulated_inputs):
            raise ValueError("an input cannot be both observed and simulated")
        return self


class ReplayPackManifest(StrictModel):
    """Measured, content-addressed output of one reproducible replay."""

    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    scenario_id: str
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    created_at: datetime
    code_commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|uncommitted)$")
    input_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    distinct_input_records: int = Field(ge=0)
    derived_records: int = Field(ge=0)
    compressed_input_bytes: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0.0)
    status: ArtifactStatus

    @model_validator(mode="after")
    def validate_created_at(self) -> ReplayPackManifest:
        _require_aware(self.created_at, "created_at")
        return self


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    # Normalize-awareness check without mutating frozen models.
    value.astimezone(UTC)
