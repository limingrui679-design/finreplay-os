"""March 2020 EIA Lower 48 working-gas stock boundary."""

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

EIA_WNGSR_SOURCE_ID = "eia.wngsr.revision_safe_working_gas"
_ENTITY_ID = "eia_series:wngsr_working_gas_lower_48"
_DECISION_TIME = datetime(2020, 3, 19, 14, 30, tzinfo=UTC)
_REVISIONS_SHA256 = "ee7c703c6d30176d0253b879aa4c8c6dc0178b411c36d73036d89aeff412dd3c"
_HISTORY_SHA256 = "7973c8f5721c1addb2f8df496134aa0697a98f1f4eb9b075223f19f12f513b18"
_EVALUATION_SHA256 = "de3123137bf3d5055181aa709e522caec0afe301a1077fca79a886ee5249536b"
_SOURCE_HASHES = tuple(sorted((_REVISIONS_SHA256, _HISTORY_SHA256, _EVALUATION_SHA256)))
_AVAILABILITY_RULE = (
    "The EIA 2020-22 WNGSR performance evaluation states that WNGSR is released each "
    "Thursday at 10:30 a.m. Eastern, that every 2020-22 release met the established "
    "schedule, and that the first remote-posture release was March 19, 2020 without "
    "publication disruption. The selected March 12, 19, and 26 non-holiday Thursdays are "
    "therefore eligible at 10:30 America/New_York. Current response headers are retrieval "
    "metadata only and are never backdated."
)
_REDISTRIBUTION_NOTE = (
    "EIA government data are reusable with acknowledgment, but complete XLS and PDF "
    "responses remain in local content-addressed storage. The repository retains only "
    "minimal selected facts, URLs, hashes, source semantics, and release timing."
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkingGasStockBoundaryRoles(_StrictModel):
    """The two original pre-decision weekly stock releases."""

    march06_release: str = Field(min_length=1, max_length=300)
    march13_decision_release: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> WorkingGasStockBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("EIA WNGSR role record IDs must be unique")
        return self


class WorkingGasStockBoundaryInputLock(_StrictModel):
    """Content-addressed original WNGSR records known at the decision time."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: WorkingGasStockBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=3, max_length=3)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> WorkingGasStockBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.decision_time != _DECISION_TIME:
            raise ValueError("WNGSR decision_time must equal the March 19 release time")
        if self.build_epoch < self.decision_time:
            raise ValueError("WNGSR build_epoch cannot precede decision_time")
        if self.source_response_sha256s != _SOURCE_HASHES:
            raise ValueError("WNGSR source hash set does not match the three official responses")

        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("WNGSR records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("WNGSR roles must cover every locked record exactly once")
        by_id = {record.record_id: record for record in self.records}
        for role, expected in _expected_records().items():
            record = by_id[getattr(self.roles, role)]
            published_at = expected["published_at"]
            valid_from = expected["valid_from"]
            assert isinstance(published_at, datetime)
            assert isinstance(valid_from, datetime)
            if record.source.source_id != EIA_WNGSR_SOURCE_ID:
                raise ValueError("WNGSR lock accepts only revision-safe working-gas facts")
            if record.source.publisher != "U.S. Energy Information Administration":
                raise ValueError("WNGSR source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VINTAGE_NATIVE:
                raise ValueError("WNGSR inputs must retain native original-vintage semantics")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("WNGSR source license boundary mismatch")
            if record.source.redistribution_note != _REDISTRIBUTION_NOTE:
                raise ValueError("WNGSR redistribution boundary mismatch")
            if record.source.sha256 != _REVISIONS_SHA256:
                raise ValueError("WNGSR primary revisions-workbook hash mismatch")
            if str(record.source.url) != "https://ir.eia.gov/ngs/revisions.xls":
                raise ValueError("WNGSR primary source URL mismatch")
            if record.source.source_version != expected["source_version"]:
                raise ValueError(f"WNGSR {role} source version mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("WNGSR stocks must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"WNGSR {role} entity mismatch")
            if record.payload_schema_version != "1.0.0":
                raise ValueError(f"WNGSR {role} payload schema mismatch")
            if record.interval.availability_confidence != 1.0:
                raise ValueError("WNGSR timing must be deterministic")
            if record.interval.availability_rule != _AVAILABILITY_RULE:
                raise ValueError("WNGSR availability rule mismatch")
            if record.interval.published_at != published_at:
                raise ValueError(f"WNGSR {role} publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"WNGSR {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("WNGSR lock contains a post-decision input")
            if record.interval.valid_from != valid_from:
                raise ValueError(f"WNGSR {role} inventory validity time mismatch")
            if record.interval.valid_to is not None or record.interval.revised_at is not None:
                raise ValueError("WNGSR selected originals must remain unrevised open facts")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"WNGSR {role} source vintage mismatch")
            if record.source.retrieved_at != record.interval.ingested_at:
                raise ValueError("WNGSR retrieval and ingestion times must agree")
            if record.source.retrieved_at < published_at:
                raise ValueError("WNGSR retrieval cannot precede official release")
            if record.source.retrieved_at > self.build_epoch:
                raise ValueError("WNGSR retrieval cannot occur after build_epoch")
            for field, value in expected["critical_payload"].items():
                if record.payload.get(field) != value:
                    raise ValueError(f"WNGSR {role} {field} mismatch")
            if _hash(record.payload) != expected["payload_sha256"]:
                raise ValueError(f"WNGSR {role} payload hash mismatch")
            if _stock(record) != expected["value_bcf"]:
                raise ValueError(f"WNGSR {role} stock level mismatch")

        march06 = by_id[self.roles.march06_release]
        march13 = by_id[self.roles.march13_decision_release]
        if _stock(march06) - _stock(march13) != 9:
            raise ValueError("WNGSR decision records must establish the verified 9 Bcf decline")
        if march13.payload["prior_value_bcf"] != _stock(march06):
            raise ValueError("WNGSR March 13 prior stock must match the March 6 original")

        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match WNGSR input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> WorkingGasStockBoundaryInputLock:
        """Normalize, validate, and self-hash a WNGSR input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_working_gas_stock_boundary_input_lock(
    path: Path,
) -> WorkingGasStockBoundaryInputLock:
    try:
        return WorkingGasStockBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid EIA WNGSR input lock: {path}") from error


def build_working_gas_stock_boundary_replay_spec(
    lock: WorkingGasStockBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 working-gas boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = lock.source_response_sha256s
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(vault.records_as_of(lock.decision_time, source_ids=[EIA_WNGSR_SOURCE_ID]))
        manifest = vault.manifest(generated_at=lock.build_epoch)
    selected_by_id = {record.record_id: record for record in selected}
    if set(selected_by_id) != set(record_ids):
        raise ValueError("TimeVault did not reproduce the WNGSR record IDs")
    for record in records:
        if selected_by_id[record.record_id].model_dump(mode="json") != record.model_dump(
            mode="json"
        ):
            raise ValueError("TimeVault changed a locked WNGSR fact")

    prefix = lock.artifact_prefix
    march06 = by_role["march06_release"]
    march13 = by_role["march13_decision_release"]
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.original-vintage-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="vintage-native-eia-wngsr-original-stock-query",
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
            "decision_observations": {
                "march06_lower48_working_gas_bcf": _stock(march06),
                "march13_lower48_working_gas_bcf": _stock(march13),
                "march13_reported_net_change_bcf": march13.payload[
                    "reported_net_change_bcf"
                ],
            },
            "original_value_recovery_verified": True,
            "current_history_cross_check_verified": True,
            "release_time_rule": "10:30 America/New_York on verified non-holiday Thursdays",
            "source_statistical_measures_used_as_range_input": False,
            "source_evidence_file_count": len(source_hashes),
        },
        limitations=(
            "The records are Lower 48 estimates from a sample survey, not facility measurements.",
            "The consolidated workbooks were retrieved later but explicitly recover originals.",
            "The March 20 stock is excluded from every ReplayPack input.",
            "Regional sums may differ by up to 2 Bcf because EIA rounds reported totals.",
            "Complete source workbooks and the evaluation PDF remain local download evidence.",
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
        range_width=metrics["range_width_bcf"],
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
                "This aggregate working-gas boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no operator, reservoir, pipeline, position, "
                "order, execution, portfolio, or allocation input is invented."
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
            "Four actual engines ran over two original EIA WNGSR Lower 48 stock estimates "
            "recovered from the official revision-safe archive and cross-checked against the "
            "current history and 2020-22 performance evaluation. Reported stocks remain "
            "reported; the 2,025-to-2,034 Bcf persistence-or-one-decline envelope remains "
            "inferred with no probability. The March 20 event stays only in a disjoint event "
            "lock. This is not an EIA forecast, sampling interval, calibrated interval, storage "
            "capacity estimate, injection or withdrawal measurement, market signal, causal "
            "model, deployment, external validation, investment result, or user-impact claim."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are March 13 persistence or one repetition of the 9 Bcf decline.",
            "The March 20 release and every later-known value are excluded from every input.",
            "The source's CV and standard error are metadata, not endpoint inputs.",
            "Stock changes include injections, withdrawals, and possible reclassifications.",
            "No facility, operator, reservoir, pipeline, price, return, or recommendation exists.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: WorkingGasStockBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    march06 = _stock(records_by_role["march06_release"])
    march13 = _stock(records_by_role["march13_decision_release"])
    known_decline = march06 - march13
    if known_decline != 9:
        raise ValueError("WNGSR decision inputs must establish the verified 9 Bcf decline")
    lower = march13 - known_decline
    upper = march13
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    variable = "next_lower_48_working_gas_stock_bcf"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-working-gas-stock-range",
        target_id=_ENTITY_ID,
        variable=variable,
        unit="billion_cubic_feet",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use March 13 stock persistence or one repetition of the only known original-stock "
            "decline between the March 6 and March 13 releases."
        ),
        limitations=(
            "One observed decline defines a stress range, not a forecast or probability.",
            "The source CV and weekly-net-change standard error do not set either endpoint.",
            "The March 20 event is absent from range construction.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-working-gas-stock-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate March 13 stock persistence or one repetition of the known 9 Bcf decline "
            "using only original values available at the boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, or regime meaning.",
            "The March 20 stock is evaluated only afterward.",
            "The aggregate estimate is not a facility, flow, transaction, or capacity dataset.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, variable)
    initial_state = {state_key: float(march13)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "march06_working_gas_bcf": march06,
        "march13_working_gas_bcf": march13,
        "known_decline_bcf": known_decline,
        "lower_stock_bcf": lower,
        "upper_stock_bcf": upper,
        "range_width_bcf": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.working-gas-stock-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-eia-wngsr-working-gas-stock-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_original_stock_levels": metrics,
            "naive_baseline": {
                variable: march13,
                "definition": "persistence of the March 13 Lower 48 working-gas stock",
            },
            "bound_construction": {
                "lower_stock_bcf": lower,
                "upper_stock_bcf": upper,
                "range_width_bcf": width,
                "known_decline_bcf": known_decline,
                "endpoint_method": "latest_stock_persistence_or_repeat_one_known_decline",
                "original_vintage_values_only": True,
                "source_statistical_measures_used": False,
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
            "The endpoints mechanically reuse one reported stock decline.",
            "The March 20 value is absent.",
            "The range is not an official interval, probability, causal model, or forecast.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: WorkingGasStockBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    range_width: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=28)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-range-screen",
        hypothesis=(
            "A retrospectively constructed one-decline WNGSR stock boundary qualifies for "
            "research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "WNGSR is a sampled aggregate stock estimate. Two releases and one later outcome "
            "cannot establish predictive validity, storage constraints, flows, market response, "
            "pandemic effects, or causal mechanisms."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-decline working-gas stock range width in Bcf",
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
        output_manifest_sha256=_hash({"working_gas_stock_range_width_bcf": range_width}),
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
            "known-original-stock-decline": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No March 20 WNGSR fact is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective WNGSR attempt must fail closed")
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
            claim_id="claim-reported-eia-wngsr-original-stocks",
            statement=(
                "The March 12 and March 19 WNGSR releases originally reported Lower 48 "
                f"working-gas stocks of {metrics['march06_working_gas_bcf']:,} and "
                f"{metrics['march13_working_gas_bcf']:,} Bcf."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary="The current history must match both revision-safe original values.",
            limitations=(
                "The values are sampled aggregate estimates, not facility or flow measurements.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-eia-wngsr-stock-range",
            statement=(
                "The next-release stress endpoints are March 13 persistence or one repeat of "
                f"the known {metrics['known_decline_bcf']}-Bcf decline: "
                f"[{metrics['lower_stock_bcf']:,}, {metrics['upper_stock_bcf']:,}] Bcf."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability, coverage, official, or causal guarantee.",
            limitations=(
                "The March 20 event was not used to set the range.",
                "The source CV and standard error do not set the endpoints.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-eia-wngsr-trial-rejection",
            statement="TrialCourt rejected the retrospective one-decline WNGSR attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external or domain review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-eia-wngsr-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not transactions, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-eia-wngsr-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary=(
                "Artifact presence proves integration structure, not energy-market correctness."
            ),
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _expected_records() -> dict[str, dict[str, Any]]:
    common = {
        "metric": "working_gas_in_underground_storage_lower_48",
        "unit": "Billion Cubic Feet",
        "source_form": "EIA-912",
        "release_time_local": "10:30:00",
        "release_timezone": "America/New_York",
        "release_timezone_abbreviation": "EDT",
        "coefficient_of_variation_percent_lower_48": "0.5",
        "statistical_measures_define_finreplay_range": False,
        "published_revision_or_reclassification_note": None,
        "current_history_matches_original_estimate": True,
        "revision_history_semantics": (
            "original estimate before any published revision or reclassification"
        ),
        "revisions_workbook_url": "https://ir.eia.gov/ngs/revisions.xls",
        "revisions_workbook_sha256": _REVISIONS_SHA256,
        "history_workbook_url": "https://ir.eia.gov/ngs/ngshistory.xls",
        "history_workbook_sha256": _HISTORY_SHA256,
        "performance_evaluation_url": "https://ir.eia.gov/ngs/wngsrevaluation_2024.pdf",
        "performance_evaluation_sha256": _EVALUATION_SHA256,
        "performance_evaluation_pages": 24,
        "availability_method": (
            "official_standard_release_time_plus_2020_schedule-performance proof"
        ),
    }
    return {
        "march06_release": {
            "published_at": datetime(2020, 3, 12, 14, 30, tzinfo=UTC),
            "valid_from": datetime(2020, 3, 6, 15, 0, tzinfo=UTC),
            "source_version": (
                "EIA-WNGSR:2020-03-12:2020-03-06:rev:ee7c703c6d30176d0253:"
                "hist:7973c8f5721c1addb2f8:eval:de3123137bf3d5055181"
            ),
            "payload_sha256": (
                "6797c2d596e1868daeaa5fae69fde17a2c24b2b7003fdce9cf83c30c346006e3"
            ),
            "value_bcf": 2_043,
            "critical_payload": {
                **common,
                "release_date": "2020-03-12",
                "week_ending": "2020-03-06",
                "prior_week_ending": "2020-02-28",
                "value_bcf": 2_043,
                "prior_value_bcf": 2_091,
                "reported_net_change_bcf": -48,
                "net_change_standard_error_bcf_lower_48": "0.6",
                "official_release_at": "2020-03-12T14:30:00+00:00",
                "inventory_as_of_local": "2020-03-06T09:00:00-06:00",
                "five_region_rounding_difference_bcf": 0,
                "south_central_subregion_rounding_difference_bcf": 0,
            },
        },
        "march13_decision_release": {
            "published_at": _DECISION_TIME,
            "valid_from": datetime(2020, 3, 13, 14, 0, tzinfo=UTC),
            "source_version": (
                "EIA-WNGSR:2020-03-19:2020-03-13:rev:ee7c703c6d30176d0253:"
                "hist:7973c8f5721c1addb2f8:eval:de3123137bf3d5055181"
            ),
            "payload_sha256": (
                "ac9817b3dbd54e06873c5d0d8d871be5f3b16e244bbcc337c934b3d450eef89d"
            ),
            "value_bcf": 2_034,
            "critical_payload": {
                **common,
                "release_date": "2020-03-19",
                "week_ending": "2020-03-13",
                "prior_week_ending": "2020-03-06",
                "value_bcf": 2_034,
                "prior_value_bcf": 2_043,
                "reported_net_change_bcf": -9,
                "net_change_standard_error_bcf_lower_48": "0.8",
                "official_release_at": "2020-03-19T14:30:00+00:00",
                "inventory_as_of_local": "2020-03-13T09:00:00-05:00",
                "five_region_rounding_difference_bcf": -1,
                "south_central_subregion_rounding_difference_bcf": 1,
            },
        },
    }


def _records_by_role(
    lock: WorkingGasStockBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _stock(record: BitemporalRecord) -> int:
    value = record.payload["value_bcf"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("WNGSR stock must be integer Bcf")
    if not 1 <= value <= 10_000:
        raise ValueError("WNGSR stock is outside the supported range")
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
