"""CPI headline boundary replay over archived BLS releases."""

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

BLS_CPI_SOURCE_ID = "bls.cpi.archived_release"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CPIBoundaryRoles(_StrictModel):
    """Four dated BLS Consumer Price Index facts assigned to analytical roles."""

    december_monthly: str = Field(min_length=1, max_length=300)
    december_yoy: str = Field(min_length=1, max_length=300)
    january_monthly: str = Field(min_length=1, max_length=300)
    january_yoy: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> CPIBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("CPI release boundary role record IDs must be unique")
        return self


class CPIBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision BLS headline facts for a CPI monthly change boundary."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: CPIBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=4, max_length=4)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> CPIBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("CPI release boundary build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("CPI release boundary records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError(
                "CPI release boundary roles must cover every locked record exactly once"
            )
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("CPI release boundary source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("CPI release boundary source hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected = {
            "december_monthly": (
                "2023-01-12",
                "2022-12",
                "all_items_monthly_change_seasonally_adjusted",
                datetime(2023, 1, 12, 13, 30, tzinfo=UTC),
            ),
            "december_yoy": (
                "2023-01-12",
                "2022-12",
                "all_items_12_month_change_not_seasonally_adjusted",
                datetime(2023, 1, 12, 13, 30, tzinfo=UTC),
            ),
            "january_monthly": (
                "2023-02-14",
                "2023-01",
                "all_items_monthly_change_seasonally_adjusted",
                datetime(2023, 2, 14, 13, 30, tzinfo=UTC),
            ),
            "january_yoy": (
                "2023-02-14",
                "2023-01",
                "all_items_12_month_change_not_seasonally_adjusted",
                datetime(2023, 2, 14, 13, 30, tzinfo=UTC),
            ),
        }
        for role, (release, report_period, metric, expected_available_at) in expected.items():
            record = by_id[getattr(self.roles, role)]
            if record.source.source_id != BLS_CPI_SOURCE_ID:
                raise ValueError(
                    "CPI release boundary lock accepts only archived BLS Consumer Price Index facts"
                )
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("CPI release boundary inputs must use versioned release snapshots")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("BLS Consumer Price Index facts must remain reported evidence")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("CPI release boundary timing must be deterministic")
            if record.interval.published_at != expected_available_at:
                raise ValueError(f"CPI release boundary {role} publication time mismatch")
            if record.interval.available_at != expected_available_at:
                raise ValueError(f"CPI release boundary {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("CPI release boundary lock contains a post-decision input")
            payload = record.payload
            if payload.get("release_date") != release:
                raise ValueError(f"CPI release boundary {role} release mismatch")
            if payload.get("report_period") != report_period:
                raise ValueError(f"CPI release boundary {role} report-period mismatch")
            if payload.get("metric") != metric:
                raise ValueError(f"CPI release boundary {role} metric mismatch")
            if payload.get("availability_method") != "explicit_bls_embargo_end_america_new_york":
                raise ValueError("CPI release boundary availability method mismatch")
            value = payload.get("value_tenths_percent")
            if payload.get("unit") != "Tenths of a Percent":
                raise ValueError(f"CPI release boundary {role} unit mismatch")
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(
                    f"CPI release boundary {role} value must be an integer number of tenths"
                )
            if metric == "all_items_monthly_change_seasonally_adjusted":
                if not -1_000 <= value <= 1_000:
                    raise ValueError(
                        f"CPI release boundary {role} monthly change is outside the supported range"
                    )
            elif not 0 <= value <= 1_000:
                raise ValueError(
                    f"CPI release boundary {role} 12-month change is outside the supported range"
                )
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match CPI release boundary input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> CPIBoundaryInputLock:
        """Normalize, validate, and self-hash a CPI release boundary input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_cpi_boundary_input_lock(path: Path) -> CPIBoundaryInputLock:
    try:
        return CPIBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid CPI release boundary input lock: {path}") from error


def build_cpi_boundary_replay_spec(
    lock: CPIBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for a BLS CPI monthly change release boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                source_ids=[BLS_CPI_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the CPI release boundary input lock")
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
            "The February 2023 release documents annual weight updates and recalculation of the "
            "previous five years of seasonally adjusted indexes.",
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
        range_width=int(metrics["monthly_range_width"]),
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
                "This aggregate CPI-release boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no security, order, or allocation input is invented."
            ),
        },
        limitations=(
            "Static rendering does not independently validate the CPI-boundary heuristic.",
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
            "Consumer Price Index releases available before the decision time. Reported monthly "
            "and 12-month changes remain reported; the next-release monthly-change range is a "
            "two-point arithmetic heuristic with no assigned probability; TrialCourt rejects "
            "retrospective promotion. The March 14 release is held only in a disjoint post-"
            "decision event lock. This is not a forecast, calibrated interval, causal inflation "
            "model, "
            "policy recommendation, trading signal, production deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The CPI monthly-change endpoints are only the minimum and maximum of two known "
            "headline values; they have no probability or coverage guarantee.",
            "The March 14 BLS release is excluded from all decision inputs and artifacts.",
            "Annual weight updates and seasonal recalculation limit comparability across releases.",
            "Aggregate headline values do not identify households, goods, or causal mechanisms.",
            "No market network, security, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: CPIBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int | float]]:
    december_monthly = _monthly_value(records_by_role["december_monthly"])
    january_monthly = _monthly_value(records_by_role["january_monthly"])
    december_yoy = _yoy_value(records_by_role["december_yoy"])
    january_yoy = _yoy_value(records_by_role["january_yoy"])
    monthly_lower = min(december_monthly, january_monthly)
    monthly_upper = max(december_monthly, january_monthly)
    monthly_range_width = monthly_upper - monthly_lower
    if monthly_range_width <= 0:
        raise ValueError("two BLS CPI monthly-change headlines must establish a nonzero range")
    bound_record_ids = tuple(
        sorted(
            (
                records_by_role["december_monthly"].record_id,
                records_by_role["january_monthly"].record_id,
            )
        )
    )
    sources = tuple(
        record.source
        for record in (
            records_by_role["december_monthly"],
            records_by_role["january_monthly"],
        )
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-release-cpi-range",
        target_id="bls_cpi_u_all_items:united_states",
        variable="next_release_monthly_change_tenths_percent",
        unit="tenths_percent",
        operation=ShockOperation.SET,
        lower=float(monthly_lower),
        upper=float(monthly_upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=bound_record_ids,
        sources=sources,
        derivation=(
            "Use the minimum and maximum of the two CPI monthly changes already knowable at "
            "the decision time as transparent next-release stress endpoints."
        ),
        limitations=(
            "Two release snapshots define a descriptive range, not a forecast, confidence "
            "interval, or stationary statistical sample.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-release-cpi-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate the two known CPI monthly-change endpoints using only archived BLS releases "
            "available at the historical decision boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, forecast, or policy interpretation.",
            "The February 2023 release documents annual weight updates and recalculation of the "
            "previous five years of seasonally adjusted indexes.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    lower_state = ShockCompiler.apply(
        {
            (
                "bls_cpi_u_all_items:united_states",
                "next_release_monthly_change_tenths_percent",
            ): float(january_monthly)
        },
        compiled.trials[0],
    )
    upper_state = ShockCompiler.apply(
        {
            (
                "bls_cpi_u_all_items:united_states",
                "next_release_monthly_change_tenths_percent",
            ): float(january_monthly)
        },
        compiled.trials[-1],
    )
    metrics: dict[str, int | float] = {
        "december_monthly": december_monthly,
        "january_monthly": january_monthly,
        "monthly_lower": monthly_lower,
        "monthly_upper": monthly_upper,
        "monthly_range_width": monthly_range_width,
        "december_yoy": december_yoy,
        "january_yoy": january_yoy,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.cpi-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-cpi-monthly-change-program",
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
                "next_release_monthly_change_tenths_percent": january_monthly,
                "definition": "persistence of the latest known headline CPI monthly change",
            },
            "bound_construction": {
                "lower_monthly_change_tenths_percent": monthly_lower,
                "upper_monthly_change_tenths_percent": monthly_upper,
                "range_width_tenths_percent": monthly_range_width,
                "endpoint_method": "minimum_and_maximum_of_two_known_headline_values",
                "probability_assigned": False,
                "future_event_used": False,
            },
            "program": program.model_dump(mode="json"),
            "compiled": compiled.model_dump(mode="json"),
            "applied_endpoints": {
                "lower": lower_state[
                    "bls_cpi_u_all_items:united_states",
                    "next_release_monthly_change_tenths_percent",
                ],
                "upper": upper_state[
                    "bls_cpi_u_all_items:united_states",
                    "next_release_monthly_change_tenths_percent",
                ],
            },
        },
        limitations=(
            "The two endpoints mechanically reuse only the two known headline values.",
            "The March 14 release is absent from the bound construction and artifact sources.",
            "Annual seasonal recalculation limits interpretation of change across adjacent "
            "releases.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: CPIBoundaryInputLock,
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
            "A retrospectively constructed two-release CPI monthly-change range qualifies for "
            "research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "Monthly CPI-U headlines describe aggregate price-index changes, but two releases "
            "and one later outcome cannot establish predictive validity or identify inflation "
            "causes."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="two-release CPI monthly-change range width in tenths of a percent",
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
        output_manifest_sha256=_hash({"monthly_range_width_tenths_percent": range_width}),
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
            "No March 14 release fact is present in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective CPI release boundary attempt must fail closed")
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
            claim_id="claim-reported-cpi-headlines",
            statement=(
                "The locked BLS releases report seasonally adjusted CPI-U all-items monthly "
                f"changes of {_format_tenths(metrics['december_monthly'])} percent for December "
                f"2022 and {_format_tenths(metrics['january_monthly'])} percent for January "
                "2023, alongside not-seasonally-adjusted 12-month changes of "
                f"{_format_tenths(metrics['december_yoy'])} and "
                f"{_format_tenths(metrics['january_yoy'])} percent."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are aggregate BLS release-snapshot facts, not micro price-quote records."
            ),
            limitations=(
                "The pack includes only two headline measures from each of two releases.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-cpi-range",
            statement=(
                "The next-release CPI monthly-change stress range uses endpoints of "
                f"{_format_tenths(metrics['monthly_lower'])} and "
                f"{_format_tenths(metrics['monthly_upper'])} percent, with the latest known "
                "value as the explicit persistence baseline."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no assigned probability or coverage guarantee.",
            limitations=(
                "The March 14 release was not used to set the interval.",
                "Annual weight updates and seasonal recalculation limit comparability.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement="TrialCourt rejected the retrospective two-release CPI-range attempt.",
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


def _records_by_role(lock: CPIBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _monthly_value(record: BitemporalRecord) -> int:
    value = record.payload["value_tenths_percent"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("CPI monthly-change headline value must be an integer")
    return value


def _yoy_value(record: BitemporalRecord) -> int:
    value = record.payload["value_tenths_percent"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("12-month CPI change value must be an integer")
    return value


def _format_tenths(value: int | float) -> str:
    return f"{float(value) / 10:.1f}"


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
