"""March 2020 Census/HUD housing-starts release boundary."""

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

CENSUS_NRC_SOURCE_ID = "census.hud.archived_new_residential_construction"
_ENTITY_ID = "census_hud_nrc:privately_owned_housing_starts_total"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HousingStartsBoundaryRoles(_StrictModel):
    """Two archived preliminary headline levels assigned to the boundary."""

    january_headline_level: str = Field(min_length=1, max_length=300)
    february_headline_level: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> HousingStartsBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("Census/HUD housing-starts role record IDs must be unique")
        return self


class HousingStartsBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision Census/HUD NRC release facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: HousingStartsBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=2, max_length=2)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> HousingStartsBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("housing-starts build_epoch cannot precede decision_time")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("housing-starts records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError("housing-starts roles must cover every locked record exactly once")
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("housing-starts source hashes must be unique and sorted")
        if {record.source.sha256 for record in self.records} != set(
            self.source_response_sha256s
        ):
            raise ValueError("housing-starts source hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected: dict[str, dict[str, Any]] = {
            "january_headline_level": {
                "release_date": "2020-02-19",
                "reference_month": "2020-01",
                "prior_month": "2019-12",
                "release_number": "CB20-26",
                "value": 1_567_000,
                "value_thousand": 1_567,
                "monthly_change": "-3.6",
                "monthly_margin": "13.3",
                "monthly_ci_zero": True,
                "yoy_change": "21.4",
                "yoy_margin": "12.2",
                "prior_revised": 1_626_000,
                "prior_previous": None,
                "revision_delta": None,
                "single_family": 1_010_000,
                "single_family_change": "-5.9",
                "single_family_margin": "11.6",
                "five_plus": 547_000,
                "rse": 5,
                "preliminary_revision_leq": "2.3",
                "timezone": "EST",
                "published_at": datetime(2020, 2, 19, 13, 30, tzinfo=UTC),
                "pdf_url": (
                    "https://www.census.gov/construction/nrc/pdf/"
                    "newresconst_202001.pdf"
                ),
                "pdf_sha256": (
                    "7aaddc9c7a6bf3655aad1bbcaa4f3a21047187115625626e94abb38ccdec191e"
                ),
            },
            "february_headline_level": {
                "release_date": "2020-03-18",
                "reference_month": "2020-02",
                "prior_month": "2020-01",
                "release_number": "CB20-41",
                "value": 1_599_000,
                "value_thousand": 1_599,
                "monthly_change": "-1.5",
                "monthly_margin": "12.4",
                "monthly_ci_zero": True,
                "yoy_change": "39.2",
                "yoy_margin": "17.7",
                "prior_revised": 1_624_000,
                "prior_previous": 1_567_000,
                "revision_delta": 57_000,
                "single_family": 1_072_000,
                "single_family_change": "6.7",
                "single_family_margin": "13.9",
                "five_plus": 508_000,
                "rse": 5,
                "preliminary_revision_leq": "2.1",
                "timezone": "EDT",
                "published_at": datetime(2020, 3, 18, 12, 30, tzinfo=UTC),
                "pdf_url": (
                    "https://www.census.gov/construction/nrc/pdf/"
                    "newresconst_202002.pdf"
                ),
                "pdf_sha256": (
                    "20042627dcaa63068a5bfd271f1fbb2880de0792103cf2fe0c084759f28f6a3e"
                ),
            },
        }
        for role, values in expected.items():
            record = by_id[getattr(self.roles, role)]
            published_at = values["published_at"]
            assert isinstance(published_at, datetime)
            if record.source.source_id != CENSUS_NRC_SOURCE_ID:
                raise ValueError("housing-starts lock accepts only archived NRC facts")
            if record.source.publisher != (
                "U.S. Census Bureau and U.S. Department of Housing and Urban Development"
            ):
                raise ValueError("housing-starts source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("housing-starts inputs must use versioned snapshots")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("housing-starts source license boundary mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("housing-starts levels must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"housing-starts {role} entity mismatch")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("housing-starts timing must be deterministic")
            if record.interval.published_at != published_at:
                raise ValueError(f"housing-starts {role} publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"housing-starts {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("housing-starts lock contains a post-decision input")
            expected_valid_from = datetime.fromisoformat(
                f"{values['reference_month']}-01T00:00:00+00:00"
            )
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"housing-starts {role} valid time mismatch")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"housing-starts {role} source vintage mismatch")
            if record.source.sha256 != values["pdf_sha256"]:
                raise ValueError(f"housing-starts {role} PDF hash mismatch")
            if str(record.source.url) != values["pdf_url"]:
                raise ValueError(f"housing-starts {role} source URL mismatch")
            expected_source_version = (
                f"CENSUS-HUD-NRC:{values['reference_month']}:"
                f"{values['release_number']}:pdf:{str(values['pdf_sha256'])[:24]}"
            )
            if record.source.source_version != expected_source_version:
                raise ValueError(f"housing-starts {role} source version mismatch")
            checks = {
                "release_date": values["release_date"],
                "reference_month": values["reference_month"],
                "release_number": values["release_number"],
                "release_series": "Monthly New Residential Construction",
                "metric": "privately_owned_total_housing_starts_sa_annual_rate",
                "value_units": values["value"],
                "value_thousand_units": values["value_thousand"],
                "reported_monthly_change_percent": values["monthly_change"],
                "reported_monthly_margin_90_percent": values["monthly_margin"],
                "reported_monthly_ci_includes_zero": values["monthly_ci_zero"],
                "reported_monthly_change_significant_at_90_percent": not values[
                    "monthly_ci_zero"
                ],
                "reported_year_over_year_change_percent": values["yoy_change"],
                "reported_year_over_year_margin_90_percent": values["yoy_margin"],
                "prior_month": values["prior_month"],
                "prior_month_revised_value_units": values["prior_revised"],
                "prior_month_revised_value_thousand_units": int(
                    values["prior_revised"]
                )
                // 1_000,
                "prior_month_value_in_previous_release_units": values["prior_previous"],
                "prior_month_revision_delta_units": values["revision_delta"],
                "single_family_starts_units": values["single_family"],
                "single_family_monthly_change_percent": values["single_family_change"],
                "single_family_monthly_margin_90_percent": values["single_family_margin"],
                "five_units_or_more_starts_units": values["five_plus"],
                "table3_average_rse_percent": values["rse"],
                "reported_average_preliminary_revision_leq_percent": values[
                    "preliminary_revision_leq"
                ],
                "release_time_local": "08:30:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": values["timezone"],
                "official_release_at": published_at.isoformat(),
                "covid_publication_standard_statement_present": False,
                "unit": "Housing Units at Seasonally Adjusted Annual Rate",
                "snapshot_semantics": "preliminary headline value in this archived release",
                "pdf_table_snapshot_verified": True,
                "release_pdf_url": values["pdf_url"],
                "release_pdf_sha256": values["pdf_sha256"],
                "release_pdf_pages": 7,
                "availability_method": "exact_time_in_pdf",
            }
            for field, expected_value in checks.items():
                if record.payload.get(field) != expected_value:
                    raise ValueError(f"housing-starts {role} {field} mismatch")
            if _level(record) != int(values["value"]):
                raise ValueError(f"housing-starts {role} headline level mismatch")
            if record.payload["value_thousand_units"] * 1_000 != _level(record):
                raise ValueError(f"housing-starts {role} unit conversion mismatch")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match housing-starts input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> HousingStartsBoundaryInputLock:
        """Normalize, validate, and self-hash a housing-starts input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_housing_starts_boundary_input_lock(path: Path) -> HousingStartsBoundaryInputLock:
    try:
        return HousingStartsBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid Census/HUD housing-starts input lock: {path}") from error


def build_housing_starts_boundary_replay_spec(
    lock: HousingStartsBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 housing-starts boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = tuple(sorted({record.source.sha256 for record in records}))
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(lock.decision_time, source_ids=[CENSUS_NRC_SOURCE_ID])
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    if tuple(record.record_id for record in selected) != record_ids:
        raise ValueError("TimeVault did not reproduce the housing-starts input lock")
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-census-hud-nrc-release-query",
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
            "value_semantics": (
                "release-time preliminary headline levels; not later revised levels"
            ),
        },
        limitations=(
            "The lock contains only two monthly NRC release snapshots.",
            "Each headline is preliminary and may be revised in a later release.",
            "Aggregate housing-starts estimates do not identify projects, builders, or places.",
            "Full seven-page PDFs remain local download evidence.",
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
        range_width=metrics["range_width_units"],
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
                "This aggregate housing-starts release boundary requires TimeVault, "
                "ShockCompiler, TrialCourt, and ReplayStudio; no project, builder, property, "
                "transaction, position, order, portfolio, or allocation input is invented."
            ),
        },
        limitations=("Static rendering does not validate the headline-level range heuristic.",),
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
            "Four actual engines ran over two locked Census/HUD NRC preliminary headline "
            "housing-starts levels available before the decision time. Reported release "
            "snapshots remain reported; the latest-persistence-or-repeat-known-headline-"
            "increase range remains inferred with no assigned probability. The two-headline "
            "difference is not the official month-over-month change, which uses a revised prior "
            "month. Official 90-percent sampling intervals are not used in the range. TrialCourt "
            "rejects retrospective promotion. The April 16 March value and February revision "
            "are held only in a disjoint post-decision event lock. This is not a forecast, "
            "calibrated interval, housing-market causal model, trading signal, production "
            "deployment, or external validation."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are latest-headline persistence or one repetition of the single "
            "known 32,000-unit release-headline increase, with no probability.",
            "The April 16 release and its February revision are excluded from every input.",
            "The official monthly percentage changes use revised prior-month denominators and "
            "are not the arithmetic basis for this release-headline stress range.",
            "Two snapshots do not identify housing causes, local variation, or a stable regime.",
            "No position, order, portfolio, allocation, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: HousingStartsBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    january = _level(records_by_role["january_headline_level"])
    february = _level(records_by_role["february_headline_level"])
    known_increase = february - january
    if known_increase <= 0:
        raise ValueError("two known NRC headline snapshots must establish a positive increase")
    lower = february
    upper = february + known_increase
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-housing-starts-level-range",
        target_id=_ENTITY_ID,
        variable="next_total_housing_starts_saar_units",
        unit="housing_units_saar",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use latest preliminary headline persistence or one repeat of the only known "
            "release-headline level increase as transparent next-release stress endpoints."
        ),
        limitations=(
            "The 32,000-unit difference compares two releases' preliminary headline levels; "
            "it is not the official monthly change against a revised prior-month level.",
            "Two release snapshots and one increase define a stress range, not a forecast or "
            "confidence interval.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-housing-starts-level-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate latest-headline persistence or one repetition of the known headline-level "
            "increase using only NRC snapshots available at the historical boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, housing-causality, or regime meaning.",
            "Official 90-percent sampling intervals are source metadata, not range inputs.",
            "The April 16 release and its revision are excluded and evaluated only afterward.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, "next_total_housing_starts_saar_units")
    initial_state = {state_key: float(february)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "january_headline_units": january,
        "february_headline_units": february,
        "known_headline_increase_units": known_increase,
        "lower_level_units": lower,
        "upper_level_units": upper,
        "range_width_units": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.housing-starts-level-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-census-hud-nrc-housing-starts-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_headline_levels": metrics,
            "naive_baseline": {
                "next_total_housing_starts_saar_units": february,
                "definition": "persistence of the latest preliminary NRC headline level",
            },
            "bound_construction": {
                "lower_level_units": lower,
                "upper_level_units": upper,
                "range_width_units": width,
                "known_headline_increase_units": known_increase,
                "endpoint_method": (
                    "latest_headline_persistence_or_repeat_known_headline_increase"
                ),
                "basis_is_release_headline_levels_not_official_monthly_change": True,
                "official_sampling_confidence_interval_used": False,
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
            "The endpoints mechanically reuse one release-headline increase.",
            "The April 16 March value and revised February value are absent from construction.",
            "The range is not an official confidence interval, probability, or causal model.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: HousingStartsBoundaryInputLock,
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
            "A retrospectively constructed one-headline-increase NRC housing-starts boundary "
            "qualifies for research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "Housing starts are reported aggregate survey estimates, but two preliminary "
            "release snapshots and one later outcome cannot establish predictive validity or "
            "housing, pandemic, policy, regional, builder, or macroeconomic causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-headline-increase NRC level range width in housing units",
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
        output_manifest_sha256=_hash({"housing_starts_range_width_units": range_width}),
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
            "known-headline-level-increase": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No April 16 NRC fact or February revision is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective housing-starts attempt must fail closed")
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
            claim_id="claim-reported-census-nrc-headline-levels",
            statement=(
                "Archived Census/HUD NRC releases report preliminary total housing-starts "
                f"SAAR headline levels of {metrics['january_headline_units']:,} units for "
                f"January and {metrics['february_headline_units']:,} units for February 2020."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are release-time preliminary aggregate estimates, not project-level "
                "observations or later revised values."
            ),
            limitations=("The pack includes only two release vintages.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-census-nrc-level-range",
            statement=(
                "The next-release stress endpoints are latest-headline persistence or one "
                f"repeat of the known {metrics['known_headline_increase_units']:,}-unit "
                f"headline increase: [{metrics['lower_level_units']:,}, "
                f"{metrics['upper_level_units']:,}] SAAR units."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary=(
                "The endpoints have no probability or coverage guarantee and are distinct "
                "from official monthly changes and sampling-confidence intervals."
            ),
            limitations=(
                "The April 16 event and its February revision were not used to set the range.",
                "The range has no housing, pandemic, policy, or regional causal interpretation.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-census-nrc-trial-rejection",
            statement=(
                "TrialCourt rejected the retrospective one-headline-increase NRC attempt."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-census-nrc-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not transactions, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-census-nrc-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _records_by_role(lock: HousingStartsBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _level(record: BitemporalRecord) -> int:
    value = record.payload["value_units"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("housing-starts headline level must be integer units")
    if not 1 <= value <= 100_000_000:
        raise ValueError("housing-starts headline level is outside supported range")
    thousand_units = record.payload.get("value_thousand_units")
    if (
        not isinstance(thousand_units, int)
        or isinstance(thousand_units, bool)
        or thousand_units * 1_000 != value
    ):
        raise ValueError("housing-starts headline units and thousand-units do not reconcile")
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
