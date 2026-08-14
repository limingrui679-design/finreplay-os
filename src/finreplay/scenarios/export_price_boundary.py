"""February 2020 BLS all-export price monthly-change boundary."""

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

BLS_EXPORT_PRICE_SOURCE_ID = "bls.export_prices.archived_all_exports"
_ENTITY_ID = "bls_export_price_index:all_exports_united_states"
_DECISION_TIME = datetime(2020, 3, 13, 12, 30, tzinfo=UTC)
_JANUARY_HTML_SHA256 = "dcac2c1daecc12c2bce0769999b467e25b4a4c6dea66af3538feb88fe72247ce"
_JANUARY_PDF_SHA256 = "186c6a60276ac896bdf37e1db97e7c6a313dd5e2cd2087e592b2ae8a76323327"
_FEBRUARY_HTML_SHA256 = "1b196f0ebed0fdd41d27a7696f956a5e962b1178b0687eade2ce06f845db15ae"
_FEBRUARY_PDF_SHA256 = "e0167a9ec66bc0b884d0f58c5e7de42ddc8fd849f150bf438f9590f4be7fbbf9"
BLS_EXPORT_PRICE_SUPPORTING_RECEIPT_SHA256 = (
    "744153523c39d1b8df64900dad2544aec0d30c00c669f01ddc358cd64f5c630c"
)
BLS_EXPORT_PRICE_INPUT_RESPONSE_SHA256S = tuple(
    sorted(
        (
            _JANUARY_HTML_SHA256,
            _JANUARY_PDF_SHA256,
            _FEBRUARY_HTML_SHA256,
            _FEBRUARY_PDF_SHA256,
        )
    )
)
_AVAILABILITY_RULE = (
    "Each selected BLS Import and Export Price Index release states that transmission is "
    "embargoed until 8:30 a.m. EST or EDT on its named release date. FinReplay validates "
    "the weekday and timezone abbreviation against America/New_York, cross-checks the "
    "complete archived HTML and PDF, and makes the snapshot eligible at that exact time. "
    "Current retrieval metadata is never backdated."
)
_REDISTRIBUTION_NOTE = (
    "BLS-published material is public domain except identified third-party material. "
    "Attribute the U.S. Bureau of Labor Statistics, retain archive URLs and release "
    "dates, and do not use the protected BLS emblem."
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExportPriceBoundaryRoles(_StrictModel):
    """The two all-export monthly-change releases available at the boundary."""

    january_release: str = Field(min_length=1, max_length=300)
    february_decision_release: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> ExportPriceBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("BLS export price role record IDs must be unique")
        return self


class ExportPriceBoundaryInputLock(_StrictModel):
    """Content-addressed paired-format export-price facts known at decision time."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: ExportPriceBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=4, max_length=4)
    supporting_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> ExportPriceBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.decision_time != _DECISION_TIME:
            raise ValueError("export-price decision_time must equal the March 13 embargo end")
        if self.build_epoch < self.decision_time:
            raise ValueError("export-price build_epoch cannot precede decision_time")
        if self.source_response_sha256s != BLS_EXPORT_PRICE_INPUT_RESPONSE_SHA256S:
            raise ValueError("export-price source hashes do not match four official responses")
        if self.supporting_receipt_sha256 != BLS_EXPORT_PRICE_SUPPORTING_RECEIPT_SHA256:
            raise ValueError("export-price supporting receipt hash mismatch")

        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("export-price records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("export-price roles must cover every locked record exactly once")
        by_id = {record.record_id: record for record in self.records}
        observed_response_hashes: set[str] = set()
        for role, expected in _expected_records().items():
            record = by_id[getattr(self.roles, role)]
            published_at = expected["published_at"]
            valid_from = expected["valid_from"]
            assert isinstance(published_at, datetime)
            assert isinstance(valid_from, datetime)
            if record.source.source_id != BLS_EXPORT_PRICE_SOURCE_ID:
                raise ValueError("export-price lock accepts only paired archived release facts")
            if record.source.publisher != "U.S. Bureau of Labor Statistics":
                raise ValueError("export-price source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("export-price inputs must retain versioned release snapshots")
            if record.source.license_class is not LicenseClass.REDISTRIBUTABLE:
                raise ValueError("export-price source license boundary mismatch")
            if record.source.redistribution_note != _REDISTRIBUTION_NOTE:
                raise ValueError("export-price redistribution boundary mismatch")
            if record.source.sha256 != expected["pdf_sha256"]:
                raise ValueError(f"export-price {role} primary PDF hash mismatch")
            if str(record.source.url) != expected["pdf_url"]:
                raise ValueError(f"export-price {role} primary source URL mismatch")
            if record.source.source_version != expected["source_version"]:
                raise ValueError(f"export-price {role} source version mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("export-price changes must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"export-price {role} entity mismatch")
            if record.payload_schema_version != "1.0.0":
                raise ValueError(f"export-price {role} payload schema mismatch")
            if record.interval.availability_confidence != 1.0:
                raise ValueError("export-price timing must be deterministic")
            if record.interval.availability_rule != _AVAILABILITY_RULE:
                raise ValueError("export-price availability rule mismatch")
            if record.interval.published_at != published_at:
                raise ValueError(f"export-price {role} publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"export-price {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("export-price lock contains a post-decision input")
            if record.interval.valid_from != valid_from:
                raise ValueError(f"export-price {role} validity time mismatch")
            if record.interval.valid_to is not None or record.interval.revised_at is not None:
                raise ValueError("export-price selected facts must remain open snapshots")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"export-price {role} source vintage mismatch")
            if record.source.retrieved_at != record.interval.ingested_at:
                raise ValueError("export-price retrieval and ingestion times must agree")
            if record.source.retrieved_at < published_at:
                raise ValueError("export-price retrieval cannot precede official release")
            if record.source.retrieved_at > self.build_epoch:
                raise ValueError("export-price retrieval cannot occur after build_epoch")
            for field, value in expected["critical_payload"].items():
                if record.payload.get(field) != value:
                    raise ValueError(f"export-price {role} {field} mismatch")
            if _hash(record.payload) != expected["payload_sha256"]:
                raise ValueError(f"export-price {role} payload hash mismatch")
            observed_response_hashes.update(
                (record.source.sha256, str(record.payload["release_html_sha256"]))
            )
        if observed_response_hashes != set(self.source_response_sha256s):
            raise ValueError("export-price records do not bind all four response hashes")

        january = by_id[self.roles.january_release]
        february = by_id[self.roles.february_decision_release]
        if _change_bps(january) - _change_bps(february) != 180:
            raise ValueError("export-price inputs must establish the 180-basis-point decline")
        if february.payload["prior_month_change_tenths_percent"] != 6:
            raise ValueError("February release must report revised January change")
        if february.payload["prior_month_value_in_previous_release_tenths_percent"] != 7:
            raise ValueError("February release must retain January's first report")
        if february.payload["prior_month_revision_delta_tenths_percent"] != -1:
            raise ValueError("February release must retain the -10-basis-point January revision")

        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match export-price input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> ExportPriceBoundaryInputLock:
        """Normalize, validate, and self-hash an export-price input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_export_price_boundary_input_lock(path: Path) -> ExportPriceBoundaryInputLock:
    try:
        return ExportPriceBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid BLS export price input lock: {path}") from error


def build_export_price_boundary_replay_spec(
    lock: ExportPriceBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the February 2020 export-price boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = lock.source_response_sha256s
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                source_ids=[BLS_EXPORT_PRICE_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    selected_by_id = {record.record_id: record for record in selected}
    if set(selected_by_id) != set(record_ids):
        raise ValueError("TimeVault did not reproduce the export-price record IDs")
    for record in records:
        if selected_by_id[record.record_id].model_dump(mode="json") != record.model_dump(
            mode="json"
        ):
            raise ValueError("TimeVault changed a locked export-price fact")

    prefix = lock.artifact_prefix
    january = by_role["january_release"]
    february = by_role["february_decision_release"]
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-pair-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="paired-html-pdf-bls-export-price-release-query",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.REPORTED: len(records)},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "supporting_receipt_sha256": lock.supporting_receipt_sha256,
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
            "decision_observations_basis_points": {
                "january_all_exports_monthly_change": _change_bps(january),
                "february_all_exports_monthly_change": _change_bps(february),
                "february_prior_january_change": int(
                    february.payload["prior_month_change_tenths_percent"]
                )
                * 10,
                "january_revision_delta_basis_points": int(
                    february.payload["prior_month_revision_delta_tenths_percent"]
                )
                * 10,
            },
            "html_pdf_crosscheck_verified": True,
            "adjacent_prior_value_crosscheck_verified": True,
            "revision_window_months": 3,
            "seasonally_adjusted": False,
            "source_auxiliary_measures_used_as_range_input": False,
            "source_response_file_count": len(source_hashes),
        },
        limitations=(
            "The records are aggregate export-price changes, not export quantities or values.",
            "Each snapshot was retrieved later but remains tied to an archived release pair.",
            "The March all-export change is excluded from every ReplayPack input.",
            "Index levels, annual changes, and detailed categories set no endpoint.",
            "Monthly export-price data may be revised for three subsequent releases.",
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
                "This aggregate export-price boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no individual exporter, product, shipment, tariff, "
                "security, order, portfolio, allocation, or return input is invented."
            ),
        },
        limitations=("Static rendering does not validate the one-change range heuristic.",),
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
            "Four actual engines ran over two paired archived BLS all-export monthly-change "
            "releases. Reported changes remain reported; the -290-to--110-basis-point "
            "persistence-or-one-known-decline envelope remains inferred with no probability. "
            "The March event stays only in a disjoint event lock. This is not a BLS forecast, "
            "confidence interval, calibrated range, export-quantity or nominal-value measure, "
            "tariff, PPI, firm result, profit-and-loss, causal model, deployment, external "
            "validation, investment result, or user-impact claim."
        ),
        limitations=(
            lock.claim_boundary,
            "Endpoints are February persistence or one repeat of the 180-basis-point decline.",
            "The March event and every later-known value are excluded from every input.",
            "Index levels, annual changes, and categories are metadata, not endpoint inputs.",
            "The index measures U.S. export prices, not quantity or nominal export value.",
            "The index is not seasonally adjusted and may be revised for three releases.",
            "The COVID-19 text does not establish causality or unaffected measurement.",
            "No individual exporter, shipment, tariff, firm, portfolio, or recommendation exists.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: ExportPriceBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    january = _change_bps(records_by_role["january_release"])
    february = _change_bps(records_by_role["february_decision_release"])
    known_decline = january - february
    if known_decline != 180:
        raise ValueError("export-price inputs must establish the 180-basis-point decline")
    lower = february - known_decline
    upper = february
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    variable = "next_all_exports_monthly_change_basis_points"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-all-exports-change-range",
        target_id=_ENTITY_ID,
        variable=variable,
        unit="basis_points",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use February-change persistence or one repetition of the only known decline "
            "between the January and February first-reported all-export monthly changes."
        ),
        limitations=(
            "One adjacent-release change defines a stress range, not a forecast or probability.",
            "Index levels, annual changes, categories, and revisions set neither endpoint.",
            "The March event is absent from range construction.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-all-exports-change-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate February-change persistence or one repetition of the known 180-basis-point "
            "decline using only releases available at the boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, or regime meaning.",
            "The March all-export change is evaluated only afterward.",
            "The index is not an export quantity, nominal export value, tariff, PPI, or P&L.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, variable)
    initial_state = {state_key: float(february)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "january_change_basis_points": january,
        "february_change_basis_points": february,
        "known_decline_basis_points": known_decline,
        "lower_change_basis_points": lower,
        "upper_change_basis_points": upper,
        "range_width_basis_points": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.all-exports-change-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-bls-export-price-all-exports-change-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_release_changes": metrics,
            "naive_baseline": {
                variable: february,
                "definition": "persistence of the February all-export monthly change",
            },
            "bound_construction": {
                "lower_change_basis_points": lower,
                "upper_change_basis_points": upper,
                "range_width_basis_points": width,
                "known_decline_basis_points": known_decline,
                "endpoint_method": "latest_change_persistence_or_repeat_one_known_decline",
                "original_release_values_only": True,
                "source_auxiliary_measures_used": False,
                "prior_revision_used_as_endpoint": False,
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
            "The endpoints mechanically reuse one reported adjacent-release change.",
            "The March value and January revision are absent from endpoint construction.",
            "The range is not an official interval, probability, causal model, or forecast.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: ExportPriceBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    range_width: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=40)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-range-screen",
        hypothesis=(
            "A retrospectively constructed one-decline export-price boundary qualifies for "
            "research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "The all-export index aggregates U.S. export transaction prices. Two releases and "
            "one later outcome cannot establish predictive validity, pass-through, pandemic "
            "effects, quantities, nominal export value, tariffs, firm outcomes, or causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-decline all-export price range width in basis points",
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
        output_manifest_sha256=_hash({"all_exports_change_range_width_basis_points": range_width}),
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
            "known-adjacent-release-decline": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No March export-price fact is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective export-price attempt must fail closed")
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
            claim_id="claim-reported-bls-export-price-all-exports-changes",
            statement=(
                "The February 14 and March 13 BLS releases first reported all-export monthly "
                f"changes of {_format_bps(metrics['january_change_basis_points'])} and "
                f"{_format_bps(metrics['february_change_basis_points'])} percent."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary="These are aggregate export-price release facts, not shipment records.",
            limitations=(
                "The index is not quantity, nominal export value, tariff, PPI, or firm P&L.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-bls-export-price-change-range",
            statement=(
                "The next-release stress endpoints are February persistence or one repeat of "
                "the known 180-basis-point decline, from -290 to -110 basis points."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The two endpoints carry no probability or coverage claim.",
            limitations=("The March event did not set or widen the range.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-bls-export-price-trial-rejection",
            statement="TrialCourt rejected the retrospective one-decline export-price attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is internal fail-closed evidence, not external method validation.",
            limitations=("No predictive or causal validity is inferred.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-bls-export-price-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not orders, capital, capacity, or returns.",
            limitations=("They exist only to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-bls-export-price-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this case.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _expected_records() -> dict[str, dict[str, Any]]:
    return {
        "january_release": {
            "published_at": datetime(2020, 2, 14, 13, 30, tzinfo=UTC),
            "valid_from": datetime(2020, 1, 1, tzinfo=UTC),
            "pdf_sha256": _JANUARY_PDF_SHA256,
            "pdf_url": "https://www.bls.gov/news.release/archives/ximpim_02142020.pdf",
            "source_version": (
                "BLS-MXP:2020-01:USDL-20-0247:html:dcac2c1daecc12c2bce0:pdf:186c6a60276ac896bdf3"
            ),
            "payload_sha256": "4719a84455021d76cbb573ebc7eb8288d7582296c6d279953e1b88255eb8b48e",
            "critical_payload": {
                "release_date": "2020-02-14",
                "reference_month": "2020-01",
                "release_number": "USDL-20-0247",
                "release_series": "U.S. Import and Export Price Indexes",
                "metric": "all_exports_monthly_change_not_seasonally_adjusted",
                "value_tenths_percent": 7,
                "value_basis_points": 70,
                "prior_month": "2019-12",
                "prior_month_change_tenths_percent": -2,
                "prior_month_value_in_previous_release_tenths_percent": None,
                "prior_month_revision_delta_tenths_percent": None,
                "second_prior_month": "2019-11",
                "second_prior_month_change_tenths_percent": 1,
                "year_over_year_change_tenths_percent": 5,
                "table2_prior_unadjusted_index": "125.0",
                "table2_current_unadjusted_index": "125.9",
                "table2_monthly_change_sequence_tenths_percent": [0, 1, -2, 7],
                "revision_window_months": 3,
                "index_formula": "modified Laspeyres",
                "seasonally_adjusted": False,
                "covid_methodology_statement_present": False,
                "covid_methodology_statement": None,
                "release_timezone_abbreviation": "EST",
                "official_release_at": "2020-02-14T13:30:00+00:00",
                "release_html_sha256": _JANUARY_HTML_SHA256,
                "release_pdf_sha256": _JANUARY_PDF_SHA256,
                "release_pdf_pages": 18,
                "html_pdf_crosscheck_verified": True,
                "availability_method": "exact_bls_embargo_end_crosschecked_html_pdf",
                "unit": "Tenths of a Percent",
            },
        },
        "february_decision_release": {
            "published_at": _DECISION_TIME,
            "valid_from": datetime(2020, 2, 1, tzinfo=UTC),
            "pdf_sha256": _FEBRUARY_PDF_SHA256,
            "pdf_url": "https://www.bls.gov/news.release/archives/ximpim_03132020.pdf",
            "source_version": (
                "BLS-MXP:2020-02:USDL-20-0405:html:1b196f0ebed0fdd41d27:pdf:e0167a9ec66bc0b884d0"
            ),
            "payload_sha256": "73db367fca0f8333b156ddfd44be9ef9fa8db678a0bcd405207ca02d8e798060",
            "critical_payload": {
                "release_date": "2020-03-13",
                "reference_month": "2020-02",
                "release_number": "USDL-20-0405",
                "release_series": "U.S. Import and Export Price Indexes",
                "metric": "all_exports_monthly_change_not_seasonally_adjusted",
                "value_tenths_percent": -11,
                "value_basis_points": -110,
                "prior_month": "2020-01",
                "prior_month_change_tenths_percent": 6,
                "prior_month_value_in_previous_release_tenths_percent": 7,
                "prior_month_revision_delta_tenths_percent": -1,
                "second_prior_month": "2019-12",
                "second_prior_month_change_tenths_percent": -2,
                "year_over_year_change_tenths_percent": -13,
                "table2_prior_unadjusted_index": "125.8",
                "table2_current_unadjusted_index": "124.4",
                "table2_monthly_change_sequence_tenths_percent": [1, -2, 6, -11],
                "revision_window_months": 3,
                "index_formula": "modified Laspeyres",
                "seasonally_adjusted": False,
                "release_timezone_abbreviation": "EDT",
                "official_release_at": "2020-03-13T12:30:00+00:00",
                "release_html_sha256": _FEBRUARY_HTML_SHA256,
                "release_pdf_sha256": _FEBRUARY_PDF_SHA256,
                "release_pdf_pages": 18,
                "html_pdf_crosscheck_verified": True,
                "covid_methodology_statement_present": False,
                "covid_methodology_statement": None,
                "availability_method": "exact_bls_embargo_end_crosschecked_html_pdf",
                "unit": "Tenths of a Percent",
            },
        },
    }


def _records_by_role(lock: ExportPriceBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _change_bps(record: BitemporalRecord) -> int:
    value = record.payload.get("value_basis_points")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("export-price monthly change must be integer basis points")
    return value


def _format_bps(value: int) -> str:
    return f"{value / 100:.1f}"


def _cost_model() -> CostModel:
    return CostModel(
        commission_bps=1.0,
        half_spread_bps=1.0,
        market_impact_bps=1.0,
        borrow_bps_annual=0.0,
        max_participation_rate=0.05,
    )


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _canonical_datetime(value: datetime) -> str:
    return value.isoformat()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()
