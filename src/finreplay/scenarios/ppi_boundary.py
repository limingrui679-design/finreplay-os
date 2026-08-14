"""March 2020 BLS Producer Price Index final-demand boundary."""

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

BLS_PPI_SOURCE_ID = "bls.ppi.archived_final_demand"
_ENTITY_ID = "bls_ppi:final_demand_united_states"
_DECISION_TIME = datetime(2020, 4, 9, 12, 30, tzinfo=UTC)
_FEBRUARY_HTML_SHA256 = "515855b318616035f7d4a9d06672f90636f3ec3e424a630a0eb6076167573ca2"
_FEBRUARY_PDF_SHA256 = "392c9ee30d9deae5007a796917f8c332ecbc617e947a61b38562d67fc86c96b2"
_MARCH_HTML_SHA256 = "318dafbdf942ea9ac3157e4369de66cc11f09994f7ff8d07de3c159cd9d3f9ec"
_MARCH_PDF_SHA256 = "18540697b82c4cbb42703f24a44d808661bf2baf8883b135a2c1a385c1c6d7fb"
_INPUT_SOURCE_HASHES = tuple(
    sorted(
        (
            _FEBRUARY_HTML_SHA256,
            _FEBRUARY_PDF_SHA256,
            _MARCH_HTML_SHA256,
            _MARCH_PDF_SHA256,
        )
    )
)
_AVAILABILITY_RULE = (
    "Each selected BLS PPI release explicitly states that transmission is embargoed until "
    "8:30 a.m. EDT on its named release date. FinReplay validates the weekday and EDT "
    "abbreviation against America/New_York, cross-checks the archived HTML and PDF, and "
    "makes the snapshot eligible at that exact time. Current retrieval metadata is never "
    "backdated."
)
_REDISTRIBUTION_NOTE = (
    "BLS-published material is public domain except identified third-party material. "
    "Attribute the U.S. Bureau of Labor Statistics, retain archive URLs and release "
    "dates, and do not use the protected BLS emblem."
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PPIBoundaryRoles(_StrictModel):
    """The two pre-decision final-demand monthly-change releases."""

    february_release: str = Field(min_length=1, max_length=300)
    march_decision_release: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> PPIBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("BLS PPI role record IDs must be unique")
        return self


class PPIBoundaryInputLock(_StrictModel):
    """Content-addressed paired-format PPI facts known at the decision time."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: PPIBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=4, max_length=4)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> PPIBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.decision_time != _DECISION_TIME:
            raise ValueError("PPI decision_time must equal the April 9 embargo end")
        if self.build_epoch < self.decision_time:
            raise ValueError("PPI build_epoch cannot precede decision_time")
        if self.source_response_sha256s != _INPUT_SOURCE_HASHES:
            raise ValueError("PPI source hash set does not match four official responses")

        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("PPI records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("PPI roles must cover every locked record exactly once")
        by_id = {record.record_id: record for record in self.records}
        observed_response_hashes: set[str] = set()
        for role, expected in _expected_records().items():
            record = by_id[getattr(self.roles, role)]
            published_at = expected["published_at"]
            valid_from = expected["valid_from"]
            assert isinstance(published_at, datetime)
            assert isinstance(valid_from, datetime)
            if record.source.source_id != BLS_PPI_SOURCE_ID:
                raise ValueError("PPI lock accepts only paired archived BLS PPI facts")
            if record.source.publisher != "U.S. Bureau of Labor Statistics":
                raise ValueError("PPI source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("PPI inputs must retain versioned release snapshots")
            if record.source.license_class is not LicenseClass.REDISTRIBUTABLE:
                raise ValueError("PPI source license boundary mismatch")
            if record.source.redistribution_note != _REDISTRIBUTION_NOTE:
                raise ValueError("PPI redistribution boundary mismatch")
            if record.source.sha256 != expected["pdf_sha256"]:
                raise ValueError(f"PPI {role} primary PDF hash mismatch")
            if str(record.source.url) != expected["pdf_url"]:
                raise ValueError(f"PPI {role} primary source URL mismatch")
            if record.source.source_version != expected["source_version"]:
                raise ValueError(f"PPI {role} source version mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("PPI changes must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"PPI {role} entity mismatch")
            if record.payload_schema_version != "1.0.0":
                raise ValueError(f"PPI {role} payload schema mismatch")
            if record.interval.availability_confidence != 1.0:
                raise ValueError("PPI timing must be deterministic")
            if record.interval.availability_rule != _AVAILABILITY_RULE:
                raise ValueError("PPI availability rule mismatch")
            if record.interval.published_at != published_at:
                raise ValueError(f"PPI {role} publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"PPI {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("PPI lock contains a post-decision input")
            if record.interval.valid_from != valid_from:
                raise ValueError(f"PPI {role} validity time mismatch")
            if record.interval.valid_to is not None or record.interval.revised_at is not None:
                raise ValueError("PPI selected release facts must remain open snapshots")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"PPI {role} source vintage mismatch")
            if record.source.retrieved_at != record.interval.ingested_at:
                raise ValueError("PPI retrieval and ingestion times must agree")
            if record.source.retrieved_at < published_at:
                raise ValueError("PPI retrieval cannot precede official release")
            if record.source.retrieved_at > self.build_epoch:
                raise ValueError("PPI retrieval cannot occur after build_epoch")
            for field, value in expected["critical_payload"].items():
                if record.payload.get(field) != value:
                    raise ValueError(f"PPI {role} {field} mismatch")
            if _hash(record.payload) != expected["payload_sha256"]:
                raise ValueError(f"PPI {role} payload hash mismatch")
            observed_response_hashes.update(
                (record.source.sha256, str(record.payload["release_html_sha256"]))
            )
        if observed_response_hashes != set(self.source_response_sha256s):
            raise ValueError("PPI record pairs do not bind all four response hashes")

        february = by_id[self.roles.february_release]
        march = by_id[self.roles.march_decision_release]
        if _change_bps(march) - _change_bps(february) != 40:
            raise ValueError("PPI inputs must establish the verified 40-basis-point increase")
        if march.payload["prior_month_change_tenths_percent"] != -6:
            raise ValueError("PPI March release must preserve the February headline")
        if march.payload["prior_month_revision_delta_tenths_percent"] != 0:
            raise ValueError("PPI adjacent release must retain a zero February revision")

        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match PPI input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> PPIBoundaryInputLock:
        """Normalize, validate, and self-hash a PPI input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_ppi_boundary_input_lock(path: Path) -> PPIBoundaryInputLock:
    try:
        return PPIBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid BLS PPI input lock: {path}") from error


def build_ppi_boundary_replay_spec(
    lock: PPIBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 PPI release boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = lock.source_response_sha256s
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(vault.records_as_of(lock.decision_time, source_ids=[BLS_PPI_SOURCE_ID]))
        manifest = vault.manifest(generated_at=lock.build_epoch)
    selected_by_id = {record.record_id: record for record in selected}
    if set(selected_by_id) != set(record_ids):
        raise ValueError("TimeVault did not reproduce the PPI record IDs")
    for record in records:
        if selected_by_id[record.record_id].model_dump(mode="json") != record.model_dump(
            mode="json"
        ):
            raise ValueError("TimeVault changed a locked PPI fact")

    prefix = lock.artifact_prefix
    february = by_role["february_release"]
    march = by_role["march_decision_release"]
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-pair-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="paired-html-pdf-bls-ppi-release-query",
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
            "decision_observations_basis_points": {
                "february_final_demand_monthly_change": _change_bps(february),
                "march_final_demand_monthly_change": _change_bps(march),
                "march_prior_february_change": int(
                    march.payload["prior_month_change_tenths_percent"]
                )
                * 10,
            },
            "html_pdf_crosscheck_verified": True,
            "adjacent_prior_value_crosscheck_verified": True,
            "revision_window_months": 4,
            "source_auxiliary_measures_used_as_range_input": False,
            "source_response_file_count": len(source_hashes),
        },
        limitations=(
            "The records are aggregate producer-price changes, not item-level transactions.",
            "Each snapshot was retrieved later but remains tied to an archived release pair.",
            "The April final-demand change is excluded from every ReplayPack input.",
            "Unadjusted indexes and 12-month changes do not set a FinReplay endpoint.",
            "PPI indexes are subject to revision four months after original publication.",
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
                "This aggregate PPI boundary requires TimeVault, ShockCompiler, TrialCourt, "
                "and ReplayStudio; no producer, establishment, product, security, order, "
                "portfolio, allocation, or return input is invented."
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
            "Four actual engines ran over two paired archived BLS PPI final-demand monthly-"
            "change releases. Reported changes remain reported; the -20-to-20-basis-point "
            "persistence-or-one-known-increase envelope remains inferred with no probability. "
            "The April event stays only in a disjoint event lock. This is not a BLS forecast, "
            "confidence interval, calibrated range, consumer-price measure, quantity, revenue, "
            "profit, transaction, causal model, deployment, external validation, investment "
            "result, or user-impact claim."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are March persistence or one repetition of the 40-basis-point rise.",
            "The April event and every later-known value are excluded from every input.",
            "The unadjusted index and 12-month change are metadata, not endpoint inputs.",
            "PPI measures seller prices received by domestic producers, not consumer costs.",
            "The COVID-19 text does not establish causality or unaffected measurement.",
            "No producer, product, transaction, market, portfolio, or recommendation exists.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: PPIBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    february = _change_bps(records_by_role["february_release"])
    march = _change_bps(records_by_role["march_decision_release"])
    known_increase = march - february
    if known_increase != 40:
        raise ValueError("PPI decision inputs must establish the verified 40-basis-point rise")
    lower = march
    upper = march + known_increase
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    variable = "next_final_demand_monthly_change_basis_points"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-final-demand-change-range",
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
            "Use March-change persistence or one repetition of the only known change between "
            "the February and March first-reported final-demand monthly changes."
        ),
        limitations=(
            "One adjacent-release change defines a stress range, not a forecast or probability.",
            "Unadjusted indexes and 12-month changes do not set either endpoint.",
            "The April event is absent from range construction.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-final-demand-change-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate March-change persistence or one repetition of the known 40-basis-point "
            "increase using only releases available at the boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, or regime meaning.",
            "The April final-demand change is evaluated only afterward.",
            "The aggregate price index is not a transaction, quantity, revenue, or profit.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, variable)
    initial_state = {state_key: float(march)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "february_change_basis_points": february,
        "march_change_basis_points": march,
        "known_increase_basis_points": known_increase,
        "lower_change_basis_points": lower,
        "upper_change_basis_points": upper,
        "range_width_basis_points": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.final-demand-change-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-bls-ppi-final-demand-change-program",
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
                variable: march,
                "definition": "persistence of the March final-demand monthly change",
            },
            "bound_construction": {
                "lower_change_basis_points": lower,
                "upper_change_basis_points": upper,
                "range_width_basis_points": width,
                "known_increase_basis_points": known_increase,
                "endpoint_method": "latest_change_persistence_or_repeat_one_known_increase",
                "original_release_values_only": True,
                "source_auxiliary_measures_used": False,
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
            "The April value is absent.",
            "The range is not an official interval, probability, causal model, or forecast.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: PPIBoundaryInputLock,
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
            "A retrospectively constructed one-change PPI boundary qualifies for research "
            "eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "PPI is an aggregate seller-price index. Two releases and one later outcome cannot "
            "establish predictive validity, price-setting mechanisms, pass-through, pandemic "
            "effects, consumer costs, or causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-change final-demand PPI range width in basis points",
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
        output_manifest_sha256=_hash({"ppi_change_range_width_basis_points": range_width}),
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
            "known-adjacent-release-increase": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No April PPI fact is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective PPI attempt must fail closed")
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
            claim_id="claim-reported-bls-ppi-final-demand-changes",
            statement=(
                "The March 12 and April 9 BLS PPI releases reported final-demand monthly "
                f"changes of {_format_bps(metrics['february_change_basis_points'])} and "
                f"{_format_bps(metrics['march_change_basis_points'])} percent."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary="These are aggregate seller-price release facts, not transactions.",
            limitations=(
                "PPI is not CPI, quantity, revenue, profit, or household expenditure.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-bls-ppi-change-range",
            statement=(
                "The next-release stress endpoints are March persistence or one repeat of the "
                "known 40-basis-point increase, from -20 to 20 basis points."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The two endpoints carry no probability or coverage claim.",
            limitations=("The April event did not set or widen the range.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-bls-ppi-trial-rejection",
            statement="TrialCourt rejected the retrospective one-change PPI attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is internal fail-closed evidence, not external method validation.",
            limitations=("No predictive or causal validity is inferred.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-bls-ppi-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not orders, capital, capacity, or returns.",
            limitations=("They exist only to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-bls-ppi-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this case.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _expected_records() -> dict[str, dict[str, Any]]:
    return {
        "february_release": {
            "published_at": datetime(2020, 3, 12, 12, 30, tzinfo=UTC),
            "valid_from": datetime(2020, 2, 1, tzinfo=UTC),
            "pdf_sha256": _FEBRUARY_PDF_SHA256,
            "pdf_url": "https://www.bls.gov/news.release/archives/ppi_03122020.pdf",
            "source_version": (
                "BLS-PPI:2020-02:USDL 20-0404:html:515855b318616035f7d4:"
                "pdf:392c9ee30d9deae5007a"
            ),
            "payload_sha256": "190f2445fbfefd2e5c790d3bae2d7c6fa7fb7837b5dd9f4426a54491a49d8212",
            "critical_payload": {
                "release_date": "2020-03-12",
                "reference_month": "2020-02",
                "release_number": "USDL 20-0404",
                "metric": "final_demand_monthly_change_seasonally_adjusted",
                "value_tenths_percent": -6,
                "value_basis_points": -60,
                "prior_month": "2020-01",
                "prior_month_change_tenths_percent": 5,
                "year_over_year_change_tenths_percent": 13,
                "table1_current_unadjusted_index": "118.6",
                "revision_window_months": 4,
                "release_html_sha256": _FEBRUARY_HTML_SHA256,
                "release_pdf_sha256": _FEBRUARY_PDF_SHA256,
                "release_pdf_pages": 32,
                "html_pdf_crosscheck_verified": True,
                "availability_method": "exact_bls_embargo_end_crosschecked_html_pdf",
                "unit": "Tenths of a Percent",
            },
        },
        "march_decision_release": {
            "published_at": _DECISION_TIME,
            "valid_from": datetime(2020, 3, 1, tzinfo=UTC),
            "pdf_sha256": _MARCH_PDF_SHA256,
            "pdf_url": "https://www.bls.gov/news.release/archives/ppi_04092020.pdf",
            "source_version": (
                "BLS-PPI:2020-03:USDL 20-0567:html:318dafbdf942ea9ac315:"
                "pdf:18540697b82c4cbb4270"
            ),
            "payload_sha256": "20af9f77f1aa24f24e770b85a07c78eacd078ab5aab3b6e438ab8564ad67652d",
            "critical_payload": {
                "release_date": "2020-04-09",
                "reference_month": "2020-03",
                "release_number": "USDL 20-0567",
                "metric": "final_demand_monthly_change_seasonally_adjusted",
                "value_tenths_percent": -2,
                "value_basis_points": -20,
                "prior_month": "2020-02",
                "prior_month_change_tenths_percent": -6,
                "prior_month_value_in_previous_release_tenths_percent": -6,
                "prior_month_revision_delta_tenths_percent": 0,
                "year_over_year_change_tenths_percent": 7,
                "table1_current_unadjusted_index": "118.5",
                "revision_window_months": 4,
                "release_html_sha256": _MARCH_HTML_SHA256,
                "release_pdf_sha256": _MARCH_PDF_SHA256,
                "release_pdf_pages": 31,
                "html_pdf_crosscheck_verified": True,
                "covid_methodology_statement_present": True,
                "availability_method": "exact_bls_embargo_end_crosschecked_html_pdf",
                "unit": "Tenths of a Percent",
            },
        },
    }


def _records_by_role(lock: PPIBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _change_bps(record: BitemporalRecord) -> int:
    value = record.payload.get("value_basis_points")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("PPI monthly change must be an integer number of basis points")
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
