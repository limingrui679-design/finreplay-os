"""March 2020 joint Census/BEA FT-900 trade-deficit level boundary."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from finreplay.contracts import (
    ArtifactStatus,
    BitemporalRecord,
    CostModel,
    EvidenceClass,
    LicenseClass,
    ScenarioMode,
    TemporalCoverage,
    TrialDisposition,
    TrialSpec,
)
from finreplay.engines import (
    EngineName,
    ReplayArtifact,
    ReplayClaim,
    ReplayPackSpec,
    ShockCompiler,
    ShockOperation,
    ShockParameter,
    ShockProgram,
    TimeVault,
    TrialAttempt,
    TrialCourt,
)

CENSUS_BEA_FT900_SOURCE_ID = "census.bea.ft900.archived_trade_balance"
_ENTITY_ID = "census_bea_ft900:us_goods_services_deficit"
_DECISION_TIME = datetime(2020, 4, 2, 12, 30, tzinfo=UTC)
_JANUARY_PDF_SHA256 = "b1cfa18560bc0bbb4c325d5b49bdba078407d6d247197ce1edc2d6ae30be61bf"
_JANUARY_ZIP_SHA256 = "e64a8fb9028b84789ae930db99aa67e3fb0918da7e729349f7b0907bf62193f7"
_FEBRUARY_PDF_SHA256 = "5c32f19b5b556d479de8a7cd228bda3348e5b1ceec8dfd9d327d6a783847bb7c"
_FEBRUARY_ZIP_SHA256 = "7527ba2aab574733774950ac68480d95d6f4286ddc630fca8198844503941e98"
_AVAILABILITY_RULE = (
    "Each selected joint Census/BEA FT-900 PDF states an exact 8:30 a.m. EST/EDT "
    "release date and time. FinReplay validates that label against America/New_York "
    "and makes the paired PDF/XLS ZIP semantic snapshot eligible at that instant. "
    "Current archive bytes and HTTP headers are present-retrieval evidence and are "
    "not backdated."
)
_REDISTRIBUTION_NOTE = (
    "Full Census/BEA PDFs and XLS ZIPs remain in local content-addressed storage. The "
    "repository retains only minimal reported facts, URLs, hashes, attribution, and "
    "release-snapshot semantics; no redistribution right is inferred."
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TradeDeficitLevelBoundaryRoles(_StrictModel):
    """Release lineage and the complete decision-time snapshot."""

    january_release_snapshot: str = Field(min_length=1, max_length=300)
    february_decision_snapshot: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> TradeDeficitLevelBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("Census/BEA FT-900 role record IDs must be unique")
        return self


class TradeDeficitLevelBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision joint Census/BEA FT-900 facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: TradeDeficitLevelBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=4, max_length=4)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> TradeDeficitLevelBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.decision_time != _DECISION_TIME:
            raise ValueError("FT-900 decision_time must equal the February-data release")
        if self.build_epoch < self.decision_time:
            raise ValueError("FT-900 build_epoch cannot precede decision_time")

        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("FT-900 records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("FT-900 roles must cover every locked record exactly once")

        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("FT-900 source hashes must be unique and sorted")
        expected_hashes = {
            _JANUARY_PDF_SHA256,
            _JANUARY_ZIP_SHA256,
            _FEBRUARY_PDF_SHA256,
            _FEBRUARY_ZIP_SHA256,
        }
        if set(self.source_response_sha256s) != expected_hashes:
            raise ValueError("FT-900 source hash set does not match the two paired releases")
        if {record.source.sha256 for record in self.records} != {
            _JANUARY_PDF_SHA256,
            _FEBRUARY_PDF_SHA256,
        }:
            raise ValueError("FT-900 PDF hashes do not match locked records")
        paired_hashes = {
            str(record.payload.get(field))
            for record in self.records
            for field in ("release_pdf_sha256", "release_xls_zip_sha256")
        }
        if paired_hashes != expected_hashes:
            raise ValueError("FT-900 paired PDF/XLS ZIP hashes do not match locked records")

        by_id = {record.record_id: record for record in self.records}
        for role, values in _expected_records().items():
            record = by_id[getattr(self.roles, role)]
            published_at = values["published_at"]
            assert isinstance(published_at, datetime)
            if record.source.source_id != CENSUS_BEA_FT900_SOURCE_ID:
                raise ValueError("FT-900 lock accepts only archived joint trade facts")
            if record.source.publisher != (
                "U.S. Census Bureau and U.S. Bureau of Economic Analysis"
            ):
                raise ValueError(f"FT-900 {role} source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("FT-900 inputs must use versioned release snapshots")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("FT-900 source license boundary mismatch")
            if record.source.redistribution_note != _REDISTRIBUTION_NOTE:
                raise ValueError("FT-900 redistribution boundary mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("FT-900 deficit levels must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"FT-900 {role} entity mismatch")
            if record.payload_schema_version != "1.0.0":
                raise ValueError(f"FT-900 {role} payload schema mismatch")
            if record.interval.availability_confidence != 1.0:
                raise ValueError("FT-900 timing must be deterministic")
            if record.interval.availability_rule != _AVAILABILITY_RULE:
                raise ValueError("FT-900 availability rule mismatch")
            if record.interval.published_at != published_at:
                raise ValueError(f"FT-900 {role} publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"FT-900 {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("FT-900 lock contains a post-decision input")
            if record.interval.revised_at is not None:
                raise ValueError("FT-900 inputs must be initial monthly-release records")
            if record.interval.valid_to is not None:
                raise ValueError("FT-900 monthly facts must have open valid-time intervals")
            expected_valid_from = datetime.fromisoformat(
                f"{values['reference_month']}-01T00:00:00+00:00"
            )
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"FT-900 {role} valid time mismatch")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"FT-900 {role} source vintage mismatch")
            if record.source.retrieved_at != record.interval.ingested_at:
                raise ValueError("FT-900 retrieval and ingestion times must agree")
            if record.source.retrieved_at < published_at:
                raise ValueError("FT-900 retrieval cannot precede official release")
            if record.source.retrieved_at > self.build_epoch:
                raise ValueError("FT-900 retrieval cannot occur after build_epoch")
            if record.source.sha256 != values["pdf_sha256"]:
                raise ValueError(f"FT-900 {role} PDF hash mismatch")
            if str(record.source.url) != values["pdf_url"]:
                raise ValueError(f"FT-900 {role} source URL mismatch")
            if record.source.source_version != values["source_version"]:
                raise ValueError(f"FT-900 {role} source version mismatch")
            for field, expected_value in values["critical_payload"].items():
                if record.payload.get(field) != expected_value:
                    raise ValueError(f"FT-900 {role} {field} mismatch")
            if _hash(record.payload) != values["payload_sha256"]:
                raise ValueError(f"FT-900 {role} payload hash mismatch")
            if _deficit_level(record) != values["value_million_dollars"]:
                raise ValueError(f"FT-900 {role} deficit level mismatch")

        decision_record = by_id[self.roles.february_decision_snapshot]
        if decision_record.payload["release_snapshot_deficit_million_dollars"] != {
            "2020-01": 45_482,
            "2020-02": 39_932,
        }:
            raise ValueError("FT-900 decision snapshot must retain revised January and February")
        if (
            decision_record.payload["prior_month_revised_deficit_million_dollars"]
            - decision_record.payload["value_million_dollars"]
            != 5_550
        ):
            raise ValueError("FT-900 decision-snapshot decline must equal 5,550 million dollars")

        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match FT-900 input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> TradeDeficitLevelBoundaryInputLock:
        """Normalize, validate, and self-hash an FT-900 input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_trade_deficit_level_boundary_input_lock(
    path: Path,
) -> TradeDeficitLevelBoundaryInputLock:
    try:
        return TradeDeficitLevelBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid Census/BEA FT-900 input lock: {path}") from error


def build_trade_deficit_level_boundary_replay_spec(
    lock: TradeDeficitLevelBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 FT-900 deficit boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = lock.source_response_sha256s
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                source_ids=[CENSUS_BEA_FT900_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    selected_by_id = {record.record_id: record for record in selected}
    if set(selected_by_id) != set(record_ids):
        raise ValueError("TimeVault did not reproduce the FT-900 record IDs")
    for record in records:
        if selected_by_id[record.record_id].model_dump(mode="json") != record.model_dump(
            mode="json"
        ):
            raise ValueError("TimeVault changed an FT-900 locked fact")

    prefix = lock.artifact_prefix
    decision_record = by_role["february_decision_snapshot"]
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-census-bea-ft900-release-query",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.REPORTED: len(records)},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "append": asdict(append_receipt),
            "manifest": {
                **asdict(manifest),
                "generated_at": _canonical_datetime(manifest.generated_at),
            },
            "selected_record_ids": list(record_ids),
            "max_available_at": _canonical_datetime(
                max(record.interval.available_at for record in records)
            ),
            "decision_time": _canonical_datetime(lock.decision_time),
            "value_semantics": (
                "seasonally adjusted U.S. goods-and-services deficit levels in million "
                "dollars; the boundary uses revised January and initial February values "
                "from the single February-data release snapshot"
            ),
            "decision_snapshot": {
                "revised_january_deficit_million_dollars": decision_record.payload[
                    "prior_month_revised_deficit_million_dollars"
                ],
                "initial_february_deficit_million_dollars": decision_record.payload[
                    "value_million_dollars"
                ],
            },
            "january_initial_release_retained_for_revision_lineage": True,
            "release_time_rule": "08:30 America/New_York from each dated FT-900 PDF",
            "paired_pdf_xls_crosscheck_verified": True,
            "source_evidence_file_count": len(source_hashes),
            "current_archive_byte_identity_at_release_claimed": False,
        },
        limitations=(
            "The boundary uses only the decision-time revised January and initial February "
            "levels from the April 2 release snapshot.",
            "The March deficit and May 5 revision snapshot are excluded from every input.",
            "The current archive bytes prove present retrieval, not byte identity at release.",
            "The figures are seasonally adjusted nominal dollars, not price-adjusted volume.",
            "Goods document coverage does not eliminate nonsampling error or service-estimation "
            "limitations.",
            "Full official PDF and XLS ZIP responses remain local download evidence.",
        ),
    )
    shock_artifact, metrics = _run_shockcompiler(
        lock=lock,
        records_by_role=by_role,
        source_hashes=source_hashes,
        upstream=timevault_artifact.artifact_id,
    )
    trial_artifact = _run_trialcourt(
        lock=lock,
        records_by_role=by_role,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        range_width=metrics["range_width_million_dollars"],
        upstream=(timevault_artifact.artifact_id, shock_artifact.artifact_id),
        code_commit=code_commit,
    )
    replaystudio_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.replaystudio.render",
        engine=EngineName.REPLAYSTUDIO,
        artifact_kind="static-report-contract",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.EXTRACTED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=tuple(
            sorted(
                (
                    shock_artifact.artifact_id,
                    timevault_artifact.artifact_id,
                    trial_artifact.artifact_id,
                )
            )
        ),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "renderer": "ReplayStudio",
            "portable_file_count_excluding_manifest": 5,
            "truth_labels_visible": True,
            "engine_selection": (
                "This FT-900 aggregate release boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no trade flow, shipment, customs entry, firm, "
                "position, portfolio, or allocation input is invented."
            ),
        },
        limitations=("Static rendering does not validate the one-decline range heuristic.",),
    )
    artifacts = (
        timevault_artifact,
        shock_artifact,
        trial_artifact,
        replaystudio_artifact,
    )
    return ReplayPackSpec(
        replay_id=lock.replay_id,
        scenario_id=lock.scenario_id,
        scenario_version=lock.scenario_version,
        title=lock.title,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        created_at=lock.build_epoch,
        code_commit=code_commit,
        status=ArtifactStatus.REPRODUCED,
        artifacts=artifacts,
        claims=_claims(artifacts, metrics),
        require_all_engines=False,
        distinct_input_records=len(records),
        derived_records=(
            int(trial_artifact.payload["manifest"]["entries"])
            + len(shock_artifact.payload["compiled"]["trials"])
            + 1
        ),
        compressed_input_bytes=len(
            gzip.compress(_canonical_json(lock.model_dump(mode="json")).encode(), mtime=0)
        ),
        elapsed_seconds=0.0,
        claim_boundary=(
            "Four actual engines ran over paired official PDF/XLS evidence for the January "
            "and February 2020 joint Census/BEA FT-900 releases. Range construction uses "
            "only the 45,482-million-dollar revised January and 39,932-million-dollar initial "
            "February deficit values co-published in the April 2 decision snapshot; the "
            "45,338 January initial release is retained only for revision lineage. Reported "
            "facts remain reported, while the 34,382-to-39,932 continuation envelope remains "
            "inferred with no probability. The May 5 March event and its revisions stay only "
            "in a disjoint event lock. This is not an official forecast, confidence interval, "
            "calibrated interval, causal or price-adjusted model, trade-policy conclusion, "
            "COVID effect, trading signal, deployment, external validation, or user-impact claim."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are February-level persistence or one repetition of the single "
            "5,550-million-dollar decline visible inside the April 2 release snapshot.",
            "The January initial-release level does not numerically set either endpoint.",
            "The May 5 release and every later-known value are excluded from every input.",
            "No official confidence interval, probability, or statistical-significance result "
            "is available for this headline level construction.",
            "The series is seasonally adjusted but not adjusted for price changes.",
            "No firm, shipment, customs entry, transaction, return, or recommendation exists.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: TradeDeficitLevelBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    january_initial = _deficit_level(records_by_role["january_release_snapshot"])
    decision_record = records_by_role["february_decision_snapshot"]
    revised_january = decision_record.payload["prior_month_revised_deficit_million_dollars"]
    february_initial = _deficit_level(decision_record)
    if not isinstance(revised_january, int) or isinstance(revised_january, bool):
        raise ValueError("FT-900 revised January level must be integer million dollars")
    known_decline = revised_january - february_initial
    if known_decline != 5_550:
        raise ValueError("FT-900 decision snapshot must establish the verified 5,550 decline")
    lower = february_initial - known_decline
    upper = february_initial
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    variable = "next_goods_services_deficit_level_million_dollars"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-trade-deficit-level-range",
        target_id=_ENTITY_ID,
        variable=variable,
        unit="million_us_dollars_seasonally_adjusted_deficit",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use February initial-level persistence or one repetition of the only known "
            "decline between revised January and initial February values inside the same "
            "April 2 FT-900 release snapshot."
        ),
        limitations=(
            "The January initial-release value is revision lineage and does not set the decline.",
            "One decision-snapshot decline defines a stress range, not a forecast, probability, "
            "confidence interval, or calibrated predictive interval.",
            "The May 5 March event and its revisions are absent from range construction.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-trade-deficit-level-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate February deficit-level persistence or one repetition of the known "
            "decision-snapshot decline using only facts available at the boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, price, or regime meaning.",
            "The May 5 release and all its revisions are evaluated only afterward.",
            "The aggregate release does not represent every firm, shipment, or transaction.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, variable)
    initial_state = {state_key: float(february_initial)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "january_initial_release_deficit_million_dollars": january_initial,
        "decision_snapshot_revised_january_deficit_million_dollars": revised_january,
        "january_revision_delta_known_at_decision_million_dollars": (
            revised_january - january_initial
        ),
        "february_initial_deficit_million_dollars": february_initial,
        "known_decision_snapshot_decline_million_dollars": known_decline,
        "lower_level_million_dollars": lower,
        "upper_level_million_dollars": upper,
        "range_width_million_dollars": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.trade-deficit-level-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-census-bea-ft900-trade-deficit-level-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_decision_snapshot_levels": metrics,
            "naive_baseline": {
                variable: february_initial,
                "definition": "persistence of the February initial FT-900 deficit level",
            },
            "bound_construction": {
                "lower_level_million_dollars": lower,
                "upper_level_million_dollars": upper,
                "range_width_million_dollars": width,
                "known_decision_snapshot_decline_million_dollars": known_decline,
                "endpoint_method": (
                    "latest_initial_level_persistence_or_repeat_same_release_snapshot_decline"
                ),
                "basis_is_single_february_release_snapshot": True,
                "january_initial_release_used_as_numeric_endpoint_input": False,
                "official_confidence_interval_used": False,
                "probability_assigned": False,
                "future_event_used": False,
            },
            "program": program.model_dump(mode="json"),
            "compiled": compiled.model_dump(mode="json"),
            "applied_endpoints": {
                "lower": lower_state[state_key],
                "upper": upper_state[state_key],
            },
        },
        limitations=(
            "The endpoints mechanically reuse one decline in one decision-time release snapshot.",
            "The May 5 March value and revised February value are absent.",
            "The range is not an official interval, probability, causal model, or forecast.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: TradeDeficitLevelBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    range_width: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=45)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-range-screen",
        hypothesis=(
            "A retrospectively constructed one-decline FT-900 deficit boundary qualifies "
            "for research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "The joint release is an aggregate statistical product combining goods records "
            "and estimated services. One decision-time decline and one later outcome cannot "
            "establish predictive validity or trade, price, pandemic, policy, firm, sector, "
            "or macroeconomic causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-decision-snapshot FT-900 range width in million dollars",
        expected_direction="positive",
        cost_model=_cost_model(),
        disposition=TrialDisposition.REVISE,
    )
    attempt = TrialAttempt(
        attempt_id=f"{lock.artifact_prefix}-retrospective-attempt-1",
        trial_id=spec.trial_id,
        attempt_number=1,
        completed_at=lock.build_epoch,
        code_commit=code_commit,
        config_sha256=_hash(spec.model_dump(mode="json")),
        input_manifest_sha256=lock.lock_sha256,
        output_manifest_sha256=_hash({"trade_deficit_range_width_million_dollars": range_width}),
        decision_time=lock.decision_time,
        max_input_available_at=max(
            record.interval.available_at for record in records_by_role.values()
        ),
        training_end=lock.decision_time.date() - timedelta(days=2),
        evaluation_start=holdout_start,
        evaluation_end=holdout_end,
        sample_size=len(records_by_role),
        metric_value=float(range_width),
        p_value=1.0,
        gross_return_bps=0.0,
        one_way_turnover=0.0,
        short_fraction=0.0,
        requested_capital_usd=1.0,
        median_daily_volume_usd=1.0,
        regime_metric_values={
            "known-decision-snapshot-decline": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No May 5 FT-900 fact or revision is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective FT-900 attempt must fail closed")
    return ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.trialcourt.retrospective-gate",
        engine=EngineName.TRIALCOURT,
        artifact_kind="adversarial-decision",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={
            EvidenceClass.REPORTED: len(source_record_ids),
            EvidenceClass.INFERRED: 1,
            EvidenceClass.SIMULATED: 1,
        },
        source_set_historical_replay_eligible=True,
        source_record_ids=source_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=tuple(sorted(upstream)),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "spec": spec.model_dump(mode="json"),
            "attempt": attempt.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "registration_receipt": asdict(registration_receipt),
            "attempt_receipt": asdict(attempt_receipt),
            "manifest": asdict(manifest),
        },
        limitations=(
            "The rejected attempt is a retrospective method boundary, not a validated forecast.",
            "Operational sentinel fields contain no execution, capacity, or return evidence.",
        ),
    )


def _claims(
    artifacts: tuple[ReplayArtifact, ...],
    metrics: dict[str, int],
) -> tuple[ReplayClaim, ...]:
    by_engine = {artifact.engine: artifact for artifact in artifacts}
    return (
        ReplayClaim(
            claim_id="claim-reported-census-bea-ft900-decision-levels",
            statement=(
                "The April 2 joint Census/BEA FT-900 release reports a revised January "
                "deficit of "
                f"{metrics['decision_snapshot_revised_january_deficit_million_dollars']:,} "
                "million dollars and an initial February deficit of "
                f"{metrics['february_initial_deficit_million_dollars']:,} million dollars."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "The January release's 45,338-million-dollar initial value is retained for "
                "revision lineage, not substituted for the decision-time revised value."
            ),
            limitations=(
                "These are seasonally adjusted nominal aggregate release facts, not real trade "
                "volume, firm records, shipments, customs entries, or transactions.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-census-bea-ft900-deficit-level-range",
            statement=(
                "The next-release stress endpoints are February-level persistence or one "
                "repeat of the known "
                f"{metrics['known_decision_snapshot_decline_million_dollars']:,}-"
                "million-dollar decision-snapshot decline: "
                f"[{metrics['lower_level_million_dollars']:,}, "
                f"{metrics['upper_level_million_dollars']:,}] million dollars."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability, coverage, official, or causal guarantee.",
            limitations=(
                "The May 5 event and every revision in that release were not used to set "
                "the range.",
                "The range has no trade, price, pandemic, policy, or sector causality.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-census-bea-ft900-trial-rejection",
            statement="TrialCourt rejected the retrospective one-decline FT-900 attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external or domain review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-census-bea-ft900-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not transactions, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-census-bea-ft900-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _expected_records() -> dict[str, dict[str, Any]]:
    common = {
        "release_series": "Monthly U.S. International Trade in Goods and Services",
        "metric": "goods_services_deficit_level_million_dollars",
        "release_time_local": "08:30:00",
        "release_timezone": "America/New_York",
        "seasonally_adjusted": True,
        "adjusted_for_price_changes": False,
        "goods_data_complete_enumeration_of_cbp_documents": True,
        "goods_data_subject_to_sampling_error": False,
        "headline_statistical_significance_applicable_or_measurable": False,
        "nonsampling_errors_possible": True,
        "monthly_and_annual_revisions_documented": True,
        "pdf_table_snapshot_verified": True,
        "xls_exhibit1_snapshot_verified": True,
        "pdf_xls_crosscheck_verified": True,
        "current_archive_byte_identity_at_release_claimed": False,
        "release_xls_zip_member_count": 31,
        "release_xls_exhibit1_rows": 55,
        "release_xls_exhibit1_columns": 10,
        "availability_method": "exact_time_in_pdf_values_crosschecked_to_xls_zip",
        "unit": "Million U.S. Dollars of Seasonally Adjusted Deficit",
        "snapshot_semantics": (
            "reported goods-and-services deficit fact in this archived joint release"
        ),
    }
    january_payload = {
        **common,
        "reference_month": "2020-01",
        "release_date": "2020-03-06",
        "census_release_number": "CB 20-34",
        "bea_release_number": "BEA 20-09",
        "official_release_at": "2020-03-06T13:30:00+00:00",
        "release_timezone_abbreviation": "EST",
        "value_million_dollars": 45_338,
        "signed_balance_million_dollars": -45_338,
        "prior_month": "2019-12",
        "prior_month_previous_release_deficit_million_dollars": 48_880,
        "prior_month_revised_deficit_million_dollars": 48_613,
        "prior_month_revision_delta_million_dollars": -267,
        "release_snapshot_deficit_million_dollars": {"2020-01": 45_338},
        "release_snapshot_previous_deficit_million_dollars": {"2020-01": None},
        "release_snapshot_revision_delta_million_dollars": {"2020-01": None},
        "reported_headline_deficit_billion_dollars": "45.3",
        "reported_headline_delta_billion_dollars": "3.3",
        "reported_headline_direction": "down",
        "covid_publication_standard_statement_present": False,
        "release_pdf_pages": 63,
        "release_pdf_url": (
            "https://www.census.gov/foreign-trade/Press-Release/ft900/ft900_2001.pdf"
        ),
        "release_pdf_sha256": _JANUARY_PDF_SHA256,
        "release_xls_zip_url": (
            "https://www.census.gov/foreign-trade/Press-Release/ft900/ft900xls_2001.zip"
        ),
        "release_xls_zip_sha256": _JANUARY_ZIP_SHA256,
    }
    february_payload = {
        **common,
        "reference_month": "2020-02",
        "release_date": "2020-04-02",
        "census_release_number": "CB 20-52",
        "bea_release_number": "BEA 20-16",
        "official_release_at": "2020-04-02T12:30:00+00:00",
        "release_timezone_abbreviation": "EDT",
        "value_million_dollars": 39_932,
        "signed_balance_million_dollars": -39_932,
        "prior_month": "2020-01",
        "prior_month_previous_release_deficit_million_dollars": 45_338,
        "prior_month_revised_deficit_million_dollars": 45_482,
        "prior_month_revision_delta_million_dollars": 144,
        "release_snapshot_deficit_million_dollars": {
            "2020-01": 45_482,
            "2020-02": 39_932,
        },
        "release_snapshot_previous_deficit_million_dollars": {
            "2020-01": 45_338,
            "2020-02": None,
        },
        "release_snapshot_revision_delta_million_dollars": {
            "2020-01": 144,
            "2020-02": None,
        },
        "reported_headline_deficit_billion_dollars": "39.9",
        "reported_headline_delta_billion_dollars": "5.5",
        "reported_headline_direction": "down",
        "covid_publication_standard_statement_present": False,
        "release_pdf_pages": 63,
        "release_pdf_url": (
            "https://www.census.gov/foreign-trade/Press-Release/ft900/ft900_2002.pdf"
        ),
        "release_pdf_sha256": _FEBRUARY_PDF_SHA256,
        "release_xls_zip_url": (
            "https://www.census.gov/foreign-trade/Press-Release/ft900/ft900xls_2002.zip"
        ),
        "release_xls_zip_sha256": _FEBRUARY_ZIP_SHA256,
    }
    return {
        "january_release_snapshot": {
            "reference_month": "2020-01",
            "published_at": datetime(2020, 3, 6, 13, 30, tzinfo=UTC),
            "pdf_sha256": _JANUARY_PDF_SHA256,
            "pdf_url": january_payload["release_pdf_url"],
            "source_version": (
                "CENSUS-BEA-FT900:2020-01:CB20-34:pdf:b1cfa18560bc0bbb4c32:"
                "xlszip:e64a8fb9028b84789ae9"
            ),
            "payload_sha256": "c5155788beb8043a52bcd4d4389e28379b3a06ece9a922800ed10bcdf5d1c86e",
            "value_million_dollars": 45_338,
            "critical_payload": january_payload,
        },
        "february_decision_snapshot": {
            "reference_month": "2020-02",
            "published_at": _DECISION_TIME,
            "pdf_sha256": _FEBRUARY_PDF_SHA256,
            "pdf_url": february_payload["release_pdf_url"],
            "source_version": (
                "CENSUS-BEA-FT900:2020-02:CB20-52:pdf:5c32f19b5b556d479de8:"
                "xlszip:7527ba2aab5747337749"
            ),
            "payload_sha256": "fa2c546e6936688f4a0ff00376183588dfbb09a35c2aae72afc8421f7840572b",
            "value_million_dollars": 39_932,
            "critical_payload": february_payload,
        },
    }


def _records_by_role(
    lock: TradeDeficitLevelBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _deficit_level(record: BitemporalRecord) -> int:
    value = record.payload["value_million_dollars"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("FT-900 deficit level must be integer million dollars")
    if not 1 <= value <= 1_000_000:
        raise ValueError("FT-900 deficit level is outside the supported range")
    return value


def _cost_model() -> CostModel:
    return CostModel(
        commission_bps=1.0,
        half_spread_bps=1.0,
        market_impact_bps=1.0,
        borrow_bps_annual=0.0,
        max_participation_rate=0.05,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
