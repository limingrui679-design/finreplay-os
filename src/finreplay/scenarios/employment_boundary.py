"""Employment headline boundary replay over archived BLS releases."""

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

BLS_EMPLOYMENT_SOURCE_ID = "bls.employment_situation.archived_release"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmploymentBoundaryRoles(_StrictModel):
    """Four dated BLS Employment Situation facts assigned to analytical roles."""

    december_payroll: str = Field(min_length=1, max_length=300)
    december_unemployment: str = Field(min_length=1, max_length=300)
    january_payroll: str = Field(min_length=1, max_length=300)
    january_unemployment: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> EmploymentBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("employment boundary role record IDs must be unique")
        return self


class EmploymentBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision BLS headline facts for a payroll boundary."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: EmploymentBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=4, max_length=4)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> EmploymentBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("employment boundary build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("employment boundary records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError(
                "employment boundary roles must cover every locked record exactly once"
            )
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("employment boundary source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("employment boundary source hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected = {
            "december_payroll": (
                "2023-01-06",
                "2022-12",
                "nonfarm_payroll_change",
                datetime(2023, 1, 6, 13, 30, tzinfo=UTC),
            ),
            "december_unemployment": (
                "2023-01-06",
                "2022-12",
                "unemployment_rate",
                datetime(2023, 1, 6, 13, 30, tzinfo=UTC),
            ),
            "january_payroll": (
                "2023-02-03",
                "2023-01",
                "nonfarm_payroll_change",
                datetime(2023, 2, 3, 13, 30, tzinfo=UTC),
            ),
            "january_unemployment": (
                "2023-02-03",
                "2023-01",
                "unemployment_rate",
                datetime(2023, 2, 3, 13, 30, tzinfo=UTC),
            ),
        }
        for role, (release, report_period, metric, expected_available_at) in expected.items():
            record = by_id[getattr(self.roles, role)]
            if record.source.source_id != BLS_EMPLOYMENT_SOURCE_ID:
                raise ValueError(
                    "employment boundary lock accepts only archived BLS Employment Situation facts"
                )
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("employment boundary inputs must use versioned release snapshots")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("BLS Employment Situation facts must remain reported evidence")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("employment boundary timing must be deterministic")
            if record.interval.published_at != expected_available_at:
                raise ValueError(f"employment boundary {role} publication time mismatch")
            if record.interval.available_at != expected_available_at:
                raise ValueError(f"employment boundary {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("employment boundary lock contains a post-decision input")
            payload = record.payload
            if payload.get("release_date") != release:
                raise ValueError(f"employment boundary {role} release mismatch")
            if payload.get("report_period") != report_period:
                raise ValueError(f"employment boundary {role} report-period mismatch")
            if payload.get("metric") != metric:
                raise ValueError(f"employment boundary {role} metric mismatch")
            if payload.get("availability_method") != "explicit_bls_embargo_end_america_new_york":
                raise ValueError("employment boundary availability method mismatch")
            if metric == "nonfarm_payroll_change":
                value = payload.get("value_thousands")
                if payload.get("unit") != "Thousands of Persons":
                    raise ValueError("employment boundary payroll unit mismatch")
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    raise ValueError(
                        f"employment boundary {role} payroll value must be a positive integer"
                    )
            else:
                value = payload.get("value_percent")
                if payload.get("unit") != "Percent":
                    raise ValueError("employment boundary unemployment unit mismatch")
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not 0.0 <= float(value) <= 100.0
                ):
                    raise ValueError(
                        f"employment boundary {role} rate must be between zero and 100"
                    )
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match employment boundary input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> EmploymentBoundaryInputLock:
        """Normalize, validate, and self-hash an employment boundary input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_employment_boundary_input_lock(path: Path) -> EmploymentBoundaryInputLock:
    try:
        return EmploymentBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid employment boundary input lock: {path}") from error


def build_employment_boundary_replay_spec(
    lock: EmploymentBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for a BLS payroll release boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                source_ids=[BLS_EMPLOYMENT_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the employment boundary input lock")
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
            "The lock contains two headline measures from each of two releases, not all tables.",
            "Knowability uses each page's explicit 8:30 a.m. Eastern embargo end.",
            "The January 2023 release documents annual benchmarking and seasonal-factor updates.",
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
        range_width=int(metrics["payroll_range_width"]),
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
                "This aggregate labor-release boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no security, order, or allocation input is invented."
            ),
        },
        limitations=(
            "Static rendering does not independently validate the employment-boundary heuristic.",
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
            "Four actual engines ran over four locked headline facts from two archived BLS "
            "Employment Situation releases available before the decision time. Reported payroll "
            "changes and unemployment rates remain reported; the next-release payroll range is a "
            "two-point arithmetic heuristic with no assigned probability; TrialCourt rejects "
            "retrospective promotion. The March 10 release is held only in a disjoint post-"
            "decision event lock. This is not a forecast, calibrated interval, causal labor-"
            "market model, "
            "policy recommendation, trading signal, production deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The payroll endpoints are only the minimum and maximum of two known headline values; "
            "they have no probability or coverage guarantee.",
            "The March 10 BLS release is excluded from all decision inputs and artifacts.",
            "Annual benchmarking and seasonal-factor updates limit comparability across releases.",
            "Aggregate headline values do not identify workers, employers, or causal mechanisms.",
            "No market network, security, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: EmploymentBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int | float]]:
    december_payroll = _payroll_value(records_by_role["december_payroll"])
    january_payroll = _payroll_value(records_by_role["january_payroll"])
    december_unemployment = _rate_value(records_by_role["december_unemployment"])
    january_unemployment = _rate_value(records_by_role["january_unemployment"])
    payroll_lower = min(december_payroll, january_payroll)
    payroll_upper = max(december_payroll, january_payroll)
    payroll_range_width = payroll_upper - payroll_lower
    if payroll_range_width <= 0:
        raise ValueError("two BLS payroll headlines must establish a nonzero range")
    bound_record_ids = tuple(
        sorted(
            (
                records_by_role["december_payroll"].record_id,
                records_by_role["january_payroll"].record_id,
            )
        )
    )
    sources = tuple(
        record.source
        for record in (
            records_by_role["december_payroll"],
            records_by_role["january_payroll"],
        )
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-release-payroll-range",
        target_id="bls_employment_situation:united_states",
        variable="next_headline_payroll_change_thousands",
        unit="thousands_persons",
        operation=ShockOperation.SET,
        lower=float(payroll_lower),
        upper=float(payroll_upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=bound_record_ids,
        sources=sources,
        derivation=(
            "Use the minimum and maximum of the two payroll headline changes already knowable at "
            "the decision time as transparent next-release stress endpoints."
        ),
        limitations=(
            "Two release snapshots define a descriptive range, not a forecast, confidence "
            "interval, or stationary statistical sample.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-release-payroll-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate the two known payroll-headline endpoints using only archived BLS releases "
            "available at the historical decision boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, forecast, or policy interpretation.",
            "The January 2023 release documents annual benchmarking and seasonal-factor updates.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    lower_state = ShockCompiler.apply(
        {
            (
                "bls_employment_situation:united_states",
                "next_headline_payroll_change_thousands",
            ): float(january_payroll)
        },
        compiled.trials[0],
    )
    upper_state = ShockCompiler.apply(
        {
            (
                "bls_employment_situation:united_states",
                "next_headline_payroll_change_thousands",
            ): float(january_payroll)
        },
        compiled.trials[-1],
    )
    metrics = {
        "december_payroll": december_payroll,
        "january_payroll": january_payroll,
        "payroll_lower": payroll_lower,
        "payroll_upper": payroll_upper,
        "payroll_range_width": payroll_range_width,
        "december_unemployment": december_unemployment,
        "january_unemployment": january_unemployment,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.payroll-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-payroll-headline-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 1},
        source_set_historical_replay_eligible=True,
        source_record_ids=bound_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_headlines": metrics,
            "naive_baseline": {
                "next_headline_payroll_change_thousands": january_payroll,
                "definition": "persistence of the latest known headline payroll change",
            },
            "bound_construction": {
                "lower_payroll_change_thousands": payroll_lower,
                "upper_payroll_change_thousands": payroll_upper,
                "range_width_thousands": payroll_range_width,
                "endpoint_method": "minimum_and_maximum_of_two_known_headline_values",
                "probability_assigned": False,
                "future_event_used": False,
            },
            "program": program.model_dump(mode="json"),
            "compiled": compiled.model_dump(mode="json"),
            "applied_endpoints": {
                "lower": lower_state[
                    "bls_employment_situation:united_states",
                    "next_headline_payroll_change_thousands",
                ],
                "upper": upper_state[
                    "bls_employment_situation:united_states",
                    "next_headline_payroll_change_thousands",
                ],
            },
        },
        limitations=(
            "The two endpoints mechanically reuse only the two known headline values.",
            "The March 10 release is absent from the bound construction and artifact sources.",
            "Annual benchmarking limits interpretation of change across adjacent releases.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: EmploymentBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    range_width: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=35)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-range-screen",
        hypothesis=(
            "A retrospectively constructed two-release payroll range qualifies for research "
            "eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "Monthly payroll headlines measure aggregate employment change, but two releases and "
            "one later outcome cannot establish predictive validity or identify labor-market "
            "causes."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="two-release payroll headline range width in thousands",
        expected_direction="two-sided",
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
        output_manifest_sha256=_hash({"payroll_range_width_thousands": range_width}),
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
            "december-to-january-headline-difference": float(range_width),
            "two-release-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No March 10 release fact is present in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective employment boundary attempt must fail closed")
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
    metrics: dict[str, int | float],
) -> tuple[ReplayClaim, ...]:
    by_engine = {artifact.engine: artifact for artifact in artifacts}
    return (
        ReplayClaim(
            claim_id="claim-reported-employment-headlines",
            statement=(
                "The locked BLS releases report headline nonfarm payroll changes of "
                f"{int(metrics['december_payroll']):,} thousand for December 2022 and "
                f"{int(metrics['january_payroll']):,} thousand for January 2023, alongside "
                f"unemployment rates of {metrics['december_unemployment']:.1f} and "
                f"{metrics['january_unemployment']:.1f} percent."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary="These are aggregate BLS release-snapshot facts, not worker-level records.",
            limitations=(
                "The pack includes only two headline measures from each of two releases.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-payroll-range",
            statement=(
                "The next-release payroll stress range uses endpoints of "
                f"{int(metrics['payroll_lower']):,} and {int(metrics['payroll_upper']):,} "
                "thousand, with the latest known value as the explicit persistence baseline."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no assigned probability or coverage guarantee.",
            limitations=(
                "The March 10 release was not used to set the interval.",
                "Annual benchmarking and seasonal-factor updates limit comparability.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement="TrialCourt rejected the retrospective two-release payroll-range attempt.",
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


def _records_by_role(lock: EmploymentBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _payroll_value(record: BitemporalRecord) -> int:
    value = record.payload["value_thousands"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("payroll headline value must be an integer")
    return value


def _rate_value(record: BitemporalRecord) -> float:
    value = record.payload["value_percent"]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("unemployment rate value must be numeric")
    return float(value)


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
