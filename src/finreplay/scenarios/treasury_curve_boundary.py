"""2-year/10-year Treasury-curve boundary over native ALFRED vintages."""

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

ALFRED_TREASURY_YIELD_SOURCE_ID = "fred.alfred.vintage_treasury_yield"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TreasuryCurveBoundaryRoles(_StrictModel):
    """Four dated DGS2/DGS10 facts assigned to two yield-curve snapshots."""

    march08_two_year: str = Field(min_length=1, max_length=300)
    march08_ten_year: str = Field(min_length=1, max_length=300)
    march13_two_year: str = Field(min_length=1, max_length=300)
    march13_ten_year: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> TreasuryCurveBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("Treasury-curve boundary role record IDs must be unique")
        return self


class TreasuryCurveBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision yield facts for a Treasury-curve boundary."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: TreasuryCurveBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=4, max_length=4)
    records: tuple[BitemporalRecord, ...] = Field(min_length=4, max_length=4)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> TreasuryCurveBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("Treasury-curve boundary build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("Treasury-curve boundary records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError(
                "Treasury-curve boundary roles must cover every locked record exactly once"
            )
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("Treasury-curve boundary source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("Treasury-curve boundary source hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected = {
            "march08_two_year": (
                "DGS2",
                "2023-03-08",
                "2023-03-09",
                2,
                datetime(2023, 3, 11, tzinfo=UTC),
            ),
            "march08_ten_year": (
                "DGS10",
                "2023-03-08",
                "2023-03-09",
                10,
                datetime(2023, 3, 11, tzinfo=UTC),
            ),
            "march13_two_year": (
                "DGS2",
                "2023-03-13",
                "2023-03-14",
                2,
                datetime(2023, 3, 16, tzinfo=UTC),
            ),
            "march13_ten_year": (
                "DGS10",
                "2023-03-13",
                "2023-03-14",
                10,
                datetime(2023, 3, 16, tzinfo=UTC),
            ),
        }
        for role, (
            series_id,
            observation_date,
            vintage_date,
            maturity_years,
            expected_available_at,
        ) in expected.items():
            record = by_id[getattr(self.roles, role)]
            if record.source.source_id != ALFRED_TREASURY_YIELD_SOURCE_ID:
                raise ValueError(
                    "Treasury-curve boundary lock accepts only ALFRED Treasury-yield facts"
                )
            if record.source.temporal_coverage is not TemporalCoverage.VINTAGE_NATIVE:
                raise ValueError("Treasury-curve boundary inputs must use native ALFRED vintages")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("Treasury-yield facts must remain reported evidence")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("Treasury-curve boundary timing must be deterministic")
            if record.interval.published_at != expected_available_at:
                raise ValueError(f"Treasury-curve boundary {role} publication time mismatch")
            if record.interval.available_at != expected_available_at:
                raise ValueError(f"Treasury-curve boundary {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("Treasury-curve boundary lock contains a post-decision input")
            payload = record.payload
            if payload.get("series_id") != series_id:
                raise ValueError(f"Treasury-curve boundary {role} series mismatch")
            if record.entity_id != f"fred_series:{series_id}":
                raise ValueError(f"Treasury-curve boundary {role} entity mismatch")
            if payload.get("observation_date") != observation_date:
                raise ValueError(f"Treasury-curve boundary {role} observation-date mismatch")
            if payload.get("vintage_date") != vintage_date:
                raise ValueError(f"Treasury-curve boundary {role} vintage-date mismatch")
            if payload.get("maturity_years") != maturity_years:
                raise ValueError(f"Treasury-curve boundary {role} maturity mismatch")
            expected_valid_from = datetime.fromisoformat(
                f"{observation_date}T00:00:00+00:00"
            )
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"Treasury-curve boundary {role} valid time mismatch")
            expected_vintage_as_of = datetime.fromisoformat(
                f"{vintage_date}T00:00:00+00:00"
            )
            if record.source.vintage_as_of != expected_vintage_as_of:
                raise ValueError(f"Treasury-curve boundary {role} source vintage mismatch")
            if (
                payload.get("availability_method")
                != "vintage_date_plus_two_calendar_days_utc"
            ):
                raise ValueError("Treasury-curve boundary availability method mismatch")
            value = payload.get("value_basis_points")
            if payload.get("unit") != "Basis Points":
                raise ValueError(f"Treasury-curve boundary {role} unit mismatch")
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(
                    f"Treasury-curve boundary {role} value must be integer basis points"
                )
            if not -1_000 <= value <= 10_000:
                raise ValueError(
                    f"Treasury-curve boundary {role} yield is outside the supported range"
                )
            reported_value = payload.get("reported_value_percent")
            if not isinstance(reported_value, str):
                raise ValueError(
                    f"Treasury-curve boundary {role} reported percent must be a string"
                )
            try:
                reported_basis_points = Decimal(reported_value) * 100
            except InvalidOperation as error:
                raise ValueError(
                    f"Treasury-curve boundary {role} reported percent must be decimal"
                ) from error
            if not reported_basis_points.is_finite() or reported_basis_points != value:
                raise ValueError(
                    f"Treasury-curve boundary {role} percent and basis points mismatch"
                )
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError(
                "lock_sha256 does not match Treasury-curve boundary input-lock content"
            )
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> TreasuryCurveBoundaryInputLock:
        """Normalize, validate, and self-hash a Treasury-curve boundary input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_treasury_curve_boundary_input_lock(path: Path) -> TreasuryCurveBoundaryInputLock:
    try:
        return TreasuryCurveBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid Treasury-curve boundary input lock: {path}") from error


def build_treasury_curve_boundary_replay_spec(
    lock: TreasuryCurveBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for a DGS10-minus-DGS2 spread boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(
                lock.decision_time,
                source_ids=[ALFRED_TREASURY_YIELD_SOURCE_ID],
            )
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the Treasury-curve boundary input lock")
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.vintage-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="native-vintage-yield-query",
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
            "The lock contains only DGS2 and DGS10 observations on two dates.",
            "ALFRED vintage dates are date-granular, so each fact uses a conservative two-day "
            "knowledge lag rather than a claimed intraday H.15 release time.",
            "The 10-year-minus-2-year spread is derived, not upstream reported.",
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
        range_width=int(metrics["spread_range_width"]),
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
                "This aggregate Treasury-curve boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no order, execution, portfolio, or allocation "
                "input is invented."
            ),
        },
        limitations=(
            "Static rendering does not independently validate the Treasury-curve boundary "
            "heuristic.",
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
            "Four actual engines ran over four locked DGS2/DGS10 facts from two native ALFRED "
            "vintages available before the decision time. Reported yields remain reported; the "
            "DGS10-minus-DGS2 spreads and next-spread range remain inferred with no assigned "
            "probability; TrialCourt rejects retrospective promotion. Two March 15 yields are "
            "held only in a disjoint post-decision event lock. This is not a forecast, calibrated "
            "interval, causal yield-curve model, "
            "policy recommendation, trading signal, production deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The spread endpoints are only the minimum and maximum of two known derived spreads; "
            "they have no probability or coverage guarantee.",
            "The March 15 yield facts are excluded from all decision inputs and artifacts.",
            "Date-granular ALFRED timing is conservative rather than intraday release evidence.",
            "Aggregate yields do not identify trades, holders, flows, or causal mechanisms.",
            "No market network, security, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: TreasuryCurveBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int | float]]:
    march08_two_year = _yield_value(records_by_role["march08_two_year"])
    march08_ten_year = _yield_value(records_by_role["march08_ten_year"])
    march13_two_year = _yield_value(records_by_role["march13_two_year"])
    march13_ten_year = _yield_value(records_by_role["march13_ten_year"])
    march08_spread = march08_ten_year - march08_two_year
    march13_spread = march13_ten_year - march13_two_year
    spread_lower = min(march08_spread, march13_spread)
    spread_upper = max(march08_spread, march13_spread)
    spread_range_width = spread_upper - spread_lower
    if spread_range_width <= 0:
        raise ValueError("two known Treasury-curve spreads must establish a nonzero range")
    bound_record_ids = tuple(
        sorted(record.record_id for record in records_by_role.values())
    )
    sources = tuple(
        record.source
        for record in sorted(
            records_by_role.values(),
            key=lambda item: item.record_id,
        )
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-spread-range",
        target_id="treasury_curve:united_states",
        variable="next_dgs10_minus_dgs2_spread_basis_points",
        unit="basis_points",
        operation=ShockOperation.SET,
        lower=float(spread_lower),
        upper=float(spread_upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=bound_record_ids,
        sources=sources,
        derivation=(
            "Subtract the reported DGS2 yield from DGS10 on each of the two dates already "
            "knowable at the decision time, then use the minimum and maximum derived spreads as "
            "transparent next-observation stress endpoints."
        ),
        limitations=(
            "Two derived spreads define a descriptive range, not a forecast, confidence interval, "
            "or stationary statistical sample.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-spread-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate the two known DGS10-minus-DGS2 endpoints using only native ALFRED vintages "
            "available at the historical decision boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, forecast, or policy interpretation.",
            "ALFRED provides date-granular vintages, so knowledge timing uses a conservative "
            "two-calendar-day lag.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    initial_state = {
        (
            "treasury_curve:united_states",
            "next_dgs10_minus_dgs2_spread_basis_points",
        ): float(march13_spread)
    }
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics: dict[str, int | float] = {
        "march08_two_year_basis_points": march08_two_year,
        "march08_ten_year_basis_points": march08_ten_year,
        "march08_spread_basis_points": march08_spread,
        "march13_two_year_basis_points": march13_two_year,
        "march13_ten_year_basis_points": march13_ten_year,
        "march13_spread_basis_points": march13_spread,
        "spread_lower_basis_points": spread_lower,
        "spread_upper_basis_points": spread_upper,
        "spread_range_width": spread_range_width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.spread-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-treasury-curve-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=bound_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_yields_and_derived_spreads": metrics,
            "naive_baseline": {
                "next_dgs10_minus_dgs2_spread_basis_points": march13_spread,
                "definition": "persistence of the latest known derived DGS10-minus-DGS2 spread",
            },
            "bound_construction": {
                "lower_spread_basis_points": spread_lower,
                "upper_spread_basis_points": spread_upper,
                "range_width_basis_points": spread_range_width,
                "endpoint_method": "minimum_and_maximum_of_two_known_derived_spreads",
                "probability_assigned": False,
                "future_event_used": False,
            },
            "program": program.model_dump(mode="json"),
            "compiled": compiled.model_dump(mode="json"),
            "applied_endpoints": {
                "lower": lower_state[
                    "treasury_curve:united_states",
                    "next_dgs10_minus_dgs2_spread_basis_points",
                ],
                "upper": upper_state[
                    "treasury_curve:united_states",
                    "next_dgs10_minus_dgs2_spread_basis_points",
                ],
            },
        },
        limitations=(
            "The two endpoints mechanically reuse only two known derived spreads.",
            "The two March 15 yield facts are absent from the bound construction and sources.",
            "A curve spread is not a direct observation of expectations, recession probability, "
            "bank stress, or policy effects.",
        ),
    )
    return artifact, metrics

def _run_trialcourt(
    *,
    lock: TreasuryCurveBoundaryInputLock,
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
            "A retrospectively constructed two-date Treasury-curve spread range qualifies for "
            "research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "DGS2 and DGS10 describe constant-maturity market yields, but two derived spreads and "
            "one later outcome cannot establish predictive validity, recession signals, banking "
            "causes, or policy effects."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="two-date DGS10-minus-DGS2 range width in basis points",
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
        output_manifest_sha256=_hash({"spread_range_width_basis_points": range_width}),
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
            "two-date-spread-difference": float(range_width),
            "two-date-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No March 15 yield fact is present in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective Treasury-curve boundary attempt must fail closed")
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
            claim_id="claim-reported-treasury-yields",
            statement=(
                "The locked ALFRED vintages report DGS2/DGS10 yields of "
                f"{int(metrics['march08_two_year_basis_points'])} and "
                f"{int(metrics['march08_ten_year_basis_points'])} basis points for March 8, "
                f"and {int(metrics['march13_two_year_basis_points'])} and "
                f"{int(metrics['march13_ten_year_basis_points'])} basis points for March 13, 2023."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are separately reported constant-maturity yields, not a reported curve "
                "spread, security trades, positions, or flows."
            ),
            limitations=(
                "The pack includes only two maturities on two observation dates.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-spread-range",
            statement=(
                "DGS10 minus DGS2 equals "
                f"{int(metrics['march08_spread_basis_points'])} basis points on March 8 and "
                f"{int(metrics['march13_spread_basis_points'])} basis points on March 13; the "
                "next-observation stress endpoints reuse those two derived values, with the "
                "latest spread as the explicit persistence baseline."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no assigned probability or coverage guarantee.",
            limitations=(
                "The March 15 yields were not used to set the interval.",
                "A two-point curve range has no recession, banking, or policy interpretation.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement=(
                "TrialCourt rejected the retrospective two-date Treasury-curve range attempt."
            ),
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

def _records_by_role(lock: TreasuryCurveBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _yield_value(record: BitemporalRecord) -> int:
    value = record.payload["value_basis_points"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("Treasury-yield value must be integer basis points")
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
