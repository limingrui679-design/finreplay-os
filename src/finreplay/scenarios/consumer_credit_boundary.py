"""March 2020 Federal Reserve G.19 revolving-credit boundary."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
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

FED_G19_SOURCE_ID = "federalreserve.g19.archived_consumer_credit"
_ENTITY_ID = "federal_reserve_g19:revolving_consumer_credit"
_PDF_SHA256 = "b70e0ed0718ab527f698ae2c6d16821491f2309657d25e20961c3e7ae28424a2"
_PDF_URL = "https://www.federalreserve.gov/releases/g19/20200407/g19.pdf"
_PUBLISHED_AT = datetime(2020, 4, 7, 19, 0, tzinfo=UTC)
_SNAPSHOT_VALUES = {"2020-01": -270, "2020-02": 460}
_SNAPSHOT_STATUSES = {"2020-01": "revised", "2020-02": "preliminary"}
_SNAPSHOT_PREVIOUS = {"2020-01": -330, "2020-02": None}
_SNAPSHOT_REVISIONS = {"2020-01": 60, "2020-02": None}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConsumerCreditBoundaryRoles(_StrictModel):
    """Two April 7 G.19 monthly values assigned to the boundary."""

    january_revised_change: str = Field(min_length=1, max_length=300)
    february_preliminary_change: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> ConsumerCreditBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("G.19 consumer-credit role record IDs must be unique")
        return self


class ConsumerCreditBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision Federal Reserve G.19 facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: ConsumerCreditBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=1, max_length=1)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> ConsumerCreditBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("G.19 consumer-credit build_epoch cannot precede decision_time")
        if self.decision_time != _PUBLISHED_AT:
            raise ValueError("G.19 consumer-credit decision_time must equal the April release")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("G.19 consumer-credit records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("G.19 consumer-credit roles must cover every record exactly")
        if self.source_response_sha256s != (_PDF_SHA256,):
            raise ValueError("G.19 consumer-credit source hash set does not match April PDF")
        if {record.source.sha256 for record in self.records} != {_PDF_SHA256}:
            raise ValueError("G.19 consumer-credit source hashes do not match records")
        by_id = {record.record_id: record for record in self.records}
        expected: dict[str, dict[str, Any]] = {
            "january_revised_change": {
                "reference_month": "2020-01",
                "value": -270,
                "total_change": "3.5",
                "revolving_change": "-2.7",
                "nonrevolving_change": "5.6",
                "total_flow": "144.7",
                "revolving_flow": "-29.4",
                "nonrevolving_flow": "174.2",
                "total_outstanding": "4203.2",
                "revolving_outstanding": "1091.9",
                "nonrevolving_outstanding": "3111.3",
                "total_flow_tenths": 1_447,
                "revolving_flow_tenths": -294,
                "nonrevolving_flow_tenths": 1_742,
                "total_outstanding_tenths": 42_032,
                "revolving_outstanding_tenths": 10_919,
                "nonrevolving_outstanding_tenths": 31_113,
                "status": "revised",
                "status_marker": "r",
                "previous": -330,
                "revision_delta": 60,
                "revised_at": _PUBLISHED_AT,
            },
            "february_preliminary_change": {
                "reference_month": "2020-02",
                "value": 460,
                "total_change": "6.4",
                "revolving_change": "4.6",
                "nonrevolving_change": "7.0",
                "total_flow": "268.0",
                "revolving_flow": "50.4",
                "nonrevolving_flow": "217.6",
                "total_outstanding": "4225.5",
                "revolving_outstanding": "1096.1",
                "nonrevolving_outstanding": "3129.4",
                "total_flow_tenths": 2_680,
                "revolving_flow_tenths": 504,
                "nonrevolving_flow_tenths": 2_176,
                "total_outstanding_tenths": 42_255,
                "revolving_outstanding_tenths": 10_961,
                "nonrevolving_outstanding_tenths": 31_294,
                "status": "preliminary",
                "status_marker": "p",
                "previous": None,
                "revision_delta": None,
                "revised_at": None,
            },
        }
        for role, values in expected.items():
            record = by_id[getattr(self.roles, role)]
            self._validate_record(role, record, values)
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match G.19 consumer-credit input content")
        return self

    def _validate_record(
        self,
        role: str,
        record: BitemporalRecord,
        values: dict[str, Any],
    ) -> None:
        if record.source.source_id != FED_G19_SOURCE_ID:
            raise ValueError("G.19 lock accepts only archived consumer-credit facts")
        if record.source.publisher != "Board of Governors of the Federal Reserve System":
            raise ValueError("G.19 consumer-credit source publisher mismatch")
        if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
            raise ValueError("G.19 consumer-credit inputs must use versioned snapshots")
        if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
            raise ValueError("G.19 consumer-credit source license boundary mismatch")
        if record.evidence_class is not EvidenceClass.REPORTED:
            raise ValueError("G.19 consumer-credit changes must remain reported")
        if record.entity_id != _ENTITY_ID:
            raise ValueError(f"G.19 consumer-credit {role} entity mismatch")
        if record.payload_schema_version != "1.1.0":
            raise ValueError(f"G.19 consumer-credit {role} payload schema mismatch")
        if record.interval.availability_confidence < 1.0:
            raise ValueError("G.19 consumer-credit timing must be deterministic")
        if record.interval.published_at != _PUBLISHED_AT:
            raise ValueError(f"G.19 consumer-credit {role} publication time mismatch")
        if record.interval.available_at != _PUBLISHED_AT:
            raise ValueError(f"G.19 consumer-credit {role} availability time mismatch")
        if record.interval.available_at > self.decision_time:
            raise ValueError("G.19 consumer-credit lock contains a post-decision input")
        if record.interval.revised_at != values["revised_at"]:
            raise ValueError(f"G.19 consumer-credit {role} revision clock mismatch")
        expected_valid_from = datetime.fromisoformat(
            f"{values['reference_month']}-01T00:00:00+00:00"
        )
        if record.interval.valid_from != expected_valid_from:
            raise ValueError(f"G.19 consumer-credit {role} valid time mismatch")
        if record.source.vintage_as_of != _PUBLISHED_AT:
            raise ValueError(f"G.19 consumer-credit {role} source vintage mismatch")
        if record.source.sha256 != _PDF_SHA256:
            raise ValueError(f"G.19 consumer-credit {role} PDF hash mismatch")
        if str(record.source.url) != _PDF_URL:
            raise ValueError(f"G.19 consumer-credit {role} source URL mismatch")
        expected_source_version = f"FED-G19:2020-02:pdf:{_PDF_SHA256[:24]}"
        if record.source.source_version != expected_source_version:
            raise ValueError(f"G.19 consumer-credit {role} source version mismatch")
        checks = {
            "release_date": "2020-04-07",
            "release_reference_month": "2020-02",
            "reference_month": values["reference_month"],
            "release_series": "G.19 Consumer Credit",
            "metric": "revolving_consumer_credit_percent_change_annual_rate",
            "value_basis_points": values["value"],
            "reported_total_change_percent": values["total_change"],
            "reported_revolving_change_percent": values["revolving_change"],
            "reported_nonrevolving_change_percent": values["nonrevolving_change"],
            "reported_total_flow_annual_rate_billion_dollars": values["total_flow"],
            "reported_revolving_flow_annual_rate_billion_dollars": values["revolving_flow"],
            "reported_nonrevolving_flow_annual_rate_billion_dollars": values["nonrevolving_flow"],
            "reported_total_outstanding_billion_dollars": values["total_outstanding"],
            "reported_revolving_outstanding_billion_dollars": values["revolving_outstanding"],
            "reported_nonrevolving_outstanding_billion_dollars": values["nonrevolving_outstanding"],
            "total_flow_tenths_billion_dollars": values["total_flow_tenths"],
            "revolving_flow_tenths_billion_dollars": values["revolving_flow_tenths"],
            "nonrevolving_flow_tenths_billion_dollars": values["nonrevolving_flow_tenths"],
            "total_outstanding_tenths_billion_dollars": values["total_outstanding_tenths"],
            "revolving_outstanding_tenths_billion_dollars": values["revolving_outstanding_tenths"],
            "nonrevolving_outstanding_tenths_billion_dollars": values[
                "nonrevolving_outstanding_tenths"
            ],
            "estimate_status": values["status"],
            "status_marker": values["status_marker"],
            "previous_release_same_reference_revolving_change_basis_points": values["previous"],
            "revision_delta_basis_points": values["revision_delta"],
            "release_snapshot_revolving_change_basis_points": _SNAPSHOT_VALUES,
            "release_snapshot_estimate_statuses": _SNAPSHOT_STATUSES,
            "release_snapshot_previous_release_same_reference_basis_points": (_SNAPSHOT_PREVIOUS),
            "release_snapshot_revision_delta_basis_points": _SNAPSHOT_REVISIONS,
            "release_time_local": "15:00:00",
            "release_timezone": "America/New_York",
            "release_timezone_abbreviation": "EDT",
            "official_release_at": _PUBLISHED_AT.isoformat(),
            "unit": "Basis Points",
            "snapshot_semantics": ("monthly G.19 table value reported in this archived release"),
            "simple_annual_rate_from_unrounded_data": True,
            "pdf_table_snapshot_verified": True,
            "release_pdf_url": _PDF_URL,
            "release_pdf_sha256": _PDF_SHA256,
            "release_pdf_pages": 4,
            "release_pdf_page_rotation_degrees": 90,
            "availability_method": "exact_local_time_and_date_stated_in_pdf",
        }
        for field, expected_value in checks.items():
            if record.payload.get(field) != expected_value:
                raise ValueError(f"G.19 consumer-credit {role} {field} mismatch")
        value = _monthly_change(record)
        reported = record.payload.get("reported_revolving_change_percent")
        if not isinstance(reported, str):
            raise ValueError(f"G.19 consumer-credit {role} reported value must be string")
        try:
            reported_basis_points = Decimal(reported) * 100
        except InvalidOperation as error:
            raise ValueError(
                f"G.19 consumer-credit {role} reported value must be decimal"
            ) from error
        if not reported_basis_points.is_finite() or reported_basis_points != value:
            raise ValueError(f"G.19 consumer-credit {role} percent/basis-point mismatch")

    @classmethod
    def create(cls, payload: dict[str, Any]) -> ConsumerCreditBoundaryInputLock:
        """Normalize, validate, and self-hash a G.19 input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_consumer_credit_boundary_input_lock(path: Path) -> ConsumerCreditBoundaryInputLock:
    try:
        return ConsumerCreditBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid G.19 consumer-credit input lock: {path}") from error


def build_consumer_credit_boundary_replay_spec(
    lock: ConsumerCreditBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 G.19 boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(vault.records_as_of(lock.decision_time, source_ids=[FED_G19_SOURCE_ID]))
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the G.19 consumer-credit input lock")
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-fed-g19-release-query",
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
            "same_release_snapshot": True,
        },
        limitations=(
            "The lock contains two monthly G.19 values from one April 7 release snapshot.",
            "January is revised and February is preliminary; both may change later.",
            "Aggregate revolving credit does not identify households, cards, or transactions.",
            "The full four-page PDF remains local download evidence.",
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
        range_width=metrics["range_width_basis_points"],
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
                "This aggregate revolving-credit release boundary requires TimeVault, "
                "ShockCompiler, TrialCourt, and ReplayStudio; no household, card account, "
                "transaction, position, order, portfolio, or allocation input is invented."
            ),
        },
        limitations=("Static rendering does not validate the change-range heuristic.",),
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
            "Four actual engines ran over two locked G.19 revolving-credit monthly changes "
            "in the April 7 release snapshot and available at the decision time. January's "
            "revised -270-basis-point value and February's preliminary 460-basis-point value "
            "remain reported evidence. The 460-to-1,190-basis-point persistence-or-repeat-"
            "known-increase range remains inferred with no probability. TrialCourt rejects "
            "retrospective promotion. The May 7 March value and later January/February "
            "revisions are held only in a disjoint post-decision event lock. This is not a "
            "forecast, calibrated interval, consumer or pandemic causal model, card-spending "
            "measure, trading signal, production deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are February persistence or one repetition of the single known "
            "730-basis-point January-to-February increase, with no probability.",
            "The May 7 release and its revisions are excluded from every decision input.",
            "G.19 values are simple annual rates computed from unrounded data; the table's "
            "one-decimal values are used rather than rounded headline fractions.",
            "Two monthly values do not identify consumer causes or a stable regime.",
            "No position, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: ConsumerCreditBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    january = _monthly_change(records_by_role["january_revised_change"])
    february = _monthly_change(records_by_role["february_preliminary_change"])
    known_increase = february - january
    if known_increase <= 0:
        raise ValueError("two known G.19 values must establish a positive change step")
    lower = february
    upper = february + known_increase
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-revolving-credit-change-range",
        target_id=_ENTITY_ID,
        variable="next_revolving_credit_change_annual_rate_basis_points",
        unit="basis_points",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use latest-value persistence or one repeat of the only known January-to-February "
            "increase as transparent next-release stress endpoints."
        ),
        limitations=(
            "Two values and one increase define a stress range, not a forecast or confidence "
            "interval.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-revolving-credit-change-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate latest persistence or one repetition of the known increase using only "
            "G.19 facts available at the historical boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, consumer-causality, or regime meaning.",
            "The May 7 release and all of its revisions are evaluated only afterward.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, "next_revolving_credit_change_annual_rate_basis_points")
    initial_state = {state_key: float(february)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "january_revised_change_basis_points": january,
        "february_preliminary_change_basis_points": february,
        "known_increase_basis_points": known_increase,
        "lower_change_basis_points": lower,
        "upper_change_basis_points": upper,
        "range_width_basis_points": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.revolving-credit-change-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-fed-g19-revolving-credit-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_changes": metrics,
            "naive_baseline": {
                "next_revolving_credit_change_annual_rate_basis_points": february,
                "definition": "persistence of the latest April 7 G.19 table value",
            },
            "bound_construction": {
                "lower_change_basis_points": lower,
                "upper_change_basis_points": upper,
                "range_width_basis_points": width,
                "known_increase_basis_points": known_increase,
                "endpoint_method": "latest_persistence_or_repeat_known_increase",
                "table_values_not_rounded_headline_fractions": True,
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
            "The endpoints mechanically reuse one known increase.",
            "The May 7 March value and revised January/February values are absent.",
            "The range is not a probability, confidence interval, or causal model.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: ConsumerCreditBoundaryInputLock,
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
            "A retrospectively constructed one-increase G.19 revolving-credit boundary "
            "qualifies for research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "G.19 reports aggregate revolving consumer credit, but two monthly values and one "
            "later outcome cannot establish predictive validity or household, card-spending, "
            "pandemic, policy, lender, or macroeconomic causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-increase G.19 change-range width in basis points",
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
        output_manifest_sha256=_hash({"consumer_credit_range_width_basis_points": range_width}),
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
            "known-monthly-increase": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No May 7 G.19 fact or revision is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective G.19 consumer-credit attempt must fail closed")
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
            claim_id="claim-reported-fed-g19-revolving-credit-changes",
            statement=(
                "The archived April 7 Federal Reserve G.19 release reports January revolving-"
                f"credit growth at {metrics['january_revised_change_basis_points']} basis "
                f"points and February at {metrics['february_preliminary_change_basis_points']} "
                "basis points, both as simple annual rates."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are aggregate release-snapshot estimates, not household, account, "
                "transaction, or card-spending observations."
            ),
            limitations=("The pack includes one release snapshot and two monthly values.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-fed-g19-change-range",
            statement=(
                "The next-release stress endpoints are latest-value persistence or one repeat "
                f"of the known {metrics['known_increase_basis_points']}-basis-point increase: "
                f"[{metrics['lower_change_basis_points']}, "
                f"{metrics['upper_change_basis_points']}] basis points."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability or coverage guarantee.",
            limitations=(
                "The May 7 event and its revisions were not used to set the range.",
                "The range has no consumer, pandemic, policy, or lender causal interpretation.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-fed-g19-trial-rejection",
            statement="TrialCourt rejected the retrospective one-increase G.19 attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-fed-g19-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not transactions, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-fed-g19-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _records_by_role(lock: ConsumerCreditBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _monthly_change(record: BitemporalRecord) -> int:
    value = record.payload["value_basis_points"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("G.19 consumer-credit change must be integer basis points")
    if not -100_000 <= value <= 100_000:
        raise ValueError("G.19 consumer-credit change is outside supported range")
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
