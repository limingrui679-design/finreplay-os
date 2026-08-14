"""March 2020 Census/HUD new-home-sales level boundary."""

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

CENSUS_NRS_SOURCE_ID = "census.hud.archived_new_residential_sales"
_ENTITY_ID = "census_hud_nrs:new_single_family_houses_sold_us"
_DECISION_TIME = datetime(2020, 3, 24, 14, 0, tzinfo=UTC)
_JANUARY_PDF_SHA256 = "ba86558efb14745ddf6c56684c9023444397941a0c49bed406e1d6eda6dcca3b"
_FEBRUARY_PDF_SHA256 = "9a47e1fd70c0830394a9681ec0bc1881e1d0522c105ff9aeff60dd01c98c3fb8"
_AVAILABILITY_RULE = (
    "Each selected Census/HUD New Residential Sales PDF states an exact 10:00 a.m. "
    "EST/EDT release date and time. FinReplay validates the timezone abbreviation against "
    "America/New_York and makes the release snapshot eligible at that exact stated time. "
    "Current HTTP headers are retrieval metadata only and are not backdated."
)
_REDISTRIBUTION_NOTE = (
    "Full Census/HUD PDFs remain in local content-addressed storage. The repository retains "
    "only minimal reported facts, URLs, hashes, attribution, and release-snapshot semantics; "
    "no redistribution right is inferred."
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NewHomeSalesLevelBoundaryRoles(_StrictModel):
    """Release lineage and the complete decision-time snapshot."""

    january_release_snapshot: str = Field(min_length=1, max_length=300)
    february_decision_snapshot: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> NewHomeSalesLevelBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("Census/HUD NRS role record IDs must be unique")
        return self


class NewHomeSalesLevelBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision Census/HUD NRS facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: NewHomeSalesLevelBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> NewHomeSalesLevelBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.decision_time != _DECISION_TIME:
            raise ValueError("NRS decision_time must equal the February-data release")
        if self.build_epoch < self.decision_time:
            raise ValueError("NRS build_epoch cannot precede decision_time")

        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("NRS records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("NRS roles must cover every locked record exactly once")

        expected_hashes = {_JANUARY_PDF_SHA256, _FEBRUARY_PDF_SHA256}
        if self.source_response_sha256s != tuple(sorted(expected_hashes)):
            raise ValueError("NRS source hash set does not match the two releases")
        if {record.source.sha256 for record in self.records} != expected_hashes:
            raise ValueError("NRS PDF hashes do not match locked records")

        by_id = {record.record_id: record for record in self.records}
        for role, values in _expected_records().items():
            record = by_id[getattr(self.roles, role)]
            published_at = values["published_at"]
            assert isinstance(published_at, datetime)
            if record.source.source_id != CENSUS_NRS_SOURCE_ID:
                raise ValueError("NRS lock accepts only archived new-home-sales facts")
            if record.source.publisher != (
                "U.S. Census Bureau and U.S. Department of Housing and Urban Development"
            ):
                raise ValueError(f"NRS {role} source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("NRS inputs must use versioned release snapshots")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("NRS source license boundary mismatch")
            if record.source.redistribution_note != _REDISTRIBUTION_NOTE:
                raise ValueError("NRS redistribution boundary mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("NRS sales levels must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"NRS {role} entity mismatch")
            if record.payload_schema_version != "1.0.0":
                raise ValueError(f"NRS {role} payload schema mismatch")
            if record.interval.availability_confidence != 1.0:
                raise ValueError("NRS timing must be deterministic")
            if record.interval.availability_rule != _AVAILABILITY_RULE:
                raise ValueError("NRS availability rule mismatch")
            if record.interval.published_at != published_at:
                raise ValueError(f"NRS {role} publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"NRS {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("NRS lock contains a post-decision input")
            if record.interval.revised_at is not None:
                raise ValueError("NRS inputs must be initial monthly-release records")
            if record.interval.valid_to is not None:
                raise ValueError("NRS monthly facts must have open valid-time intervals")
            expected_valid_from = datetime.fromisoformat(
                f"{values['reference_month']}-01T00:00:00+00:00"
            )
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"NRS {role} valid time mismatch")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"NRS {role} source vintage mismatch")
            if record.source.retrieved_at != record.interval.ingested_at:
                raise ValueError("NRS retrieval and ingestion times must agree")
            if record.source.retrieved_at < published_at:
                raise ValueError("NRS retrieval cannot precede official release")
            if record.source.retrieved_at > self.build_epoch:
                raise ValueError("NRS retrieval cannot occur after build_epoch")
            if record.source.sha256 != values["pdf_sha256"]:
                raise ValueError(f"NRS {role} PDF hash mismatch")
            if str(record.source.url) != values["pdf_url"]:
                raise ValueError(f"NRS {role} source URL mismatch")
            if record.source.source_version != values["source_version"]:
                raise ValueError(f"NRS {role} source version mismatch")
            for field, expected_value in values["critical_payload"].items():
                if record.payload.get(field) != expected_value:
                    raise ValueError(f"NRS {role} {field} mismatch")
            if _hash(record.payload) != values["payload_sha256"]:
                raise ValueError(f"NRS {role} payload hash mismatch")
            if _sales_level(record) != values["value_units"]:
                raise ValueError(f"NRS {role} sales level mismatch")

        decision_record = by_id[self.roles.february_decision_snapshot]
        january_record = by_id[self.roles.january_release_snapshot]
        if _sales_level(january_record) != 764_000:
            raise ValueError("NRS January release lineage must retain 764,000")
        if decision_record.payload["prior_month_revised_value_units"] != 800_000:
            raise ValueError("NRS decision snapshot must retain revised January at 800,000")
        if decision_record.payload["prior_month_value_in_previous_release_units"] != 764_000:
            raise ValueError("NRS decision snapshot revision lineage mismatch")
        if decision_record.payload["prior_month_revision_delta_units"] != 36_000:
            raise ValueError("NRS decision snapshot January revision must equal 36,000")
        if (
            decision_record.payload["prior_month_revised_value_units"]
            - _sales_level(decision_record)
            != 35_000
        ):
            raise ValueError("NRS decision-snapshot decline must equal 35,000 units SAAR")

        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match NRS input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> NewHomeSalesLevelBoundaryInputLock:
        """Normalize, validate, and self-hash an NRS input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_new_home_sales_level_boundary_input_lock(
    path: Path,
) -> NewHomeSalesLevelBoundaryInputLock:
    try:
        return NewHomeSalesLevelBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid Census/HUD NRS input lock: {path}") from error


def build_new_home_sales_level_boundary_replay_spec(
    lock: NewHomeSalesLevelBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 new-home-sales boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = lock.source_response_sha256s
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(vault.records_as_of(lock.decision_time, source_ids=[CENSUS_NRS_SOURCE_ID]))
        manifest = vault.manifest(generated_at=lock.build_epoch)
    selected_by_id = {record.record_id: record for record in selected}
    if set(selected_by_id) != set(record_ids):
        raise ValueError("TimeVault did not reproduce the NRS record IDs")
    for record in records:
        if selected_by_id[record.record_id].model_dump(mode="json") != record.model_dump(
            mode="json"
        ):
            raise ValueError("TimeVault changed an NRS locked fact")

    prefix = lock.artifact_prefix
    decision_record = by_role["february_decision_snapshot"]
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-census-hud-nrs-release-query",
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
                "new single-family houses sold at seasonally adjusted annual rates; the "
                "boundary uses revised January and initial February values from the single "
                "February-data release snapshot"
            ),
            "decision_snapshot": {
                "revised_january_sales_units_saar": decision_record.payload[
                    "prior_month_revised_value_units"
                ],
                "initial_february_sales_units_saar": decision_record.payload["value_units"],
            },
            "january_initial_release_retained_for_revision_lineage": True,
            "january_initial_release_used_as_endpoint_input": False,
            "release_time_rule": "10:00 America/New_York from each dated NRS PDF",
            "official_sampling_interval_used_as_range_input": False,
            "source_evidence_file_count": len(source_hashes),
        },
        limitations=(
            "The boundary uses only revised January and initial February values co-published "
            "in the March 24 decision snapshot.",
            "The March value and April 23 revision snapshot are excluded from every input.",
            "The January 764,000 initial value is retained only as revision lineage.",
            "The figures are annualized rates, not actual monthly transaction counts.",
            "The source-defined sale may precede a permit and is not necessarily a closing.",
            "Full five-page official PDFs remain local download evidence.",
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
        range_width=metrics["range_width_units_saar"],
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
                "This aggregate NRS boundary requires TimeVault, ShockCompiler, TrialCourt, "
                "and ReplayStudio; no property, builder, buyer, mortgage, closing, portfolio, "
                "or allocation input is invented."
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
            "Four actual engines ran over official archived Census/HUD NRS PDFs. Range "
            "construction uses only the 800,000-unit revised January and 765,000-unit initial "
            "February SAAR values co-published in the March 24 decision snapshot; the 764,000 "
            "January initial release is revision lineage only. Reported facts remain reported, "
            "while the 730,000-to-765,000 continuation envelope remains inferred with no "
            "probability. The April 23 March event and revisions stay only in a disjoint event "
            "lock. This is not an official forecast, sampling-confidence interval, calibrated "
            "interval, causal or housing-market model, COVID effect, transaction count, closing, "
            "trading signal, deployment, external validation, or user-impact claim."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are February-level persistence or one repetition of the single "
            "35,000-unit SAAR decline visible inside the March 24 release snapshot.",
            "The January initial-release level does not numerically set either endpoint.",
            "The April 23 release and every later-known value are excluded from every input.",
            "Official 90-percent sampling intervals are source metadata, not range inputs.",
            "A sale is a deposit or signed agreement and may precede permit issuance.",
            "No property, builder, buyer, mortgage, closing, return, or recommendation exists.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: NewHomeSalesLevelBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    january_initial = _sales_level(records_by_role["january_release_snapshot"])
    decision_record = records_by_role["february_decision_snapshot"]
    revised_january = decision_record.payload["prior_month_revised_value_units"]
    february_initial = _sales_level(decision_record)
    if not isinstance(revised_january, int) or isinstance(revised_january, bool):
        raise ValueError("NRS revised January level must be integer units SAAR")
    known_decline = revised_january - february_initial
    if known_decline != 35_000:
        raise ValueError("NRS decision snapshot must establish the verified 35,000 decline")
    lower = february_initial - known_decline
    upper = february_initial
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    variable = "next_new_single_family_houses_sold_level_units_saar"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-new-home-sales-level-range",
        target_id=_ENTITY_ID,
        variable=variable,
        unit="houses_at_seasonally_adjusted_annual_rate",
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
            "March 24 NRS release snapshot."
        ),
        limitations=(
            "The January initial-release value is revision lineage and does not set the decline.",
            "One decision-snapshot decline defines a stress range, not a forecast, probability, "
            "confidence interval, or calibrated predictive interval.",
            "Official 90-percent sampling margins do not define either endpoint.",
            "The April 23 March event and its revisions are absent from range construction.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-new-home-sales-level-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate February sales-level persistence or one repetition of the known "
            "decision-snapshot decline using only facts available at the boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, or regime meaning.",
            "The April 23 release and all its revisions are evaluated only afterward.",
            "The aggregate annualized rate is not a property or transaction dataset.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, variable)
    initial_state = {state_key: float(february_initial)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "january_initial_release_sales_units_saar": january_initial,
        "decision_snapshot_revised_january_sales_units_saar": revised_january,
        "january_revision_delta_known_at_decision_units_saar": (revised_january - january_initial),
        "february_initial_sales_units_saar": february_initial,
        "known_decision_snapshot_decline_units_saar": known_decline,
        "lower_level_units_saar": lower,
        "upper_level_units_saar": upper,
        "range_width_units_saar": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.new-home-sales-level-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-census-hud-nrs-new-home-sales-level-program",
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
                "definition": "persistence of the February initial NRS sales level",
            },
            "bound_construction": {
                "lower_level_units_saar": lower,
                "upper_level_units_saar": upper,
                "range_width_units_saar": width,
                "known_decision_snapshot_decline_units_saar": known_decline,
                "endpoint_method": (
                    "latest_initial_level_persistence_or_repeat_same_release_snapshot_decline"
                ),
                "basis_is_single_february_release_snapshot": True,
                "january_initial_release_used_as_numeric_endpoint_input": False,
                "official_sampling_interval_used": False,
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
            "The April 23 March value and revised February value are absent.",
            "The range is not an official interval, probability, causal model, or forecast.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: NewHomeSalesLevelBoundaryInputLock,
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
            "A retrospectively constructed one-decline NRS boundary qualifies for research "
            "eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "NRS is a sampled aggregate annualized rate with revisions. One decision-time "
            "decline and one later outcome cannot establish predictive validity or housing, "
            "price, pandemic, policy, builder, buyer, mortgage, or closing causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-decision-snapshot NRS range width in units SAAR",
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
        output_manifest_sha256=_hash({"new_home_sales_range_width_units_saar": range_width}),
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
            "No April 23 NRS fact or revision is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective NRS attempt must fail closed")
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
            claim_id="claim-reported-census-hud-nrs-decision-levels",
            statement=(
                "The March 24 Census/HUD NRS release reports a revised January sales rate of "
                f"{metrics['decision_snapshot_revised_january_sales_units_saar']:,} and an "
                "initial February rate of "
                f"{metrics['february_initial_sales_units_saar']:,} units SAAR."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "The January release's 764,000 initial value is retained for revision lineage, "
                "not substituted for the decision-time revised value."
            ),
            limitations=(
                "These are sampled annualized aggregate rates, not actual monthly sales, "
                "property records, closings, mortgages, buyers, or builders.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-census-hud-nrs-sales-level-range",
            statement=(
                "The next-release stress endpoints are February-level persistence or one "
                "repeat of the known "
                f"{metrics['known_decision_snapshot_decline_units_saar']:,}-unit SAAR "
                "decision-snapshot decline: "
                f"[{metrics['lower_level_units_saar']:,}, "
                f"{metrics['upper_level_units_saar']:,}] units SAAR."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability, coverage, official, or causal guarantee.",
            limitations=(
                "The April 23 event and every revision in that release were not used to set "
                "the range.",
                "Official sampling margins do not set the endpoints.",
                "The range has no housing, price, pandemic, policy, buyer, or builder causality.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-census-hud-nrs-trial-rejection",
            statement="TrialCourt rejected the retrospective one-decline NRS attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external or domain review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-census-hud-nrs-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not transactions, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-census-hud-nrs-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not housing correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _expected_records() -> dict[str, dict[str, Any]]:
    common = {
        "release_series": "Monthly New Residential Sales",
        "metric": "new_single_family_houses_sold_sa_annual_rate",
        "release_time_local": "10:00:00",
        "release_timezone": "America/New_York",
        "sale_definition_boundary": (
            "deposit taken or sales agreement signed; may precede permit issuance"
        ),
        "snapshot_semantics": "preliminary headline value in this archived release",
        "pdf_table_snapshot_verified": True,
        "release_pdf_pages": 5,
        "availability_method": "exact_time_in_pdf",
        "unit": "Houses at Seasonally Adjusted Annual Rate",
    }
    january_payload = {
        **common,
        "reference_month": "2020-01",
        "release_date": "2020-02-26",
        "release_number": "CB20-28",
        "official_release_at": "2020-02-26T15:00:00+00:00",
        "release_timezone_abbreviation": "EST",
        "value_units": 764_000,
        "value_thousand_units": 764,
        "reported_monthly_change_percent": "7.9",
        "reported_monthly_margin_90_percent": "17.8",
        "reported_monthly_ci_includes_zero": True,
        "reported_monthly_change_significant_at_90_percent": False,
        "reported_year_over_year_change_percent": "18.6",
        "reported_year_over_year_margin_90_percent": "19.2",
        "reported_year_over_year_ci_includes_zero": True,
        "year_over_year_comparison_value_units": 644_000,
        "prior_month": "2019-12",
        "prior_month_revised_value_units": 708_000,
        "prior_month_revised_value_thousand_units": 708,
        "prior_month_value_in_previous_release_units": None,
        "prior_month_revision_delta_units": None,
        "new_houses_for_sale_units": 324_000,
        "reported_months_supply": "5.1",
        "median_sales_price_usd": 348_200,
        "average_sales_price_usd": 402_300,
        "table1a_average_rse_percent": 9,
        "reported_average_preliminary_revision_percent": "4.2",
        "covid_publication_standard_statement_present": False,
        "release_pdf_url": ("https://www.census.gov/construction/nrs/pdf/newressales_202001.pdf"),
        "release_pdf_sha256": _JANUARY_PDF_SHA256,
    }
    february_payload = {
        **common,
        "reference_month": "2020-02",
        "release_date": "2020-03-24",
        "release_number": "CB20-49",
        "official_release_at": "2020-03-24T14:00:00+00:00",
        "release_timezone_abbreviation": "EDT",
        "value_units": 765_000,
        "value_thousand_units": 765,
        "reported_monthly_change_percent": "-4.4",
        "reported_monthly_margin_90_percent": "14.8",
        "reported_monthly_ci_includes_zero": True,
        "reported_monthly_change_significant_at_90_percent": False,
        "reported_year_over_year_change_percent": "14.3",
        "reported_year_over_year_margin_90_percent": "17.5",
        "reported_year_over_year_ci_includes_zero": True,
        "year_over_year_comparison_value_units": 669_000,
        "prior_month": "2020-01",
        "prior_month_revised_value_units": 800_000,
        "prior_month_revised_value_thousand_units": 800,
        "prior_month_value_in_previous_release_units": 764_000,
        "prior_month_revision_delta_units": 36_000,
        "new_houses_for_sale_units": 319_000,
        "reported_months_supply": "5.0",
        "median_sales_price_usd": 345_900,
        "average_sales_price_usd": 403_800,
        "table1a_average_rse_percent": 8,
        "reported_average_preliminary_revision_percent": "4.6",
        "covid_publication_standard_statement_present": False,
        "release_pdf_url": ("https://www.census.gov/construction/nrs/pdf/newressales_202002.pdf"),
        "release_pdf_sha256": _FEBRUARY_PDF_SHA256,
    }
    return {
        "january_release_snapshot": {
            "reference_month": "2020-01",
            "published_at": datetime(2020, 2, 26, 15, 0, tzinfo=UTC),
            "pdf_sha256": _JANUARY_PDF_SHA256,
            "pdf_url": january_payload["release_pdf_url"],
            "source_version": ("CENSUS-HUD-NRS:2020-01:CB20-28:pdf:ba86558efb14745ddf6c5668"),
            "payload_sha256": ("c6354ea987406f1b61de5429187a52ee206fc152068522e93fde8a9b45846686"),
            "value_units": 764_000,
            "critical_payload": january_payload,
        },
        "february_decision_snapshot": {
            "reference_month": "2020-02",
            "published_at": _DECISION_TIME,
            "pdf_sha256": _FEBRUARY_PDF_SHA256,
            "pdf_url": february_payload["release_pdf_url"],
            "source_version": ("CENSUS-HUD-NRS:2020-02:CB20-49:pdf:9a47e1fd70c0830394a9681e"),
            "payload_sha256": ("16f86f03a1447bd20121b07541f66f963f41d39cc6a86eb4fe9dfe4506f123db"),
            "value_units": 765_000,
            "critical_payload": february_payload,
        },
    }


def _records_by_role(
    lock: NewHomeSalesLevelBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _sales_level(record: BitemporalRecord) -> int:
    value = record.payload["value_units"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("NRS sales level must be integer units SAAR")
    if not 1 <= value <= 10_000_000:
        raise ValueError("NRS sales level is outside the supported range")
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
