"""Treasury General Account cash-balance boundary over archived DTS reports."""

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

TREASURY_DTS_SOURCE_ID = "treasury.dts.published_report"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TGACashBoundaryRoles(_StrictModel):
    """Two dated Treasury General Account closing balances."""

    may31_closing: str = Field(min_length=1, max_length=300)
    june01_closing: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> TGACashBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("TGA cash boundary role record IDs must be unique")
        return self


class TGACashBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision TGA balances from two DTS snapshots."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: TGACashBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> TGACashBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("TGA cash boundary build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("TGA cash boundary records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("TGA cash boundary roles must cover every locked record exactly once")
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("TGA cash boundary source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("TGA cash boundary source hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected = {
            "may31_closing": (
                "2023-05-31",
                "2023-06-01",
                datetime(2023, 6, 1, 20, tzinfo=UTC),
            ),
            "june01_closing": (
                "2023-06-01",
                "2023-06-02",
                datetime(2023, 6, 2, 20, tzinfo=UTC),
            ),
        }
        for role, (report_date, publication_date, expected_available_at) in expected.items():
            record = by_id[getattr(self.roles, role)]
            if record.source.source_id != TREASURY_DTS_SOURCE_ID:
                raise ValueError("TGA cash boundary lock accepts only archived Treasury DTS facts")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("TGA cash boundary inputs must use versioned report snapshots")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("Treasury DTS balances must remain reported evidence")
            if record.entity_id != "us_treasury:treasury_general_account":
                raise ValueError(f"TGA cash boundary {role} entity mismatch")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("TGA cash boundary timing must be deterministic")
            if record.interval.published_at != expected_available_at:
                raise ValueError(f"TGA cash boundary {role} publication time mismatch")
            if record.interval.available_at != expected_available_at:
                raise ValueError(f"TGA cash boundary {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("TGA cash boundary lock contains a post-decision input")
            expected_valid_from = datetime.fromisoformat(f"{report_date}T00:00:00+00:00")
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"TGA cash boundary {role} valid time mismatch")
            if record.source.vintage_as_of != expected_valid_from:
                raise ValueError(f"TGA cash boundary {role} source vintage mismatch")
            payload = record.payload
            if payload.get("report_date") != report_date:
                raise ValueError(f"TGA cash boundary {role} report-date mismatch")
            if payload.get("publication_business_date") != publication_date:
                raise ValueError(f"TGA cash boundary {role} publication-date mismatch")
            if payload.get("metric") != "tga_closing_balance":
                raise ValueError(f"TGA cash boundary {role} metric mismatch")
            if payload.get("unit") != "Millions of Dollars":
                raise ValueError(f"TGA cash boundary {role} unit mismatch")
            if payload.get("table") != "Daily Treasury Statement Table I":
                raise ValueError(f"TGA cash boundary {role} table mismatch")
            if (
                payload.get("availability_method")
                != "official_following_business_day_deadline_1600_america_new_york"
            ):
                raise ValueError("TGA cash boundary availability method mismatch")
            if payload.get("arithmetic_verified") is not True:
                raise ValueError(f"TGA cash boundary {role} arithmetic flag mismatch")
            value = _integer_payload(payload, "value_millions", role)
            opening = _integer_payload(payload, "opening_balance_millions", role)
            deposits = _integer_payload(payload, "deposits_millions", role)
            withdrawals = _integer_payload(payload, "withdrawals_millions", role)
            balances = (value, opening, deposits, withdrawals)
            if any(item < 0 or item > 10_000_000 for item in balances):
                raise ValueError(f"TGA cash boundary {role} balance is outside supported range")
            if value <= 0:
                raise ValueError(f"TGA cash boundary {role} closing balance must be positive")
            if opening + deposits - withdrawals != value:
                raise ValueError(f"TGA cash boundary {role} balances do not reconcile")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match TGA cash boundary input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> TGACashBoundaryInputLock:
        """Normalize, validate, and self-hash a TGA cash boundary input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_tga_cash_boundary_input_lock(path: Path) -> TGACashBoundaryInputLock:
    try:
        return TGACashBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid TGA cash boundary input lock: {path}") from error


def build_tga_cash_boundary_replay_spec(
    lock: TGACashBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run the four engines relevant to a two-report TGA cash-balance boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(lock.decision_time, source_ids=[TREASURY_DTS_SOURCE_ID])
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the TGA cash boundary input lock")
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.report-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-treasury-report-query",
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
            "The lock contains only two published TGA closing balances from DTS Table I.",
            "Knowledge time is the official following-business-day 4:00 p.m. deadline, not the "
            "exact publication instant.",
            "The reports describe aggregate Treasury cash operations, not causes or forecasts.",
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
        range_width=int(metrics["balance_range_width_millions"]),
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
                "This aggregate Treasury cash boundary requires TimeVault, ShockCompiler, "
                "TrialCourt, and ReplayStudio; no security, order, portfolio, or allocation "
                "input is invented."
            ),
        },
        limitations=(
            "Static rendering does not independently validate the TGA cash-range heuristic.",
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
        claims=_claims(artifacts, metrics),
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
            "Four actual engines ran over two locked Treasury DTS Table I facts available before "
            "the decision time. Reported balances remain reported; the next-balance range remains "
            "inferred with no assigned probability; TrialCourt rejects retrospective promotion. "
            "The June 2 balance is held only in a disjoint post-decision event lock. This is not "
            "a forecast, calibrated interval, debt-limit causal model, fiscal-solvency measure, "
            "trading signal, production deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are only the minimum and maximum of two known closing balances; they "
            "have no probability or coverage guarantee.",
            "The June 2 report is excluded from every decision input and artifact.",
            "The publication deadline is a conservative knowledge time, not an exact instant.",
            "Aggregate TGA balances do not identify causal policy, counterparties, or future cash.",
            "No security, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: TGACashBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    may31 = _balance(records_by_role["may31_closing"])
    june01 = _balance(records_by_role["june01_closing"])
    lower = min(may31, june01)
    upper = max(may31, june01)
    width = upper - lower
    if width <= 0:
        raise ValueError("two known TGA closing balances must establish a nonzero range")
    bound_record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-closing-range",
        target_id="us_treasury:treasury_general_account",
        variable="next_reported_tga_closing_balance_millions",
        unit="millions_usd",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=bound_record_ids,
        sources=sources,
        derivation=(
            "Use the minimum and maximum of the two reported TGA closing balances already "
            "knowable at the decision time as transparent next-report stress endpoints."
        ),
        limitations=(
            "Two adjacent published balances define a descriptive range, not a forecast, "
            "confidence interval, or stationary statistical sample.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-closing-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate the two known DTS closing-balance endpoints using only report snapshots "
            "available at the historical decision boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, or policy interpretation.",
            "The June 2 report is excluded from construction and evaluated only afterward.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (
        "us_treasury:treasury_general_account",
        "next_reported_tga_closing_balance_millions",
    )
    initial_state = {state_key: float(june01)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "may31_closing_balance_millions": may31,
        "june01_closing_balance_millions": june01,
        "balance_lower_millions": lower,
        "balance_upper_millions": upper,
        "balance_range_width_millions": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.balance-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-tga-cash-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=bound_record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_balances": metrics,
            "naive_baseline": {
                "next_reported_tga_closing_balance_millions": june01,
                "definition": "persistence of the latest known reported TGA closing balance",
            },
            "bound_construction": {
                "lower_balance_millions": lower,
                "upper_balance_millions": upper,
                "range_width_millions": width,
                "endpoint_method": "minimum_and_maximum_of_two_known_reported_balances",
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
            "The two endpoints mechanically reuse only two known reported balances.",
            "The June 2 balance is absent from the bound construction and sources.",
            "A TGA balance range is not a debt-default probability or fiscal-solvency measure.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: TGACashBoundaryInputLock,
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
            "A retrospectively constructed two-report TGA cash range qualifies for research "
            "eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "DTS Table I reports aggregate cash operations, but two closing balances and one "
            "later outcome cannot establish predictive validity, debt-limit causality, or policy "
            "effects."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="two-report TGA closing-balance range width in millions of dollars",
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
        output_manifest_sha256=_hash({"balance_range_width_millions": range_width}),
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
            "two-report-balance-difference": float(range_width),
            "two-report-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No June 2 DTS event fact is present in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective TGA cash boundary attempt must fail closed")
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
            claim_id="claim-reported-tga-balances",
            statement=(
                "Treasury DTS Table I reports TGA closing balances of "
                f"{metrics['may31_closing_balance_millions']:,} million dollars for May 31 and "
                f"{metrics['june01_closing_balance_millions']:,} million dollars for June 1, "
                "2023."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary="These are published aggregate balances, not a forecast or causal estimate.",
            limitations=("The pack includes only two report dates and Table I balances.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-tga-range",
            statement=(
                "The next-report stress endpoints reuse the two known balances: "
                f"[{metrics['balance_lower_millions']:,}, "
                f"{metrics['balance_upper_millions']:,}] million dollars, with the June 1 "
                "balance as the explicit persistence baseline."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no assigned probability or coverage guarantee.",
            limitations=(
                "The June 2 report was not used to set the interval.",
                "The two-point range has no debt-default, solvency, or policy interpretation.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement="TrialCourt rejected the retrospective two-report TGA range attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external fiscal-domain review.",
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


def _records_by_role(lock: TGACashBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _balance(record: BitemporalRecord) -> int:
    return _integer_payload(record.payload, "value_millions", "balance")


def _integer_payload(payload: dict[str, Any], field: str, role: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"TGA cash boundary {role} {field} must be an integer")
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
