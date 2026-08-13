"""March 2020 U.S. Census MARTS retail-sales boundary."""

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

CENSUS_MARTS_SOURCE_ID = "census.marts.archived_retail_sales"
_ENTITY_ID = "census_marts:retail_and_food_services_total"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetailSalesBoundaryRoles(_StrictModel):
    """Two archived MARTS monthly-change snapshots assigned to the boundary."""

    january_monthly_change: str = Field(min_length=1, max_length=300)
    february_monthly_change: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> RetailSalesBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("MARTS retail-sales role record IDs must be unique")
        return self


class RetailSalesBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision U.S. Census MARTS release facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: RetailSalesBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(
        self,
        info: ValidationInfo,
    ) -> RetailSalesBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("MARTS retail-sales build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("MARTS retail-sales records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("MARTS retail-sales roles must cover every record exactly")
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("MARTS retail-sales source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(self.source_response_sha256s):
            raise ValueError("MARTS retail-sales source hashes do not match records")
        by_id = {record.record_id: record for record in self.records}
        expected: dict[str, dict[str, Any]] = {
            "january_monthly_change": {
                "release_date": "2020-02-14",
                "reference_month": "2020-01",
                "release_number": "CB20-22",
                "value": 30,
                "reported": "0.3",
                "margin": "0.4",
                "sales_billion": "529.8",
                "adjusted_sales_million": 529_766,
                "year_over_year": "4.4",
                "year_over_year_margin": "0.7",
                "prior_current": 20,
                "prior_previous": 30,
                "revision_delta": -10,
                "prior_margin": "0.2",
                "prior_previous_margin": "0.4",
                "adjusted_prior_sales_million": 528_367,
                "timezone": "EST",
                "published_at": datetime(2020, 2, 14, 13, 30, tzinfo=UTC),
                "pdf_url": (
                    "https://www2.census.gov/retail/releases/historical/marts/adv2001.pdf"
                ),
                "pdf_sha256": (
                    "f4d1d478abc141e5169f96ba358b012fca0d82d65cff2f83b7b69fe15ff6197d"
                ),
                "pdf_pages": 6,
                "xls_url": (
                    "https://www2.census.gov/retail/releases/historical/marts/rs2001.xls"
                ),
                "xls_sha256": (
                    "c466ee4847a324cbe0a8679d333ee434f61cae63a2281d0a7cdda8ac39a1e9f3"
                ),
            },
            "february_monthly_change": {
                "release_date": "2020-03-17",
                "reference_month": "2020-02",
                "release_number": "CB20-36",
                "value": -50,
                "reported": "-0.5",
                "margin": "0.4",
                "sales_billion": "528.1",
                "adjusted_sales_million": 528_113,
                "year_over_year": "4.3",
                "year_over_year_margin": "0.7",
                "prior_current": 60,
                "prior_previous": 30,
                "revision_delta": 30,
                "prior_margin": "0.3",
                "prior_previous_margin": "0.4",
                "adjusted_prior_sales_million": 530_930,
                "timezone": "EDT",
                "published_at": datetime(2020, 3, 17, 12, 30, tzinfo=UTC),
                "pdf_url": (
                    "https://www2.census.gov/retail/releases/historical/marts/adv2002.pdf"
                ),
                "pdf_sha256": (
                    "c78fcc9bfdba9414ac8d27eceafb417af495b942994c1dbb66c5a0d00d095aa8"
                ),
                "pdf_pages": 6,
                "xls_url": (
                    "https://www2.census.gov/retail/releases/historical/marts/rs2002.xls"
                ),
                "xls_sha256": (
                    "731f01ed7c36fc9cad9889a54787b3a08480d44b89f98c43cc02fbd316558aab"
                ),
            },
        }
        for role, values in expected.items():
            record = by_id[getattr(self.roles, role)]
            published_at = values["published_at"]
            assert isinstance(published_at, datetime)
            if record.source.source_id != CENSUS_MARTS_SOURCE_ID:
                raise ValueError("MARTS lock accepts only archived retail-sales facts")
            if record.source.publisher != "U.S. Census Bureau":
                raise ValueError("MARTS retail-sales source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("MARTS retail-sales inputs must use versioned snapshots")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("MARTS retail-sales source license boundary mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("MARTS retail-sales changes must remain reported")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"MARTS retail-sales {role} entity mismatch")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("MARTS retail-sales timing must be deterministic")
            if record.interval.published_at != published_at:
                raise ValueError(f"MARTS retail-sales {role} publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"MARTS retail-sales {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("MARTS retail-sales lock contains a post-decision input")
            expected_valid_from = datetime.fromisoformat(
                f"{values['reference_month']}-01T00:00:00+00:00"
            )
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"MARTS retail-sales {role} valid time mismatch")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"MARTS retail-sales {role} source vintage mismatch")
            if record.source.sha256 != values["pdf_sha256"]:
                raise ValueError(f"MARTS retail-sales {role} PDF hash mismatch")
            if str(record.source.url) != values["pdf_url"]:
                raise ValueError(f"MARTS retail-sales {role} source URL mismatch")
            expected_source_version = (
                f"CENSUS-MARTS:{values['reference_month']}:{values['release_number']}:"
                f"pdf:{str(values['pdf_sha256'])[:20]}:"
                f"xls:{str(values['xls_sha256'])[:20]}"
            )
            if record.source.source_version != expected_source_version:
                raise ValueError(f"MARTS retail-sales {role} source version mismatch")
            payload = record.payload
            checks = {
                "release_date": values["release_date"],
                "reference_month": values["reference_month"],
                "release_number": values["release_number"],
                "release_series": "Advance Monthly Retail Trade Survey",
                "metric": "retail_and_food_services_monthly_change",
                "value_basis_points": values["value"],
                "reported_monthly_change_percent": values["reported"],
                "reported_monthly_margin_90_percent": values["margin"],
                "reported_sales_billion_dollars": values["sales_billion"],
                "xls_adjusted_sales_million_dollars": values["adjusted_sales_million"],
                "year_over_year_change_percent": values["year_over_year"],
                "year_over_year_margin_90_percent": values["year_over_year_margin"],
                "prior_month_change_in_current_release_basis_points": values["prior_current"],
                "prior_month_change_in_previous_release_basis_points": values["prior_previous"],
                "prior_month_revision_delta_basis_points": values["revision_delta"],
                "prior_month_margin_90_percent": values["prior_margin"],
                "prior_month_previous_margin_90_percent": values["prior_previous_margin"],
                "xls_adjusted_prior_month_sales_million_dollars": values[
                    "adjusted_prior_sales_million"
                ],
                "table3_monthly_change_median_standard_error_percent": "0.2",
                "table3_average_revision_percent": "0.1",
                "table3_median_absolute_revision_percent": "0.1",
                "release_time_local": "08:30:00",
                "release_timezone_abbreviation": values["timezone"],
                "release_timezone": "America/New_York",
                "official_release_at": published_at.isoformat(),
                "scheduled_annual_revision_at": "2020-04-27T14:00:00+00:00",
                "covid_publication_standard_statement_present": False,
                "unit": "Basis Points",
                "snapshot_semantics": "headline value reported in this archived release",
                "pdf_xls_crosscheck_verified": True,
                "pdf_table_snapshot_verified": True,
                "xls_table_snapshot_verified": True,
                "release_pdf_url": values["pdf_url"],
                "release_pdf_sha256": values["pdf_sha256"],
                "release_pdf_pages": values["pdf_pages"],
                "release_xls_url": values["xls_url"],
                "release_xls_sha256": values["xls_sha256"],
                "release_xls_sheet_names": ["Table 1.", "Table 2.", "Table 3."],
                "availability_method": "exact_time_in_pdf_and_values_crosschecked_to_xls",
            }
            for field, expected_value in checks.items():
                if payload.get(field) != expected_value:
                    raise ValueError(f"MARTS retail-sales {role} {field} mismatch")
            value = _monthly_change(record)
            reported = payload.get("reported_monthly_change_percent")
            if not isinstance(reported, str):
                raise ValueError(f"MARTS retail-sales {role} reported value must be string")
            try:
                reported_basis_points = Decimal(reported) * 100
            except InvalidOperation as error:
                raise ValueError(
                    f"MARTS retail-sales {role} reported value must be decimal"
                ) from error
            if not reported_basis_points.is_finite() or reported_basis_points != value:
                raise ValueError(f"MARTS retail-sales {role} percent/basis-point mismatch")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match MARTS input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> RetailSalesBoundaryInputLock:
        """Normalize, validate, and self-hash a MARTS input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_retail_sales_boundary_input_lock(
    path: Path,
) -> RetailSalesBoundaryInputLock:
    try:
        return RetailSalesBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid MARTS retail-sales input lock: {path}") from error


def build_retail_sales_boundary_replay_spec(
    lock: RetailSalesBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 MARTS boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(lock.decision_time, source_ids=[CENSUS_MARTS_SOURCE_ID])
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the MARTS retail-sales input lock")
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-census-marts-release-query",
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
            "The lock contains only two monthly MARTS release snapshots.",
            "Each release is a preliminary/revised vintage and may later change.",
            "Aggregate retail-sales estimates do not identify individual retailers or households.",
            "Paired full PDF/XLS release files remain local download evidence.",
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
        range_width=metrics["range_width_basis_points"],
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
                "This aggregate retail-sales release boundary requires TimeVault, "
                "ShockCompiler, TrialCourt, and ReplayStudio; no retailer, household, transaction, "
                "position, order, portfolio, or allocation input is invented."
            ),
        },
        limitations=("Static rendering does not validate the monthly-change range heuristic.",),
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
            "Four actual engines ran over two locked U.S. Census MARTS monthly-change facts "
            "available before the decision time. Reported release-snapshot values remain "
            "reported; the repeat-known-decrease-or-persistence range remains inferred with no "
            "assigned probability; TrialCourt rejects retrospective promotion. The April 15 "
            "March value and its revision of February are held only in a disjoint post-decision "
            "event lock. This is not a forecast, calibrated interval, retailer or consumer causal "
            "model, trading signal, production deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are one repetition of the single known 80-basis-point decrease or "
            "latest-change persistence, with no probability.",
            "The April 15 release and its February revision are excluded from every "
            "decision input.",
            "Two release snapshots do not identify retail-sales causes or a stable regime.",
            "The range has no policy, return, or forecast interpretation.",
            "No position, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: RetailSalesBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    january = _monthly_change(records_by_role["january_monthly_change"])
    february = _monthly_change(records_by_role["february_monthly_change"])
    known_decrease = january - february
    if known_decrease <= 0:
        raise ValueError("two known MARTS releases must establish a negative monthly-change step")
    lower = february - known_decrease
    upper = february
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-retail-sales-change-range",
        target_id=_ENTITY_ID,
        variable="next_total_retail_sales_monthly_change_basis_points",
        unit="basis_points",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use one repeat of the only known release-to-release decrease or latest-change "
            "persistence as transparent next-release stress endpoints."
        ),
        limitations=(
            "Two release snapshots and one decrease define a stress range, not a forecast or "
            "confidence interval.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-retail-sales-change-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate one repetition of the known headline monthly-change decrease or latest "
            "persistence using only MARTS snapshots available at the historical boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, retail-causality, or regime meaning.",
            "The April 15 release and its revisions are excluded and evaluated only afterward.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, "next_total_retail_sales_monthly_change_basis_points")
    initial_state = {state_key: float(february)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "january_monthly_change_basis_points": january,
        "february_monthly_change_basis_points": february,
        "known_decrease_basis_points": known_decrease,
        "lower_change_basis_points": lower,
        "upper_change_basis_points": upper,
        "range_width_basis_points": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.monthly-change-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-census-marts-retail-sales-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_changes": metrics,
            "naive_baseline": {
                "next_monthly_change_basis_points": february,
                "definition": "persistence of the latest known MARTS headline monthly change",
            },
            "bound_construction": {
                "lower_change_basis_points": lower,
                "upper_change_basis_points": upper,
                "range_width_basis_points": width,
                "known_decrease_basis_points": known_decrease,
                "endpoint_method": "repeat_known_monthly_decrease_or_latest_persistence",
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
            "The endpoints mechanically reuse one known release-to-release decrease.",
            "The April 15 March value and revised February value are absent from construction.",
            "The range is not a retail-sales probability or causal explanation.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: RetailSalesBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_record_ids: tuple[str, ...],
    source_hashes: tuple[str, ...],
    range_width: int,
    upstream: tuple[str, ...],
    code_commit: str,
) -> ReplayArtifact:
    holdout_start = lock.decision_time.date() + timedelta(days=1)
    holdout_end = holdout_start + timedelta(days=45)
    spec = TrialSpec(
        trial_id=f"{lock.artifact_prefix}-retrospective-range-screen",
        hypothesis=(
            "A retrospectively constructed one-decrease MARTS retail-sales boundary "
            "qualifies for research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "Retail sales is a reported aggregate output estimate, but two release "
            "snapshots and one later outcome cannot establish predictive validity or retailer, "
            "pandemic, policy, category, or macroeconomic causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-decrease MARTS monthly-change range width in basis points",
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
        output_manifest_sha256=_hash({"monthly_change_range_width_basis_points": range_width}),
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
            "known-monthly-change-decrease": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No April 15 MARTS release fact or February revision is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective MARTS retail-sales attempt must fail closed")
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
            claim_id="claim-reported-marts-retail-sales-changes",
            statement=(
                "Paired archived U.S. Census MARTS PDF/XLS releases report total retail and "
                f"food services monthly changes of "
                f"{metrics['january_monthly_change_basis_points']} basis points for January "
                f"and {metrics['february_monthly_change_basis_points']} basis points for "
                "February 2020."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are aggregate release-snapshot estimates, not individual retailer or "
                "household observations."
            ),
            limitations=("The pack includes only two release vintages.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-marts-monthly-change-range",
            statement=(
                "The next-release stress endpoints are one repeat of the known "
                f"{metrics['known_decrease_basis_points']}-basis-point decrease or latest "
                "persistence: "
                f"[{metrics['lower_change_basis_points']}, "
                f"{metrics['upper_change_basis_points']}] basis points."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary="The endpoints have no probability or coverage guarantee.",
            limitations=(
                "The April 15 event and its February revision were not used to set the interval.",
                "The range has no pandemic, policy, sector, or retail causal interpretation.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-marts-trial-rejection",
            statement="TrialCourt rejected the retrospective one-decrease MARTS attempt.",
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-marts-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not transactions, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-marts-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _records_by_role(
    lock: RetailSalesBoundaryInputLock,
) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _monthly_change(record: BitemporalRecord) -> int:
    value = record.payload["value_basis_points"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("MARTS retail-sales monthly change must be integer basis points")
    if not -100_000 <= value <= 100_000:
        raise ValueError("MARTS retail-sales monthly change is outside supported range")
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
