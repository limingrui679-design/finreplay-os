"""March 2020 Federal Reserve H.4.1 liquidity-swap balance boundary."""

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

H41_LIQUIDITY_SWAPS_SOURCE_ID = "federal_reserve.h41.central_bank_liquidity_swaps"
H41_LIQUIDITY_SWAPS_INPUT_RESPONSE_SHA256S = tuple(
    sorted(
        (
            "d08360db4285e0db87257f5f72b6e6eff91e3f937e9da00de0bbffb62dc0a515",
            "b5dc44df02874ba2f4d112a95a04449c924e5da68ee977dcd3fae1ca812bf571",
            "a25a62443e7ee3bbda990ec2ef095624e1873c237819a81d1d17c6c7a2aef77e",
            "77157f38df055c43d46fb850d0534a5fd4836449df8067ed87612890f69b8819",
        )
    )
)
H41_LIQUIDITY_SWAPS_SUPPORTING_RECEIPT_SHA256 = (
    "312ef4c75191536fc8241076af9f42d7e55c90db8f47fdb91a38b11cab1b9580"
)

_ENTITY_ID = "federal_reserve_facility:central_bank_liquidity_swaps"
_DECISION_TIME = datetime(2020, 3, 26, 20, 30, tzinfo=UTC)
_AVAILABILITY_RULE = (
    "For March 19 and 26, the archived H.4.1 HTML explicitly states 'For Release at 4:30 "
    "P.M. EDT'; FinReplay validates that official stated time against America/New_York. "
    "The April 2 HTML identifies only its Thursday release date, so FinReplay waits until "
    "the following New York midnight. Every Table 1 fact is cross-checked against the "
    "complete official ASCII release. Neither method is represented as an independently "
    "measured server-publication log, and current retrieval metadata is never backdated."
)
_REDISTRIBUTION_NOTE = (
    "Keep full downloaded HTML and ASCII releases only in local content-addressed storage. "
    "Attribute the Board of Governors and preserve source links; repository scenarios retain "
    "only minimal reported facts and hashes."
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class H41LiquiditySwapsBoundaryRoles(_StrictModel):
    """The two H.4.1 Wednesday balances available at the decision boundary."""

    march18_release: str = Field(min_length=1, max_length=300)
    march25_decision_release: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> H41LiquiditySwapsBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("H.4.1 liquidity-swap role record IDs must be unique")
        return self


class H41LiquiditySwapsBoundaryInputLock(_StrictModel):
    """Content-addressed paired-format H.4.1 balances known at decision time."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: H41LiquiditySwapsBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=4, max_length=4)
    supporting_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(
        self,
        info: ValidationInfo,
    ) -> H41LiquiditySwapsBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.decision_time != _DECISION_TIME:
            raise ValueError("H.4.1 swap decision_time must equal the March 26 stated release")
        if self.build_epoch < self.decision_time:
            raise ValueError("H.4.1 swap build_epoch cannot precede decision_time")
        if self.source_response_sha256s != H41_LIQUIDITY_SWAPS_INPUT_RESPONSE_SHA256S:
            raise ValueError("H.4.1 swap source hashes do not match four official responses")
        if (
            self.supporting_receipt_sha256
            != H41_LIQUIDITY_SWAPS_SUPPORTING_RECEIPT_SHA256
        ):
            raise ValueError("H.4.1 swap supporting receipt hash mismatch")

        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("H.4.1 swap records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("H.4.1 swap roles must cover every locked record exactly once")
        by_id = {record.record_id: record for record in self.records}
        for role, expected in _expected_records().items():
            record = by_id[getattr(self.roles, role)]
            published_at = expected["published_at"]
            valid_from = expected["valid_from"]
            assert isinstance(published_at, datetime)
            assert isinstance(valid_from, datetime)
            if record.source.source_id != H41_LIQUIDITY_SWAPS_SOURCE_ID:
                raise ValueError("H.4.1 swap lock accepts only paired release facts")
            if record.source.publisher != "Board of Governors of the Federal Reserve System":
                raise ValueError("H.4.1 swap publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("H.4.1 swap inputs must retain versioned snapshots")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("H.4.1 swap source license boundary mismatch")
            if record.source.redistribution_note != _REDISTRIBUTION_NOTE:
                raise ValueError("H.4.1 swap redistribution boundary mismatch")
            if record.source.sha256 != expected["semantic_sha256"]:
                raise ValueError(f"H.4.1 swap {role} semantic hash mismatch")
            if str(record.source.url) != expected["url"]:
                raise ValueError(f"H.4.1 swap {role} source URL mismatch")
            if record.source.source_version != expected["source_version"]:
                raise ValueError(f"H.4.1 swap {role} source version mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("H.4.1 swap balances must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"H.4.1 swap {role} entity mismatch")
            if record.payload_schema_version != "1.0.0":
                raise ValueError(f"H.4.1 swap {role} payload schema mismatch")
            if record.interval.availability_confidence != 1.0:
                raise ValueError("H.4.1 swap timing must remain deterministic")
            if record.interval.availability_rule != _AVAILABILITY_RULE:
                raise ValueError("H.4.1 swap availability rule mismatch")
            if record.interval.published_at != published_at:
                raise ValueError(f"H.4.1 swap {role} publication boundary mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"H.4.1 swap {role} availability boundary mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("H.4.1 swap lock contains a post-decision input")
            if record.interval.valid_from != valid_from:
                raise ValueError(f"H.4.1 swap {role} validity time mismatch")
            if record.interval.valid_to is not None or record.interval.revised_at is not None:
                raise ValueError("H.4.1 swap selected facts must remain open snapshots")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"H.4.1 swap {role} source vintage mismatch")
            if record.source.retrieved_at != record.interval.ingested_at:
                raise ValueError("H.4.1 swap retrieval and ingestion times must agree")
            if record.source.retrieved_at < published_at:
                raise ValueError("H.4.1 swap retrieval cannot precede availability")
            if record.source.retrieved_at > self.build_epoch:
                raise ValueError("H.4.1 swap retrieval cannot occur after build_epoch")
            for field, expected_value in expected["critical_payload"].items():
                if record.payload.get(field) != expected_value:
                    raise ValueError(f"H.4.1 swap {role} {field} mismatch")
            if _hash(record.payload) != expected["payload_sha256"]:
                raise ValueError(f"H.4.1 swap {role} payload hash mismatch")

        earlier = by_id[self.roles.march18_release]
        decision = by_id[self.roles.march25_decision_release]
        if _balance(decision) - _balance(earlier) != 206_006:
            raise ValueError("H.4.1 inputs must establish the 206,006-million-dollar increase")

        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match H.4.1 swap input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> H41LiquiditySwapsBoundaryInputLock:
        """Normalize, validate, and self-hash an H.4.1 swap input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_h41_liquidity_swaps_boundary_input_lock(
    path: Path,
) -> H41LiquiditySwapsBoundaryInputLock:
    try:
        return H41LiquiditySwapsBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid H.4.1 liquidity-swap input lock: {path}") from error


def build_h41_liquidity_swaps_boundary_replay_spec(
    lock: H41LiquiditySwapsBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 liquidity-swap boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = lock.source_response_sha256s
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                source_ids=[H41_LIQUIDITY_SWAPS_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    selected_by_id = {record.record_id: record for record in selected}
    if set(selected_by_id) != set(record_ids):
        raise ValueError("TimeVault did not reproduce the H.4.1 swap record IDs")
    for record in records:
        if selected_by_id[record.record_id].model_dump(mode="json") != record.model_dump(
            mode="json"
        ):
            raise ValueError("TimeVault changed a locked H.4.1 swap fact")

    earlier = by_role["march18_release"]
    decision = by_role["march25_decision_release"]
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-pair-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="paired-html-ascii-h41-liquidity-swap-query",
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
            "decision_observations_millions": {
                "march18_wednesday_outstanding": _balance(earlier),
                "march25_wednesday_outstanding": _balance(decision),
                "known_wednesday_increase": _balance(decision) - _balance(earlier),
            },
            "html_ascii_crosscheck_verified": True,
            "swap_exchange_rate_measurement_boundary_retained": True,
            "actual_server_publication_log_available": False,
            "weekly_average_fields_used_as_range_input": False,
            "source_response_file_count": len(source_hashes),
        },
        limitations=(
            "The release time is official stated timing, not an observed server log.",
            "Only Wednesday aggregate balances set the range; weekly averages do not.",
            "The H.4.1 exchange-rate convention is not current-market exposure or P&L.",
            "The April 1 balance is excluded from every ReplayPack input.",
            "Two weekly balances cannot establish forecast skill or causality.",
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
                "This aggregate balance boundary requires TimeVault, ShockCompiler, TrialCourt, "
                "and ReplayStudio; no central-bank counterparty, transaction, market exposure, "
                "execution, portfolio, return, or user input is invented."
            ),
        },
        limitations=("Static rendering does not validate the one-increase range heuristic.",),
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
            "Four actual engines ran over two paired archived Federal Reserve H.4.1 Table 1 "
            "Wednesday central-bank-liquidity-swap balances. Reported balances remain reported; "
            "the 206,051-to-412,057-million-dollar persistence-or-repeat-increase range remains "
            "inferred with no probability. The April 1 event stays only in a disjoint event "
            "lock. The March release times are official stated times, not server logs. This is "
            "not a Federal Reserve forecast, calibrated interval, current-market exposure, "
            "institution-level allocation, transaction record, policy-effectiveness result, "
            "causal model, deployment, external validation, investment result, or user impact."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are March 25 persistence or one repetition of one known increase.",
            "The April 1 event and every later-known value are excluded from every input.",
            "Weekly averages and year-over-year changes do not set either endpoint.",
            "The reported exchange-rate convention is not current-market risk or P&L.",
            "No probability, coverage, causal, policy, or performance claim is made.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: H41LiquiditySwapsBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    earlier = _balance(records_by_role["march18_release"])
    latest = _balance(records_by_role["march25_decision_release"])
    known_increase = latest - earlier
    if known_increase != 206_006:
        raise ValueError("H.4.1 decision inputs must establish the verified increase")
    lower = latest
    upper = latest + known_increase
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    variable = "next_wednesday_liquidity_swaps_outstanding_million_dollars"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-balance-range",
        target_id=_ENTITY_ID,
        variable=variable,
        unit="million_dollars",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use March 25 Wednesday-balance persistence or one repetition of the only known "
            "increase from March 18 to March 25."
        ),
        limitations=(
            "One adjacent weekly increase defines a stress range, not a forecast or probability.",
            "Weekly averages and year-over-year changes set neither endpoint.",
            "The April 1 event is absent from range construction.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-balance-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate Wednesday-balance persistence or one repetition of the known weekly "
            "increase using only paired releases available at the boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, or regime meaning.",
            "The April 1 balance is evaluated only afterward.",
            "A balance-sheet line is not transaction-level usage or current-market exposure.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, variable)
    initial_state = {state_key: float(latest)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "march18_balance_million_dollars": earlier,
        "march25_balance_million_dollars": latest,
        "known_increase_million_dollars": known_increase,
        "lower_level_million_dollars": lower,
        "upper_level_million_dollars": upper,
        "range_width_million_dollars": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.balance-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-h41-liquidity-swap-balance-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_balance_levels": metrics,
            "naive_baseline": {
                variable: latest,
                "definition": "persistence of the March 25 Wednesday balance",
            },
            "bound_construction": {
                "lower_level_million_dollars": lower,
                "upper_level_million_dollars": upper,
                "range_width_million_dollars": width,
                "known_increase_million_dollars": known_increase,
                "endpoint_method": "latest_level_persistence_or_repeat_one_known_increase",
                "wednesday_balance_only": True,
                "weekly_average_used": False,
                "year_change_used": False,
                "current_market_revaluation_performed": False,
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
            "The endpoints mechanically reuse one reported weekly increase.",
            "The April 1 value is absent.",
            "The range is not an official interval, probability, causal model, or forecast.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: H41LiquiditySwapsBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    range_width: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=14)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-range-screen",
        hypothesis=(
            "A retrospectively constructed one-increase H.4.1 swap-balance boundary qualifies "
            "for research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "The H.4.1 line is an aggregate weekly balance under a specified exchange-rate "
            "convention. Two inputs and one later outcome cannot establish predictive validity, "
            "policy effectiveness, market impact, or causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-increase H.4.1 liquidity-swap range width in millions",
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
        output_manifest_sha256=_hash({"swap_balance_range_width_millions": range_width}),
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
            "known-weekly-increase": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No April 1 H.4.1 fact is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective H.4.1 swap attempt must fail closed")
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
            claim_id="claim-reported-h41-liquidity-swap-balances",
            statement=(
                "The paired H.4.1 releases report Wednesday central-bank-liquidity-swap "
                f"balances of ${metrics['march18_balance_million_dollars']:,} million on "
                f"March 18 and ${metrics['march25_balance_million_dollars']:,} million on "
                "March 25, 2020."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary="These are aggregate balance-sheet facts under H.4.1's stated convention.",
            limitations=(
                "They are not transactions, current-market exposure, P&L, or counterparty loss.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-h41-liquidity-swap-range",
            statement=(
                "The next-Wednesday stress endpoints are March 25 persistence or one repeat "
                f"of the known ${metrics['known_increase_million_dollars']:,} million increase: "
                f"[${metrics['lower_level_million_dollars']:,}, "
                f"${metrics['upper_level_million_dollars']:,}] million."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability, coverage, official, or causal guarantee.",
            limitations=("The April 1 event was not used to set the range.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-h41-liquidity-swap-trial-rejection",
            statement="TrialCourt rejected the retrospective one-increase H.4.1 attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external or domain review.",
            limitations=("The attempt makes no statistical, policy, or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-h41-liquidity-swap-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not transactions, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-h41-liquidity-swap-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not policy correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _expected_records() -> dict[str, dict[str, Any]]:
    common_payload: dict[str, object] = {
        "actual_server_publication_log_available": False,
        "html_ascii_crosscheck_verified": True,
        "measurement_boundary": (
            "Dollar value of foreign currency held under swap agreements, valued at the "
            "exchange rate used when acquired and to be used when returned to the foreign "
            "central bank."
        ),
        "metric": "wednesday_outstanding",
        "program": "Central bank liquidity swaps",
        "release_series": "H.4.1 Factors Affecting Reserve Balances",
        "release_time_local": "16:30:00",
        "release_timezone": "America/New_York",
        "release_timezone_abbreviation": "EDT",
        "table": "H.4.1 Table 1",
        "unit": "Millions of Dollars",
        "availability_method": "exact_official_stated_time_crosschecked_html_ascii",
    }
    return {
        "march18_release": {
            "published_at": datetime(2020, 3, 19, 20, 30, tzinfo=UTC),
            "valid_from": datetime(2020, 3, 18, tzinfo=UTC),
            "semantic_sha256": (
                "8261da1e27e2ed08ab3671af4b94c394108e7809a256638d0a7332f8ed60519b"
            ),
            "url": "https://www.federalreserve.gov/releases/h41/20200319/h41.htm",
            "source_version": "H41-SWAPS:2020-03-19:semantic:8261da1e27e2ed08ab3671af",
            "payload_sha256": (
                "11af944c9bdcd7c871d42fcf8015ccfb86af4c34ed9a9d807164ae3f2fc6c6fd"
            ),
            "critical_payload": {
                **common_payload,
                "release_date": "2020-03-19",
                "week_ending": "2020-03-18",
                "value_millions": 45,
                "weekly_average_millions": 45,
                "weekly_average_change_from_prior_week_millions": -13,
                "weekly_average_change_from_year_ago_millions": -23,
                "release_semantic_sha256": (
                    "8261da1e27e2ed08ab3671af4b94c394108e7809a256638d0a7332f8ed60519b"
                ),
                "official_stated_release_at": "2020-03-19T20:30:00+00:00",
                "conservative_available_at": "2020-03-19T20:30:00+00:00",
            },
        },
        "march25_decision_release": {
            "published_at": _DECISION_TIME,
            "valid_from": datetime(2020, 3, 25, tzinfo=UTC),
            "semantic_sha256": (
                "90221fc89c30bf797806200eb6bc725f976ca314d5f9a098c587143d2fc6d540"
            ),
            "url": "https://www.federalreserve.gov/releases/h41/20200326/h41.htm",
            "source_version": "H41-SWAPS:2020-03-26:semantic:90221fc89c30bf797806200e",
            "payload_sha256": (
                "a91fa0b0f23319500982c24233b72d4b9ea6994e91fb354e1588d3fa6cb903ba"
            ),
            "critical_payload": {
                **common_payload,
                "release_date": "2020-03-26",
                "week_ending": "2020-03-25",
                "value_millions": 206_051,
                "weekly_average_millions": 168_814,
                "weekly_average_change_from_prior_week_millions": 168_769,
                "weekly_average_change_from_year_ago_millions": 168_748,
                "release_semantic_sha256": (
                    "90221fc89c30bf797806200eb6bc725f976ca314d5f9a098c587143d2fc6d540"
                ),
                "official_stated_release_at": "2020-03-26T20:30:00+00:00",
                "conservative_available_at": "2020-03-26T20:30:00+00:00",
            },
        },
    }


def _records_by_role(
    lock: H41LiquiditySwapsBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _balance(record: BitemporalRecord) -> int:
    value = record.payload.get("value_millions")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("H.4.1 liquidity-swap balance must be integer millions")
    if not 1 <= value <= 10_000_000:
        raise ValueError("H.4.1 liquidity-swap balance is outside the supported range")
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
