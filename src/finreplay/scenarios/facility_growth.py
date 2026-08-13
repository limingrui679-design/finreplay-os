"""BTFP early-growth boundary replay over archived Federal Reserve releases."""

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

FED_H41_BTFP_SOURCE_ID = "federal_reserve.h41.btfp_historical_release"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FacilityGrowthRoles(_StrictModel):
    """Four dated H.4.1 facts assigned to analytical roles."""

    first_weekly_average: str = Field(min_length=1, max_length=300)
    first_wednesday: str = Field(min_length=1, max_length=300)
    second_weekly_average: str = Field(min_length=1, max_length=300)
    second_wednesday: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> FacilityGrowthRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("facility growth role record IDs must be unique")
        return self


class FacilityGrowthInputLock(_StrictModel):
    """Content-addressed pre-decision H.4.1 facts for an early BTFP boundary."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: FacilityGrowthRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=4, max_length=4)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> FacilityGrowthInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("facility growth build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("facility growth records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("facility growth roles must cover every locked record exactly once")
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("facility growth source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("facility growth source hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected = {
            "first_weekly_average": ("2023-03-16", "2023-03-15", "weekly_average"),
            "first_wednesday": ("2023-03-16", "2023-03-15", "wednesday_outstanding"),
            "second_weekly_average": ("2023-03-23", "2023-03-22", "weekly_average"),
            "second_wednesday": ("2023-03-23", "2023-03-22", "wednesday_outstanding"),
        }
        for role, (release, week_ending, metric) in expected.items():
            record = by_id[getattr(self.roles, role)]
            if record.source.source_id != FED_H41_BTFP_SOURCE_ID:
                raise ValueError("facility growth lock accepts only archived H.4.1 BTFP facts")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("facility growth inputs must use versioned release snapshots")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("H.4.1 BTFP facts must remain reported evidence")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("facility growth timing must use a deterministic safe bound")
            if record.interval.available_at > self.decision_time:
                raise ValueError("facility growth lock contains a post-decision input")
            payload = record.payload
            if payload.get("release_date") != release:
                raise ValueError(f"facility growth {role} release mismatch")
            if payload.get("week_ending") != week_ending:
                raise ValueError(f"facility growth {role} week-ending mismatch")
            if payload.get("metric") != metric:
                raise ValueError(f"facility growth {role} metric mismatch")
            if payload.get("program") != "Bank Term Funding Program":
                raise ValueError("facility growth program mismatch")
            if payload.get("unit") != "Millions of Dollars":
                raise ValueError("facility growth unit mismatch")
            value = payload.get("value_millions")
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"facility growth {role} value must be a positive integer")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match facility growth input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> FacilityGrowthInputLock:
        """Normalize, validate, and self-hash a facility growth input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_facility_growth_input_lock(path: Path) -> FacilityGrowthInputLock:
    try:
        return FacilityGrowthInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid facility growth input lock: {path}") from error


def build_facility_growth_replay_spec(
    lock: FacilityGrowthInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for a BTFP early-growth decision boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                source_ids=[FED_H41_BTFP_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the facility growth input lock")
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
            "The lock contains two measures from each of two releases, not all H.4.1 tables.",
            "Release dates use a conservative two-day knowledge bound, not exact intraday time.",
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
        growth_bound=metrics["wednesday_growth"],
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
                "This aggregate facility-release boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no network, order, or allocation input is invented."
            ),
        },
        limitations=(
            "Static rendering does not independently validate the facility-growth heuristic.",
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
            "Four actual engines ran over four locked BTFP facts from two archived Federal "
            "Reserve H.4.1 releases that were conservatively knowable at the decision time. "
            "Reported balances remain reported; growth and the next-week envelope are arithmetic "
            "inferences with no assigned probability; TrialCourt rejects retrospective promotion. "
            "The March 30 release is held only in a disjoint post-decision event lock. This is not "
            "a forecast, systemic-stress attribution, causal model, policy recommendation, trading "
            "signal, production deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The next-week growth interval uses a single prior weekly Wednesday change and has no "
            "probability or coverage guarantee.",
            "The March 30 H.4.1 release is excluded from all decision inputs and artifacts.",
            "Aggregate facility balances do not identify borrowers, collateral, motives, or "
            "causal stress channels.",
            "No market network, security, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: FacilityGrowthInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    first_average = _value(records_by_role["first_weekly_average"])
    second_average = _value(records_by_role["second_weekly_average"])
    first_wednesday = _value(records_by_role["first_wednesday"])
    second_wednesday = _value(records_by_role["second_wednesday"])
    average_growth = second_average - first_average
    wednesday_growth = second_wednesday - first_wednesday
    if average_growth <= 0 or wednesday_growth <= 0:
        raise ValueError("early BTFP releases must establish positive reported growth")
    bound_record_ids = tuple(
        sorted(
            (
                records_by_role["first_wednesday"].record_id,
                records_by_role["second_wednesday"].record_id,
            )
        )
    )
    sources = tuple(
        record.source
        for record in (
            records_by_role["first_wednesday"],
            records_by_role["second_wednesday"],
        )
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-week-growth-bound",
        target_id="federal_reserve_facility:btfp",
        variable="next_week_growth_millions",
        unit="millions_usd",
        operation=ShockOperation.SET,
        lower=0.0,
        upper=float(wednesday_growth),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=bound_record_ids,
        sources=sources,
        derivation=(
            "Bound the next reported Wednesday balance between no additional growth and one "
            "continuation of the already known March 15-to-March 22 Wednesday increase."
        ),
        limitations=(
            "One observed weekly change is a transparent stress envelope, not a forecast or "
            "statistical interval.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-week-growth-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate no-growth and one-week-growth-continuation endpoints using only archived "
            "H.4.1 releases knowable at the historical decision boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, forecast, confidence, or policy interpretation.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    lower_state = ShockCompiler.apply(
        {("federal_reserve_facility:btfp", "next_week_growth_millions"): 0.0},
        compiled.trials[0],
    )
    upper_state = ShockCompiler.apply(
        {("federal_reserve_facility:btfp", "next_week_growth_millions"): 0.0},
        compiled.trials[-1],
    )
    metrics = {
        "first_average": first_average,
        "second_average": second_average,
        "average_growth": average_growth,
        "first_wednesday": first_wednesday,
        "second_wednesday": second_wednesday,
        "wednesday_growth": wednesday_growth,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.growth-bound",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-facility-growth-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=bound_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_growth_millions": metrics,
            "naive_baseline": {
                "next_week_growth_millions": 0.0,
                "next_wednesday_balance_millions": second_wednesday,
            },
            "bound_construction": {
                "lower_growth_millions": 0,
                "upper_growth_millions": wednesday_growth,
                "lower_balance_millions": second_wednesday,
                "upper_balance_millions": second_wednesday + wednesday_growth,
                "probability_assigned": False,
                "future_event_used": False,
            },
            "program": program.model_dump(mode="json"),
            "compiled": compiled.model_dump(mode="json"),
            "applied_endpoints": {
                "lower": lower_state[
                    "federal_reserve_facility:btfp", "next_week_growth_millions"
                ],
                "upper": upper_state[
                    "federal_reserve_facility:btfp", "next_week_growth_millions"
                ],
            },
        },
        limitations=(
            "The upper endpoint mechanically repeats one prior Wednesday balance change.",
            "The March 30 release is absent from the bound construction and artifact sources.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: FacilityGrowthInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    growth_bound: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=7)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-growth-screen",
        hypothesis=(
            "A retrospectively constructed one-week BTFP growth boundary qualifies for research "
            "eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "BTFP balances can change with aggregate facility use, but two releases and one later "
            "outcome cannot establish predictive validity or identify stress causes."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="inferred next-week BTFP Wednesday growth-bound magnitude",
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
        output_manifest_sha256=_hash({"growth_bound_millions": growth_bound}),
        decision_time=lock.decision_time,
        max_input_available_at=max(
            record.interval.available_at for record in records_by_role.values()
        ),
        training_end=lock.decision_time.date() - timedelta(days=2),
        evaluation_start=holdout_start,
        evaluation_end=holdout_end,
        sample_size=len(records_by_role),
        metric_value=float(growth_bound),
        p_value=1.0,
        gross_return_bps=0.0,
        one_way_turnover=0.0,
        short_fraction=0.0,
        requested_capital_usd=1.0,
        median_daily_volume_usd=1.0,
        regime_metric_values={
            "weekly-average-growth": float(growth_bound),
            "wednesday-balance-growth": float(growth_bound),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No March 30 release fact is present in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective facility growth attempt must fail closed")
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
            claim_id="claim-reported-btfp-releases",
            statement=(
                "The locked H.4.1 releases report BTFP Wednesday balances of "
                f"{metrics['first_wednesday']:,} and {metrics['second_wednesday']:,} million "
                "dollars for March 15 and March 22, 2023."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary="These are aggregate Federal Reserve reported balances, not borrower data.",
            limitations=("The pack includes two measures from two releases only.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-btfp-growth-envelope",
            statement=(
                "The next reported Wednesday balance uses a no-growth baseline and an upper "
                f"growth endpoint of {metrics['wednesday_growth']:,} million dollars, matching "
                "the one prior known weekly change."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no assigned probability or coverage guarantee.",
            limitations=("The March 30 release was not used to set the interval.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement="TrialCourt rejected the retrospective one-week facility-growth attempt.",
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


def _records_by_role(lock: FacilityGrowthInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _value(record: BitemporalRecord) -> int:
    value = record.payload["value_millions"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("facility value must be an integer")
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
