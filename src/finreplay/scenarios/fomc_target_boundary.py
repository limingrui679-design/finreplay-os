"""FOMC target-range boundary replay over archived policy statements."""

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

FED_FOMC_SOURCE_ID = "federal_reserve.fomc.archived_statement"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FOMCTargetBoundaryRoles(_StrictModel):
    """Four dated FOMC target endpoints assigned to analytical roles."""

    february_lower: str = Field(min_length=1, max_length=300)
    february_upper: str = Field(min_length=1, max_length=300)
    march_lower: str = Field(min_length=1, max_length=300)
    march_upper: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> FOMCTargetBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("FOMC target boundary role record IDs must be unique")
        return self


class FOMCTargetBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision FOMC target-range facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: FOMCTargetBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=4, max_length=4)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> FOMCTargetBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("FOMC target boundary build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("FOMC target boundary records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError(
                "FOMC target boundary roles must cover every locked record exactly once"
            )
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("FOMC target boundary source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("FOMC target boundary source hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected = {
            "february_lower": (
                "2023-02-01",
                "target_range_lower",
                450,
                datetime(2023, 2, 1, 19, 0, tzinfo=UTC),
            ),
            "february_upper": (
                "2023-02-01",
                "target_range_upper",
                475,
                datetime(2023, 2, 1, 19, 0, tzinfo=UTC),
            ),
            "march_lower": (
                "2023-03-22",
                "target_range_lower",
                475,
                datetime(2023, 3, 22, 18, 0, tzinfo=UTC),
            ),
            "march_upper": (
                "2023-03-22",
                "target_range_upper",
                500,
                datetime(2023, 3, 22, 18, 0, tzinfo=UTC),
            ),
        }
        for role, (release, metric, expected_value, expected_available_at) in expected.items():
            record = by_id[getattr(self.roles, role)]
            if record.source.source_id != FED_FOMC_SOURCE_ID:
                raise ValueError(
                    "FOMC target boundary lock accepts only archived FOMC statement facts"
                )
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("FOMC target boundary inputs must use versioned release snapshots")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("FOMC target facts must remain reported evidence")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("FOMC target boundary timing must be deterministic")
            if record.interval.published_at != expected_available_at:
                raise ValueError(f"FOMC target boundary {role} publication time mismatch")
            if record.interval.available_at != expected_available_at:
                raise ValueError(f"FOMC target boundary {role} availability time mismatch")
            if record.interval.valid_from != expected_available_at:
                raise ValueError(f"FOMC target boundary {role} effective time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("FOMC target boundary lock contains a post-decision input")
            payload = record.payload
            if payload.get("release_date") != release:
                raise ValueError(f"FOMC target boundary {role} release mismatch")
            if payload.get("metric") != metric:
                raise ValueError(f"FOMC target boundary {role} metric mismatch")
            if (
                payload.get("availability_method")
                != "explicit_fomc_release_time_america_new_york"
            ):
                raise ValueError("FOMC target boundary availability method mismatch")
            if payload.get("policy") != "Federal funds target range":
                raise ValueError("FOMC target boundary policy mismatch")
            if payload.get("unit") != "Basis Points":
                raise ValueError("FOMC target boundary unit mismatch")
            if payload.get("range_width_basis_points") != 25:
                raise ValueError("FOMC target boundary range width mismatch")
            value = payload.get("value_basis_points")
            if value != expected_value:
                raise ValueError(f"FOMC target boundary {role} value mismatch")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match FOMC target boundary input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> FOMCTargetBoundaryInputLock:
        """Normalize, validate, and self-hash an FOMC target boundary input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_fomc_target_boundary_input_lock(path: Path) -> FOMCTargetBoundaryInputLock:
    try:
        return FOMCTargetBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid FOMC target boundary input lock: {path}") from error


def build_fomc_target_boundary_replay_spec(
    lock: FOMCTargetBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for an FOMC target-range boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                source_ids=[FED_FOMC_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the FOMC target boundary input lock")
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-release-query",
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
        },
        limitations=(
            "The lock contains only target-range endpoints from two policy statements.",
            "Knowability uses each page's explicit 2:00 p.m. EST or EDT release label.",
            "Statement text is not converted into a causal policy or market-response model.",
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
        step_bound=int(metrics["known_upper_step_basis_points"]),
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
                "This aggregate policy-release boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no security, order, or allocation input is invented."
            ),
        },
        limitations=(
            "Static rendering does not independently validate the fomc-target-boundary heuristic.",
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
        claims=_claims(by_role, artifacts, metrics),
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
            "Four actual engines ran over four locked target endpoints from two archived FOMC "
            "statements available before the decision time. Official target ranges remain "
            "reported; the next upper-bound range is an arithmetic heuristic with no assigned "
            "probability; "
            "TrialCourt rejects retrospective promotion. The May 3 upper target is held only in a "
            "disjoint post-decision event lock. This is not a forecast, calibrated interval, "
            "causal monetary-policy model, policy recommendation, trading signal, production "
            "deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The next-upper endpoints only repeat zero or one known 25-basis-point step; they have "
            "no probability or coverage guarantee.",
            "The May 3 FOMC statement is excluded from all decision inputs and artifacts.",
            "Target ranges do not establish market expectations, policy correctness, or effects.",
            "No market network, security, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: FOMCTargetBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    february_lower = _basis_points(records_by_role["february_lower"])
    february_upper = _basis_points(records_by_role["february_upper"])
    march_lower = _basis_points(records_by_role["march_lower"])
    march_upper = _basis_points(records_by_role["march_upper"])
    february_width = february_upper - february_lower
    march_width = march_upper - march_lower
    lower_step = march_lower - february_lower
    upper_step = march_upper - february_upper
    if february_width != 25 or march_width != 25:
        raise ValueError("locked FOMC target ranges must both be 25 basis points wide")
    if lower_step != 25 or upper_step != 25:
        raise ValueError("locked FOMC statements must establish one known 25-basis-point step")
    next_upper_lower = march_upper
    next_upper_upper = march_upper + upper_step
    bound_record_ids = tuple(
        sorted(
            (
                records_by_role["february_upper"].record_id,
                records_by_role["march_upper"].record_id,
            )
        )
    )
    sources = tuple(
        record.source
        for record in (
            records_by_role["february_upper"],
            records_by_role["march_upper"],
        )
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-upper-target-bound",
        target_id="fomc_policy:federal_funds_target_range",
        variable="next_target_range_upper_basis_points",
        unit="basis_points",
        operation=ShockOperation.SET,
        lower=float(next_upper_lower),
        upper=float(next_upper_upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=bound_record_ids,
        sources=sources,
        derivation=(
            "Bound the next target-range upper endpoint between persistence of the March upper "
            "endpoint and one continuation of the already known February-to-March 25-basis-point "
            "upper-endpoint step."
        ),
        limitations=(
            "Repeating zero or one known policy step defines a transparent stress range, not a "
            "forecast, confidence interval, or policy recommendation.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-upper-target-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate persistence and one-step-continuation endpoints using only archived FOMC "
            "statements available at the historical decision boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, forecast, causal, or normative policy "
            "interpretation.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (
        "fomc_policy:federal_funds_target_range",
        "next_target_range_upper_basis_points",
    )
    lower_state = ShockCompiler.apply(
        {state_key: float(march_upper)},
        compiled.trials[0],
    )
    upper_state = ShockCompiler.apply(
        {state_key: float(march_upper)},
        compiled.trials[-1],
    )
    metrics = {
        "february_lower_basis_points": february_lower,
        "february_upper_basis_points": february_upper,
        "march_lower_basis_points": march_lower,
        "march_upper_basis_points": march_upper,
        "known_lower_step_basis_points": lower_step,
        "known_upper_step_basis_points": upper_step,
        "next_upper_lower_basis_points": next_upper_lower,
        "next_upper_upper_basis_points": next_upper_upper,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.upper-bound",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-fomc-upper-target-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=bound_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_target_ranges": metrics,
            "naive_baseline": {
                "next_target_range_upper_basis_points": march_upper,
                "definition": "persistence of the latest known upper target endpoint",
            },
            "bound_construction": {
                "lower_next_upper_basis_points": next_upper_lower,
                "upper_next_upper_basis_points": next_upper_upper,
                "known_step_basis_points": upper_step,
                "endpoint_method": "zero_or_one_continuation_of_known_upper_endpoint_step",
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
            "The upper endpoint mechanically repeats one known 25-basis-point policy step.",
            "The May 3 statement is absent from the bound construction and artifact sources.",
            "No market-implied expectation, voting model, macro forecast, or causal effect is "
            "used.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: FOMCTargetBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    step_bound: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=45)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-step-screen",
        hypothesis=(
            "A retrospectively constructed zero-or-one-step FOMC target boundary qualifies for "
            "research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "Successive target ranges record policy decisions, but two statements and one later "
            "outcome cannot establish predictive validity, policy correctness, or causal effects."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="known FOMC upper-target step in basis points",
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
        output_manifest_sha256=_hash({"upper_target_step_basis_points": step_bound}),
        decision_time=lock.decision_time,
        max_input_available_at=max(
            record.interval.available_at for record in records_by_role.values()
        ),
        training_end=lock.decision_time.date() - timedelta(days=2),
        evaluation_start=holdout_start,
        evaluation_end=holdout_end,
        sample_size=len(records_by_role),
        metric_value=float(step_bound),
        p_value=1.0,
        gross_return_bps=0.0,
        one_way_turnover=0.0,
        short_fraction=0.0,
        requested_capital_usd=1.0,
        median_daily_volume_usd=1.0,
        regime_metric_values={
            "february-to-march-lower-step": float(step_bound),
            "february-to-march-upper-step": float(step_bound),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No May 3 statement fact is present in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective FOMC target boundary attempt must fail closed")
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
    records_by_role: dict[str, BitemporalRecord],
    artifacts: tuple[ReplayArtifact, ...],
    metrics: dict[str, int],
) -> tuple[ReplayClaim, ...]:
    by_engine = {artifact.engine: artifact for artifact in artifacts}
    return (
        ReplayClaim(
            claim_id="claim-reported-fomc-target-ranges",
            statement=(
                "The locked FOMC statements report federal-funds target ranges of 450 to 475 "
                "basis points on February 1, 2023 and 475 to 500 basis points on March 22, 2023."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary="These are official policy-release facts, not forecasts or market prices.",
            limitations=(
                "The pack includes only the two target endpoints from each statement.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-next-upper-bound",
            statement=(
                "The next statement's upper target uses a 500-basis-point persistence baseline "
                "and a 525-basis-point one-known-step-continuation endpoint."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no assigned probability or coverage guarantee.",
            limitations=(
                "The May 3 statement was not used to set the interval.",
                "The range is not a monetary-policy forecast or recommendation.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement="TrialCourt rejected the retrospective FOMC policy-step attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external forecast review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not orders, capacity, capital, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _records_by_role(lock: FOMCTargetBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _basis_points(record: BitemporalRecord) -> int:
    value = record.payload["value_basis_points"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("FOMC target endpoint must be an integer number of basis points")
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
