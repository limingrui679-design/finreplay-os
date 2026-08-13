"""April 2020 EIA commercial-crude-stock boundary over archived WPSR releases."""

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

EIA_WPSR_SOURCE_ID = "eia.wpsr.archived_commercial_crude_stocks"
_ENTITY_ID = "eia_series:weekly_us_commercial_crude_stocks_excluding_spr"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EIACrudeStockBoundaryRoles(_StrictModel):
    """Two archived WPSR stock facts assigned to the decision boundary."""

    april03_stock: str = Field(min_length=1, max_length=300)
    april10_stock: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> EIACrudeStockBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("EIA crude-stock boundary role record IDs must be unique")
        return self


class EIACrudeStockBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision WPSR commercial-crude-stock facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: EIACrudeStockBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    release_pdf_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> EIACrudeStockBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("EIA crude-stock build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("EIA crude-stock records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("EIA crude-stock roles must cover every locked record exactly once")
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("EIA crude-stock source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("EIA crude-stock source hashes do not match locked records")
        if self.release_pdf_sha256s != tuple(sorted(set(self.release_pdf_sha256s))):
            raise ValueError("EIA crude-stock PDF hashes must be unique and sorted")
        by_id = {record.record_id: record for record in self.records}
        expected = {
            "april03_stock": {
                "release_date": "2020-04-08",
                "week_ending": "2020-04-03",
                "prior_week_ending": "2020-03-27",
                "available_at": datetime(2020, 4, 9, 4, tzinfo=UTC),
                "csv_last_modified_at": "2020-04-08T12:12:05+00:00",
                "pdf_last_modified_at": "2020-04-08T18:15:55+00:00",
            },
            "april10_stock": {
                "release_date": "2020-04-15",
                "week_ending": "2020-04-10",
                "prior_week_ending": "2020-04-03",
                "available_at": datetime(2020, 4, 16, 4, tzinfo=UTC),
                "csv_last_modified_at": "2020-04-15T22:02:44+00:00",
                "pdf_last_modified_at": "2020-04-15T22:45:00+00:00",
            },
        }
        locked_pdf_hashes: set[str] = set()
        for role, values in expected.items():
            record = by_id[getattr(self.roles, role)]
            release_date = str(values["release_date"])
            week_ending = str(values["week_ending"])
            prior_week_ending = str(values["prior_week_ending"])
            expected_available_at = values["available_at"]
            assert isinstance(expected_available_at, datetime)
            if record.source.source_id != EIA_WPSR_SOURCE_ID:
                raise ValueError("EIA crude-stock lock accepts only archived WPSR facts")
            if record.source.publisher != "U.S. Energy Information Administration":
                raise ValueError("EIA crude-stock source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("EIA crude-stock inputs must use versioned release snapshots")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("EIA crude-stock source license boundary mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("EIA crude-stock values must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"EIA crude-stock {role} entity mismatch")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("EIA crude-stock timing must be deterministic")
            if record.interval.published_at != expected_available_at:
                raise ValueError(f"EIA crude-stock {role} publication time mismatch")
            if record.interval.available_at != expected_available_at:
                raise ValueError(f"EIA crude-stock {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("EIA crude-stock lock contains a post-decision input")
            expected_valid_from = datetime.fromisoformat(f"{week_ending}T00:00:00+00:00")
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"EIA crude-stock {role} valid time mismatch")
            expected_pdf_last_modified = datetime.fromisoformat(
                str(values["pdf_last_modified_at"])
            )
            if record.source.vintage_as_of != expected_pdf_last_modified:
                raise ValueError(f"EIA crude-stock {role} source vintage mismatch")
            expected_csv_url = (
                "https://www.eia.gov/petroleum/supply/weekly/archive/2020/"
                f"{release_date.replace('-', '_')}/csv/table4.csv"
            )
            if str(record.source.url) != expected_csv_url:
                raise ValueError(f"EIA crude-stock {role} source URL mismatch")
            payload = record.payload
            if payload.get("release_date") != release_date:
                raise ValueError(f"EIA crude-stock {role} release-date mismatch")
            if payload.get("week_ending") != week_ending:
                raise ValueError(f"EIA crude-stock {role} week-ending mismatch")
            if payload.get("prior_week_ending") != prior_week_ending:
                raise ValueError(f"EIA crude-stock {role} prior-week mismatch")
            if payload.get("metric") != "commercial_crude_stocks_excluding_spr":
                raise ValueError(f"EIA crude-stock {role} metric mismatch")
            if payload.get("unit") != "Thousand Barrels":
                raise ValueError(f"EIA crude-stock {role} unit mismatch")
            if payload.get("table") != "Weekly Petroleum Status Report Table 4":
                raise ValueError(f"EIA crude-stock {role} table mismatch")
            if payload.get("arithmetic_verified") is not True:
                raise ValueError(f"EIA crude-stock {role} arithmetic flag mismatch")
            if (
                payload.get("availability_method")
                != "official_release_date_next_local_midnight"
            ):
                raise ValueError("EIA crude-stock availability method mismatch")
            if payload.get("csv_last_modified_at") != values["csv_last_modified_at"]:
                raise ValueError(f"EIA crude-stock {role} CSV modification time mismatch")
            if payload.get("pdf_last_modified_at") != values["pdf_last_modified_at"]:
                raise ValueError(f"EIA crude-stock {role} PDF modification time mismatch")
            expected_pdf_url = expected_csv_url.replace("csv/table4.csv", "pdf/wpsrall.pdf")
            if payload.get("release_pdf_url") != expected_pdf_url:
                raise ValueError(f"EIA crude-stock {role} PDF URL mismatch")
            pdf_hash = payload.get("release_pdf_sha256")
            if not isinstance(pdf_hash, str) or re_full_hash(pdf_hash) is False:
                raise ValueError(f"EIA crude-stock {role} PDF hash mismatch")
            locked_pdf_hashes.add(pdf_hash)
            current = _integer_payload(payload, "value_thousand_barrels", role)
            prior = _integer_payload(payload, "prior_value_thousand_barrels", role)
            difference = _integer_payload(
                payload,
                "reported_difference_thousand_barrels",
                role,
            )
            if current <= 0 or prior <= 0 or max(current, prior, abs(difference)) > 10_000_000:
                raise ValueError(f"EIA crude-stock {role} value is outside supported range")
            if current - prior != difference:
                raise ValueError(f"EIA crude-stock {role} values do not reconcile")
            _require_decimal_match(
                payload,
                "reported_value_million_barrels",
                current,
                role,
            )
            _require_decimal_match(
                payload,
                "reported_prior_value_million_barrels",
                prior,
                role,
            )
            _require_decimal_match(
                payload,
                "reported_difference_million_barrels",
                difference,
                role,
            )
        if locked_pdf_hashes != set(self.release_pdf_sha256s):
            raise ValueError("EIA crude-stock PDF hashes do not match locked records")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match EIA crude-stock input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> EIACrudeStockBoundaryInputLock:
        """Normalize, validate, and self-hash an EIA crude-stock input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_eia_crude_stock_boundary_input_lock(path: Path) -> EIACrudeStockBoundaryInputLock:
    try:
        return EIACrudeStockBoundaryInputLock.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid EIA crude-stock input lock: {path}") from error


def build_eia_crude_stock_boundary_replay_spec(
    lock: EIACrudeStockBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the April 2020 commercial-crude-stock boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(lock.decision_time, source_ids=[EIA_WPSR_SOURCE_ID])
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the EIA crude-stock input lock")
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-eia-wpsr-release-query",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.REPORTED: len(records)},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "release_pdf_sha256s": list(lock.release_pdf_sha256s),
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
            "The lock contains only two archived WPSR commercial-crude stock levels.",
            "Knowledge time is next-local-midnight, not an asserted exact release instant.",
            "Aggregate stocks do not identify facilities, transactions, flows, or causes.",
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
        range_width=metrics["stock_range_width_thousand_barrels"],
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
                "This aggregate commercial-crude-stock boundary requires TimeVault, "
                "ShockCompiler, TrialCourt, and ReplayStudio; no facility network, position, "
                "order, execution, portfolio, or allocation input is invented."
            ),
        },
        limitations=("Static rendering does not validate the stock-range heuristic.",),
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
            "Four actual engines ran over two locked WPSR commercial-crude-stock facts "
            "available before the decision time. Reported stocks remain reported; the next-stock "
            "range remains inferred with no assigned probability; TrialCourt rejects "
            "retrospective promotion. The April 17 stock is held only in a disjoint post-decision "
            "event lock. This is not a forecast, calibrated interval, oil-market causal model, "
            "trading signal, production deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are the minimum and maximum of two known stock levels only.",
            "The April 17 stock is excluded from every decision input and artifact.",
            "The next-local-midnight knowledge rule is conservative rather than exact.",
            "Two aggregate stock levels do not identify facilities, transactions, flows, or "
            "causes.",
            "No position, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: EIACrudeStockBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    april03 = _stock(records_by_role["april03_stock"])
    april10 = _stock(records_by_role["april10_stock"])
    lower = min(april03, april10)
    upper = max(april03, april10)
    width = upper - lower
    if width <= 0:
        raise ValueError("two known EIA stock levels must establish a nonzero range")
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-stock-range",
        target_id=_ENTITY_ID,
        variable="next_reported_commercial_crude_stocks_thousand_barrels",
        unit="thousand_barrels",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use the minimum and maximum of the two archived WPSR stock levels knowable at the "
            "decision time as transparent next-release stress endpoints."
        ),
        limitations=(
            "Two adjacent reported levels define a descriptive range, not a forecast or "
            "confidence interval.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-stock-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate the two known WPSR stock endpoints using only releases available at the "
            "historical decision boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, storage-capacity, or policy "
            "interpretation.",
            "The April 17 stock is excluded and evaluated only afterward.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, "next_reported_commercial_crude_stocks_thousand_barrels")
    initial_state = {state_key: float(april10)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "april03_stock_thousand_barrels": april03,
        "april10_stock_thousand_barrels": april10,
        "stock_lower_thousand_barrels": lower,
        "stock_upper_thousand_barrels": upper,
        "stock_range_width_thousand_barrels": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.stock-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-commercial-crude-stock-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_stocks": metrics,
            "naive_baseline": {
                "next_reported_commercial_crude_stocks_thousand_barrels": april10,
                "definition": "persistence of the latest known WPSR commercial-crude stock",
            },
            "bound_construction": {
                "lower_stock_thousand_barrels": lower,
                "upper_stock_thousand_barrels": upper,
                "range_width_thousand_barrels": width,
                "endpoint_method": "minimum_and_maximum_of_two_known_reported_stock_levels",
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
            "The endpoints mechanically reuse two known aggregate stock levels.",
            "The April 17 stock is absent from the bound construction.",
            "The range is not an inventory probability, storage-capacity model, or causal claim.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: EIACrudeStockBoundaryInputLock,
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
        trial_id=f"{lock.artifact_prefix}-retrospective-stock-screen",
        hypothesis=(
            "A retrospectively constructed two-release WPSR stock range qualifies for research "
            "eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "Commercial crude stocks summarize aggregate petroleum balances, but two reported "
            "levels and one later outcome cannot establish predictive validity or causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="two-release stock range width in thousand barrels",
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
        output_manifest_sha256=_hash(
            {"stock_range_width_thousand_barrels": range_width}
        ),
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
            "two-release-difference": float(range_width),
            "two-release-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No April 17 WPSR event fact is present in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective EIA crude-stock attempt must fail closed")
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
            claim_id="claim-reported-commercial-crude-stocks",
            statement=(
                "Archived EIA WPSR Table 4 reports U.S. commercial crude stocks excluding SPR "
                f"of {metrics['april03_stock_thousand_barrels']} thousand barrels for April 3 "
                f"and {metrics['april10_stock_thousand_barrels']} thousand barrels for April 10, "
                "2020."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are aggregate reported stock levels, not forecasts or causal estimates."
            ),
            limitations=("The pack includes only two release snapshots.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-commercial-crude-stock-range",
            statement=(
                "The next-stock stress endpoints reuse the two known values: "
                f"[{metrics['stock_lower_thousand_barrels']}, "
                f"{metrics['stock_upper_thousand_barrels']}] thousand barrels, with April 10 "
                "as the persistence baseline."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability or coverage guarantee.",
            limitations=(
                "The April 17 stock was not used to set the interval.",
                "The range has no causal, capacity, or policy interpretation.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-trial-rejection",
            statement="TrialCourt rejected the retrospective two-release stock attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external review.",
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


def _records_by_role(
    lock: EIACrudeStockBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _stock(record: BitemporalRecord) -> int:
    value = record.payload["value_thousand_barrels"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("EIA commercial-crude stock must be integer thousand barrels")
    return value


def _integer_payload(payload: dict[str, Any], field: str, role: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"EIA crude-stock {role} {field} must be an integer")
    return value


def _require_decimal_match(
    payload: dict[str, Any],
    field: str,
    expected_thousand_barrels: int,
    role: str,
) -> None:
    raw_value = payload.get(field)
    if not isinstance(raw_value, str):
        raise ValueError(f"EIA crude-stock {role} {field} must be a string")
    try:
        value = Decimal(raw_value.replace(",", "")) * 1_000
    except InvalidOperation as error:
        raise ValueError(f"EIA crude-stock {role} {field} must be decimal") from error
    if not value.is_finite() or value != expected_thousand_barrels:
        raise ValueError(f"EIA crude-stock {role} {field} does not match integer value")


def re_full_hash(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


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
