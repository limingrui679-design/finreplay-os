"""GDP revision-boundary replay over native ALFRED historical vintages."""

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

ALFRED_GDP_SOURCE_ID = "fred.alfred.vintage_gdp"
GDP_SERIES_ID = "GDP"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MacroRevisionRoles(_StrictModel):
    """Stable analytical roles for four decision-time vintage facts."""

    q3_advance: str = Field(min_length=1, max_length=300)
    q3_second: str = Field(min_length=1, max_length=300)
    q3_predecision: str = Field(min_length=1, max_length=300)
    q4_advance: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> MacroRevisionRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("macro revision role record IDs must be unique")
        return self


class MacroRevisionInputLock(_StrictModel):
    """Content-addressed ALFRED decision inputs for one GDP revision boundary."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    series_id: Literal["GDP"] = "GDP"
    roles: MacroRevisionRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=3, max_length=3)
    records: tuple[BitemporalRecord, ...] = Field(min_length=4, max_length=4)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> MacroRevisionInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("macro revision build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("macro revision records must be unique and sorted")
        role_ids = tuple(self.roles.model_dump().values())
        if set(record_ids) != set(role_ids):
            raise ValueError("macro revision roles must cover every locked record exactly once")
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("macro revision source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("macro revision source hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected = {
            "q3_advance": ("2022-10-27", "2022-07-01"),
            "q3_second": ("2022-11-30", "2022-07-01"),
            "q3_predecision": ("2023-01-26", "2022-07-01"),
            "q4_advance": ("2023-01-26", "2022-10-01"),
        }
        for role, (vintage, observation) in expected.items():
            record = by_id[getattr(self.roles, role)]
            if record.source.source_id != ALFRED_GDP_SOURCE_ID:
                raise ValueError("macro revision lock accepts only ALFRED GDP records")
            if record.source.temporal_coverage is not TemporalCoverage.VINTAGE_NATIVE:
                raise ValueError("macro revision inputs must be native-vintage evidence")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("ALFRED GDP estimates must remain reported evidence")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("macro revision timing must use a deterministic safe bound")
            if record.interval.available_at > self.decision_time:
                raise ValueError("macro revision lock contains a post-decision input")
            if record.payload.get("series_id") != self.series_id:
                raise ValueError("macro revision record series_id mismatch")
            if record.payload.get("vintage_date") != vintage:
                raise ValueError(f"macro revision {role} vintage mismatch")
            if record.payload.get("observation_date") != observation:
                raise ValueError(f"macro revision {role} observation mismatch")
            if record.payload.get("unit") != "Billions of Dollars":
                raise ValueError("macro revision GDP unit mismatch")
            _positive_decimal(record.payload.get("value"), f"macro revision {role} value")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match macro revision input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> MacroRevisionInputLock:
        """Normalize, validate, and self-hash a macro revision input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_macro_revision_input_lock(path: Path) -> MacroRevisionInputLock:
    try:
        return MacroRevisionInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid macro revision input lock: {path}") from error


def build_macro_revision_replay_spec(
    lock: MacroRevisionInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run the four engines relevant to a vintage-revision decision boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    max_available_at = max(record.interval.available_at for record in records)
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                source_ids=[ALFRED_GDP_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the macro revision input lock")

    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.vintage-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="native-vintage-query",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.REPORTED: len(selected)},
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
            "max_available_at": _canonical_datetime(max_available_at),
            "decision_time": _canonical_datetime(lock.decision_time),
            "availability_interpretation": (
                "Each ALFRED vintage uses the adapter's two-calendar-day conservative knowledge "
                "bound; no intraday release time is claimed."
            ),
        },
        limitations=(
            "The input lock contains four selected GDP vintage facts, not the complete series.",
            "Date-granular vintages use a conservative knowledge bound, not an exact release "
            "timestamp.",
        ),
    )
    shock_artifact, revision_metrics = _run_shockcompiler(
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
        revision_bound=revision_metrics["bound"],
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
                "TimeVault, ShockCompiler, TrialCourt, and ReplayStudio are the four engines "
                "relevant to this macro-vintage boundary; no trading or network engine is "
                "invented for padding."
            ),
        },
        limitations=(
            "Static report generation does not independently validate the revision heuristic.",
        ),
    )
    artifacts = (
        timevault_artifact,
        shock_artifact,
        trial_artifact,
        replaystudio_artifact,
    )
    compressed_input_bytes = len(
        gzip.compress(_canonical_json(lock.model_dump(mode="json")).encode(), mtime=0)
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
        claims=_claims(lock, by_role, artifacts, revision_metrics),
        require_all_engines=False,
        distinct_input_records=len(records),
        derived_records=(
            int(trial_artifact.payload["manifest"]["entries"])
            + len(shock_artifact.payload["compiled"]["trials"])
            + 1
        ),
        compressed_input_bytes=compressed_input_bytes,
        elapsed_seconds=0.0,
        claim_boundary=(
            "Four actual engine implementations ran over four locked native-vintage ALFRED GDP "
            "facts available before the historical decision time. Reported estimates remain "
            "reported; revision deltas and the symmetric boundary are arithmetic inferences; "
            "TrialCourt rejects retrospective promotion. The later Q4 second estimate is held "
            "only in a disjoint post-decision event lock and is not a ReplayPack input. This is "
            "not a forecast, probability distribution, causal model, trading signal, policy "
            "recommendation, production deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The symmetric Q4 revision interval is a transparent heuristic based only on the "
            "known Q3 cumulative revision magnitude; it has no assigned probability or coverage "
            "guarantee.",
            "The later Q4 second estimate is excluded from decision inputs and all artifacts.",
            "ALFRED vintage dates are date-granular, so the adapter delays knowability by two "
            "calendar days instead of claiming exact publication time.",
            "No market data, portfolio, order, network contagion, or execution claim is made.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: MacroRevisionInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, Decimal]]:
    q3_advance = _value(records_by_role["q3_advance"])
    q3_second = _value(records_by_role["q3_second"])
    q3_predecision = _value(records_by_role["q3_predecision"])
    q4_advance = _value(records_by_role["q4_advance"])
    advance_to_second = q3_second - q3_advance
    second_to_predecision = q3_predecision - q3_second
    cumulative = q3_predecision - q3_advance
    bound = abs(cumulative)
    if bound <= 0:
        raise ValueError("known Q3 cumulative revision must produce a positive boundary")
    bound_record_ids = tuple(
        sorted(
            (
                records_by_role["q3_advance"].record_id,
                records_by_role["q3_predecision"].record_id,
                records_by_role["q4_advance"].record_id,
            )
        )
    )
    unique_sources = {
        record.source.sha256: record.source
        for record in (
            records_by_role["q3_advance"],
            records_by_role["q3_predecision"],
            records_by_role["q4_advance"],
        )
    }
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-q4-revision-bound",
        target_id="bea:gdp:2022q4",
        variable="revision_billions",
        unit="billions_usd_saar",
        operation=ShockOperation.SET,
        lower=-float(bound),
        upper=float(bound),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=bound_record_ids,
        sources=tuple(unique_sources[key] for key in sorted(unique_sources)),
        derivation=(
            "Use the absolute Q3 advance-to-predecision GDP revision as a symmetric endpoint "
            "magnitude around a zero-revision Q4 naive baseline. The Q4 second estimate is not "
            "an input and no probability is assigned."
        ),
        limitations=(
            "A prior-quarter revision magnitude is a transparent heuristic, not a forecast or "
            "coverage guarantee.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-q4-revision-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate a no-probability Q4 GDP revision interval using only revision information "
            "that was conservatively knowable before the historical decision time."
        ),
        global_limitations=(
            "The two endpoints are scenario bounds and do not imply likelihood, confidence, or "
            "an expected revision.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    lower_state = ShockCompiler.apply(
        {("bea:gdp:2022q4", "revision_billions"): 0.0},
        compiled.trials[0],
    )
    upper_state = ShockCompiler.apply(
        {("bea:gdp:2022q4", "revision_billions"): 0.0},
        compiled.trials[-1],
    )
    metrics = {
        "advance_to_second": advance_to_second,
        "second_to_predecision": second_to_predecision,
        "cumulative": cumulative,
        "bound": bound,
        "q4_advance": q4_advance,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.revision-bound",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-revision-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=bound_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_q3_revision_path_billions": {
                "advance_to_second": _decimal_text(advance_to_second),
                "second_to_predecision": _decimal_text(second_to_predecision),
                "advance_to_predecision": _decimal_text(cumulative),
            },
            "naive_baseline": {
                "revision_billions": 0.0,
                "q4_gdp_billions": _decimal_text(q4_advance),
            },
            "bound_construction": {
                "magnitude_billions": _decimal_text(bound),
                "lower_revision_billions": _decimal_text(-bound),
                "upper_revision_billions": _decimal_text(bound),
                "probability_assigned": False,
                "future_event_used": False,
            },
            "candidate_q4_gdp_billions": {
                "lower": _decimal_text(q4_advance - bound),
                "upper": _decimal_text(q4_advance + bound),
            },
            "program": program.model_dump(mode="json"),
            "compiled": compiled.model_dump(mode="json"),
            "applied_endpoints": {
                "lower": lower_state["bea:gdp:2022q4", "revision_billions"],
                "upper": upper_state["bea:gdp:2022q4", "revision_billions"],
            },
        },
        limitations=(
            "The symmetric interval is inferred from one prior-quarter revision path.",
            "The Q4 second estimate and every later revision are absent from the artifact.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: MacroRevisionInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    revision_bound: Decimal,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=27)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-revision-screen",
        hypothesis=(
            "A retrospectively constructed single-quarter GDP revision boundary qualifies for "
            "research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "National-account estimates are revised as source information changes, but one prior "
            "quarter and one later realized revision cannot establish predictive validity."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="absolute inferred GDP revision-bound magnitude",
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
        output_manifest_sha256=_hash({"revision_bound_billions": _decimal_text(revision_bound)}),
        decision_time=lock.decision_time,
        max_input_available_at=max(
            record.interval.available_at for record in records_by_role.values()
        ),
        training_end=lock.decision_time.date() - timedelta(days=2),
        evaluation_start=holdout_start,
        evaluation_end=holdout_end,
        sample_size=len(records_by_role),
        metric_value=float(revision_bound),
        p_value=1.0,
        gross_return_bps=0.0,
        one_way_turnover=0.0,
        short_fraction=0.0,
        requested_capital_usd=1.0,
        median_daily_volume_usd=1.0,
        regime_metric_values={
            "advance-to-second-stage": float(revision_bound),
            "predecision-cumulative-stage": float(revision_bound),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records that no inferential test was "
            "performed.",
            "One-dollar operational fields are non-trading schema sentinels, not market evidence.",
            "No post-decision GDP estimate is present in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective macro revision attempt must fail closed")
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
            "P-value, capital, volume, and return fields are explicit non-performance sentinels.",
        ),
    )


def _claims(
    lock: MacroRevisionInputLock,
    records_by_role: dict[str, BitemporalRecord],
    artifacts: tuple[ReplayArtifact, ...],
    metrics: dict[str, Decimal],
) -> tuple[ReplayClaim, ...]:
    by_engine = {artifact.engine: artifact for artifact in artifacts}
    return (
        ReplayClaim(
            claim_id="claim-reported-gdp-vintages",
            statement=(
                "The decision lock preserves Q3 2022 GDP at three ALFRED vintages and the Q4 "
                f"2022 advance estimate of {records_by_role['q4_advance'].payload['value']} "
                "billion dollars SAAR."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary="These are official reported estimates at named vintages, not final output.",
            limitations=("The pack includes four selected facts, not the full GDP series.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-revision-envelope",
            statement=(
                "The Q4 revision scenario uses a zero-revision naive baseline and symmetric "
                f"endpoints of plus or minus {_decimal_text(metrics['bound'])} billion dollars, "
                "derived from the known Q3 cumulative revision magnitude."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no assigned probability or coverage guarantee.",
            limitations=("The later Q4 second estimate was not used to set the interval.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement=(
                "TrialCourt rejected the retrospective single-quarter attempt and retained all "
                "six adversarial findings."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external forecast validation.",
            limitations=("The attempt makes no statistical accuracy or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-schema-sentinels",
            statement=(
                "Trial operational fields are explicit one-dollar and zero-return schema "
                "sentinels rather than market or capacity evidence."
            ),
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels exist only to exercise mandatory adversarial checks.",
            limitations=("They must not be read as investment or execution results.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-four-engine-pack",
            statement=(
                "The ReplayPack contains content-addressed outputs from the four engines relevant "
                "to this macro-vintage problem."
            ),
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _records_by_role(lock: MacroRevisionInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _value(record: BitemporalRecord) -> Decimal:
    return _positive_decimal(record.payload.get("value"), record.record_id)


def _positive_decimal(value: object, context: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{context} must be a positive decimal") from error
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{context} must be a positive decimal")
    return parsed


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


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
