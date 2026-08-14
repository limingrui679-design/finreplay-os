"""March 2020 Census Construction Spending release boundary."""

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

CENSUS_C30_SOURCE_ID = "census.c30.archived_construction_spending"
_ENTITY_ID = "census_c30:total_construction_value_put_in_place"
_DECISION_TIME = datetime(2020, 4, 1, 14, 0, tzinfo=UTC)
_JANUARY_PDF_SHA256 = (
    "73d0e0ec0216d74255ebcafb316a2081a91b80ef76a34e07f6b31c79d57f9918"
)
_JANUARY_XLSX_SHA256 = (
    "a224c4f710f41c610725fe58c88bbf7263a02bfcaaeeab425cc2697cd7461f4d"
)
_FEBRUARY_PDF_SHA256 = (
    "c212b816fce0823d3e15b01c35d306253bb86280581a3a7d61421ba614dc25bb"
)
_FEBRUARY_XLSX_SHA256 = (
    "566f2267ff69d815ce4bf1ffac6206775d0e3696ea79102352444e051e405579"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConstructionSpendingBoundaryRoles(_StrictModel):
    """Two archived preliminary headline levels assigned to the boundary."""

    january_headline_level: str = Field(min_length=1, max_length=300)
    february_headline_level: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> ConstructionSpendingBoundaryRoles:
        values = tuple(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("Census construction-spending role record IDs must be unique")
        return self


class ConstructionSpendingBoundaryInputLock(_StrictModel):
    """Content-addressed pre-decision Census C30 release facts."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    artifact_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    title: str = Field(min_length=10, max_length=300)
    decision_time: datetime
    build_epoch: datetime
    roles: ConstructionSpendingBoundaryRoles
    source_response_sha256s: tuple[str, ...] = Field(min_length=4, max_length=4)
    records: tuple[BitemporalRecord, ...] = Field(min_length=2, max_length=2)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> ConstructionSpendingBoundaryInputLock:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.build_epoch, "build_epoch")
        if self.build_epoch < self.decision_time:
            raise ValueError("construction-spending build_epoch cannot precede decision_time")
        if self.decision_time != _DECISION_TIME:
            raise ValueError("construction-spending decision_time must equal the April release")
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError("construction-spending records must be unique and sorted")
        if set(record_ids) != set(self.roles.model_dump().values()):
            raise ValueError(
                "construction-spending roles must cover every locked record exactly once"
            )
        if self.source_response_sha256s != tuple(sorted(set(self.source_response_sha256s))):
            raise ValueError("construction-spending source hashes must be unique and sorted")
        if set(self.source_response_sha256s) != {
            _JANUARY_PDF_SHA256,
            _JANUARY_XLSX_SHA256,
            _FEBRUARY_PDF_SHA256,
            _FEBRUARY_XLSX_SHA256,
        }:
            raise ValueError("construction-spending source hash set does not match releases")
        if {record.source.sha256 for record in self.records} != {
            _JANUARY_PDF_SHA256,
            _FEBRUARY_PDF_SHA256,
        }:
            raise ValueError("construction-spending PDF hashes do not match locked records")
        paired_hashes = {
            str(record.payload.get(field))
            for record in self.records
            for field in ("release_pdf_sha256", "release_xlsx_sha256")
        }
        if paired_hashes != set(self.source_response_sha256s):
            raise ValueError("construction-spending paired hashes do not match locked records")
        by_id = {record.record_id: record for record in self.records}
        expected: dict[str, dict[str, Any]] = {
            "january_headline_level": {
                "release_date": "2020-03-02",
                "reference_month": "2020-01",
                "release_number": "CB20-35",
                "value": 1_369_223,
                "rounded_billion": "1369.2",
                "prior_total": 1_345_467,
                "monthly_change": "1.8",
                "monthly_change_basis_points": 180,
                "monthly_margin": "0.8",
                "yoy_change": "6.8",
                "yoy_margin": "1.3",
                "private_total": 1_022_738,
                "private_change": "1.5",
                "public_total": 346_486,
                "public_change": "2.6",
                "unadjusted": 94_902,
                "year_to_date": 94_902,
                "year_to_date_prior": 88_772,
                "year_to_date_change": "6.9",
                "table3": ("0.7", "0.6", "0.8", "0.5", "0.8"),
                "annual_total": 1_306_035,
                "annual_prior": 1_307_248,
                "annual_change": "-0.1",
                "snapshot_levels": {"2020-01": 1_369_223},
                "snapshot_statuses": {"2020-01": "preliminary"},
                "snapshot_previous": {"2020-01": None},
                "snapshot_revisions": {"2020-01": None},
                "timezone": "EST",
                "published_at": datetime(2020, 3, 2, 15, 0, tzinfo=UTC),
                "pdf_url": "https://www.census.gov/construction/c30/pdf/pr202001.pdf",
                "pdf_sha256": _JANUARY_PDF_SHA256,
                "xlsx_url": "https://www.census.gov/construction/c30/xls/pr202001.xlsx",
                "xlsx_sha256": _JANUARY_XLSX_SHA256,
                "sheet_names": ["Table1", "Table2", "Table3", "Table4"],
                "sheet_dimensions": {
                    "Table1": "A1:I76",
                    "Table2": "A1:J76",
                    "Table3": "A1:I69",
                    "Table4": "A1:E77",
                },
                "annual_notice": False,
            },
            "february_headline_level": {
                "release_date": "2020-04-01",
                "reference_month": "2020-02",
                "release_number": "CB20-48",
                "value": 1_366_697,
                "rounded_billion": "1366.7",
                "prior_total": 1_384_486,
                "monthly_change": "-1.3",
                "monthly_change_basis_points": -130,
                "monthly_margin": "0.8",
                "yoy_change": "6.0",
                "yoy_margin": "1.2",
                "private_total": 1_025_821,
                "private_change": "-1.2",
                "public_total": 340_876,
                "public_change": "-1.5",
                "unadjusted": 96_999,
                "year_to_date": 193_460,
                "year_to_date_prior": 178_772,
                "year_to_date_change": "8.2",
                "table3": ("0.7", "0.6", "0.7", "0.5", "0.7"),
                "annual_total": 1_306_855,
                "annual_prior": 1_307_248,
                "annual_change": "0.0",
                "snapshot_levels": {
                    "2020-01": 1_384_486,
                    "2020-02": 1_366_697,
                },
                "snapshot_statuses": {
                    "2020-01": "revised",
                    "2020-02": "preliminary",
                },
                "snapshot_previous": {
                    "2020-01": 1_369_223,
                    "2020-02": None,
                },
                "snapshot_revisions": {"2020-01": 15_263, "2020-02": None},
                "timezone": "EDT",
                "published_at": _DECISION_TIME,
                "pdf_url": "https://www.census.gov/construction/c30/pdf/pr202002.pdf",
                "pdf_sha256": _FEBRUARY_PDF_SHA256,
                "xlsx_url": "https://www.census.gov/construction/c30/xls/pr202002.xlsx",
                "xlsx_sha256": _FEBRUARY_XLSX_SHA256,
                "sheet_names": ["Table1", "Table2", "Table3", "Table4"],
                "sheet_dimensions": {
                    "Table1": "A1:I76",
                    "Table2": "A1:J76",
                    "Table3": "A1:I69",
                    "Table4": "A1:E77",
                },
                "annual_notice": True,
            },
        }
        for role, values in expected.items():
            record = by_id[getattr(self.roles, role)]
            published_at = values["published_at"]
            assert isinstance(published_at, datetime)
            if record.source.source_id != CENSUS_C30_SOURCE_ID:
                raise ValueError("construction-spending lock accepts only archived C30 facts")
            if record.source.publisher != "U.S. Census Bureau":
                raise ValueError("construction-spending source publisher mismatch")
            if record.source.temporal_coverage is not TemporalCoverage.VERSIONED_SNAPSHOT:
                raise ValueError("construction-spending inputs must use versioned snapshots")
            if record.source.license_class is not LicenseClass.DOWNLOAD_ONLY:
                raise ValueError("construction-spending source license boundary mismatch")
            if record.evidence_class is not EvidenceClass.REPORTED:
                raise ValueError("construction-spending levels must remain reported evidence")
            if record.entity_id != _ENTITY_ID:
                raise ValueError(f"construction-spending {role} entity mismatch")
            if record.payload_schema_version != "1.1.0":
                raise ValueError(f"construction-spending {role} payload schema mismatch")
            if record.interval.availability_confidence < 1.0:
                raise ValueError("construction-spending timing must be deterministic")
            if record.interval.published_at != published_at:
                raise ValueError(f"construction-spending {role} publication time mismatch")
            if record.interval.available_at != published_at:
                raise ValueError(f"construction-spending {role} availability time mismatch")
            if record.interval.available_at > self.decision_time:
                raise ValueError("construction-spending lock contains a post-decision input")
            if record.interval.revised_at is not None:
                raise ValueError("construction-spending inputs must be initial monthly releases")
            expected_valid_from = datetime.fromisoformat(
                f"{values['reference_month']}-01T00:00:00+00:00"
            )
            if record.interval.valid_from != expected_valid_from:
                raise ValueError(f"construction-spending {role} valid time mismatch")
            if record.source.vintage_as_of != published_at:
                raise ValueError(f"construction-spending {role} source vintage mismatch")
            if record.source.sha256 != values["pdf_sha256"]:
                raise ValueError(f"construction-spending {role} PDF hash mismatch")
            if str(record.source.url) != values["pdf_url"]:
                raise ValueError(f"construction-spending {role} source URL mismatch")
            expected_source_version = (
                f"CENSUS-C30:{values['reference_month']}:"
                f"{values['release_number']}:pdf:{str(values['pdf_sha256'])[:20]}:"
                f"xlsx:{str(values['xlsx_sha256'])[:20]}"
            )
            if record.source.source_version != expected_source_version:
                raise ValueError(f"construction-spending {role} source version mismatch")
            checks = {
                "release_date": values["release_date"],
                "release_reference_month": values["reference_month"],
                "reference_month": values["reference_month"],
                "release_number": values["release_number"],
                "release_series": "Monthly Construction Spending",
                "metric": "total_construction_saar_level_million_dollars",
                "value_million_dollars": values["value"],
                "estimate_status": "preliminary",
                "status_marker": "p",
                "previous_release_same_reference_value_million_dollars": None,
                "revision_delta_million_dollars": None,
                "reported_current_month_total_saar_million_dollars": values["value"],
                "reported_current_month_total_saar_billion_dollars": values[
                    "rounded_billion"
                ],
                "reported_prior_month_revised_total_saar_million_dollars": values[
                    "prior_total"
                ],
                "reported_current_month_change_percent": values["monthly_change"],
                "reported_current_month_change_basis_points": values[
                    "monthly_change_basis_points"
                ],
                "reported_current_month_margin_90_percent": values["monthly_margin"],
                "reported_current_month_change_significant_at_90_percent": True,
                "reported_year_over_year_change_percent": values["yoy_change"],
                "reported_year_over_year_margin_90_percent": values["yoy_margin"],
                "reported_private_saar_million_dollars": values["private_total"],
                "reported_private_monthly_change_percent": values["private_change"],
                "reported_public_saar_million_dollars": values["public_total"],
                "reported_public_monthly_change_percent": values["public_change"],
                "table2_unadjusted_current_month_million_dollars": values["unadjusted"],
                "table2_year_to_date_current_million_dollars": values["year_to_date"],
                "table2_year_to_date_prior_year_million_dollars": values[
                    "year_to_date_prior"
                ],
                "table2_year_to_date_change_percent": values["year_to_date_change"],
                "table3_total_monthly_estimate_cv_percent": values["table3"][0],
                "table3_total_year_to_date_estimate_cv_percent": values["table3"][1],
                "table3_total_year_to_date_change_standard_error_percent": values[
                    "table3"
                ][2],
                "table3_total_month_to_month_change_standard_error_percent": values[
                    "table3"
                ][3],
                "table3_total_month_to_month_prior_year_standard_error_percent": values[
                    "table3"
                ][4],
                "table4_present": True,
                "table4_annual_total_current_million_dollars": values["annual_total"],
                "table4_annual_total_prior_million_dollars": values["annual_prior"],
                "table4_annual_change_percent": values["annual_change"],
                "table4_annual_cv_percent": "0.4",
                "release_snapshot_total_construction_saar_million_dollars": values[
                    "snapshot_levels"
                ],
                "release_snapshot_estimate_statuses": values["snapshot_statuses"],
                "release_snapshot_previous_release_same_reference_million_dollars": values[
                    "snapshot_previous"
                ],
                "release_snapshot_revision_delta_million_dollars": values[
                    "snapshot_revisions"
                ],
                "release_time_local": "10:00:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": values["timezone"],
                "official_release_at": published_at.isoformat(),
                "annual_revision_notice_present": values["annual_notice"],
                "covid_publication_standard_statement_present": False,
                "future_imputation_revision_notice_present": False,
                "data_adjusted_seasonally_but_not_for_price_changes": True,
                "details_may_not_add_to_totals_due_to_rounding": True,
                "sampling_interval_semantics": "90_percent_sampling_variability_only",
                "average_absolute_preliminary_to_first_revision_percent": "1.00",
                "underlying_trend_establishment_months_total_construction": 2,
                "underlying_trend_establishment_months_specific_categories_up_to": 8,
                "unit": "Millions of Dollars at Seasonally Adjusted Annual Rate",
                "snapshot_semantics": (
                    "total construction SAAR level reported in this archived release"
                ),
                "pdf_xlsx_crosscheck_verified": True,
                "pdf_table_snapshot_verified": True,
                "xlsx_table_snapshot_verified": True,
                "release_pdf_url": values["pdf_url"],
                "release_pdf_sha256": values["pdf_sha256"],
                "release_pdf_pages": 6,
                "release_pdf_page_width_points": 612,
                "release_pdf_page_height_points": 792,
                "release_pdf_page_rotation_degrees": 0,
                "release_xlsx_url": values["xlsx_url"],
                "release_xlsx_sha256": values["xlsx_sha256"],
                "release_xlsx_sheet_names": values["sheet_names"],
                "release_xlsx_dimensions": values["sheet_dimensions"],
                "availability_method": "exact_time_in_pdf_and_values_crosschecked_to_xlsx",
            }
            if set(record.payload) != set(checks):
                raise ValueError(
                    f"construction-spending {role} payload field set mismatch"
                )
            for field, expected_value in checks.items():
                if record.payload.get(field) != expected_value:
                    raise ValueError(f"construction-spending {role} {field} mismatch")
            if _level(record) != int(values["value"]):
                raise ValueError(f"construction-spending {role} headline level mismatch")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match construction-spending input-lock content")
        return self

    @classmethod
    def create(cls, payload: dict[str, Any]) -> ConstructionSpendingBoundaryInputLock:
        """Normalize, validate, and self-hash a construction-spending input lock."""

        values = dict(payload)
        values.pop("lock_sha256", None)
        normalized = cls.model_validate(
            {**values, "lock_sha256": "0" * 64},
            context={"skip_hash": True},
        ).model_dump(mode="json", exclude={"lock_sha256"})
        return cls.model_validate({**normalized, "lock_sha256": _hash(normalized)})


def load_construction_spending_boundary_input_lock(
    path: Path,
) -> ConstructionSpendingBoundaryInputLock:
    try:
        return ConstructionSpendingBoundaryInputLock.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid Census construction-spending input lock: {path}") from error


def build_construction_spending_boundary_replay_spec(
    lock: ConstructionSpendingBoundaryInputLock,
    *,
    code_commit: str,
) -> ReplayPackSpec:
    """Run four relevant engines for the March 2020 construction-spending boundary."""

    records = lock.records
    by_role = _records_by_role(lock)
    record_ids = tuple(sorted(record.record_id for record in records))
    source_hashes = lock.source_response_sha256s
    with TimeVault() as vault:
        append_receipt = vault.append(records)
        selected = tuple(
            vault.records_as_of(lock.decision_time, source_ids=[CENSUS_C30_SOURCE_ID])
        )
        manifest = vault.manifest(generated_at=lock.build_epoch)
    selected_by_id = {record.record_id: record for record in selected}
    if set(selected_by_id) != set(record_ids):
        raise ValueError("TimeVault did not reproduce the construction-spending record IDs")
    for record in records:
        if (
            selected_by_id[record.record_id].model_dump(mode="json")
            != record.model_dump(mode="json")
        ):
            raise ValueError("TimeVault changed a construction-spending locked fact")
    prefix = lock.artifact_prefix
    timevault_artifact = ReplayArtifact.create(
        artifact_id=f"{prefix}.timevault.release-query",
        engine=EngineName.TIMEVAULT,
        artifact_kind="versioned-census-c30-release-query",
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
                "exact preliminary current-month Table 1 levels from their initial releases; "
                "later revised vintages are excluded"
            ),
        },
        limitations=(
            "The lock contains only two monthly initial-release C30 snapshots.",
            "Both values are preliminary and are revised in the May event release.",
            "Aggregate nominal construction spending does not identify projects or real volume.",
            "Full six-page PDF/XLSX pairs remain local download evidence.",
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
        range_width=metrics["range_width_million_dollars"],
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
                "This aggregate C30 boundary requires TimeVault, ShockCompiler, TrialCourt, "
                "and ReplayStudio; no project, price deflator, transaction, position, order, "
                "portfolio, or allocation input is invented."
            ),
        },
        limitations=(
            "Static rendering does not validate the preliminary-level range heuristic.",
        ),
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
            "Four actual engines ran over exact January and February preliminary total-"
            "construction SAAR levels from their initial Census C30 releases, both knowable "
            "at the April 1 decision time. Reported values remain reported; the persistence-"
            "or-repeat-known-decline stress range remains inferred with no probability. The "
            "2,526-million-dollar difference compares two initial-release current-month "
            "levels and is not Census's official February monthly change, whose denominator "
            "is a revised January level. Official 90-percent sampling intervals are excluded. "
            "TrialCourt rejects retrospective promotion. The May 1 March value and its January "
            "and February revisions stay only in a disjoint event lock. This is not a forecast, "
            "calibrated interval, real-volume or causal model, trading signal, deployment, "
            "external validation, or user-impact claim."
        ),
        limitations=(
            lock.claim_boundary,
            "The endpoints are latest preliminary-level persistence or one repetition of the "
            "single known 2,526-million-dollar initial-release decline, with no probability.",
            "The May 1 release and all of its revisions are excluded from every input.",
            "The official monthly percentage change uses a revised prior-month denominator and "
            "is not the arithmetic basis for this release-headline stress range.",
            "The figures are nominal seasonally adjusted annual rates, not price-adjusted volume.",
            "Two snapshots do not identify construction, pandemic, policy, or regional causes.",
            "No position, order, portfolio, allocation, transaction, or return is represented.",
            "A fixed build epoch and zero elapsed time preserve deterministic pack identity.",
        ),
    )


def _run_shockcompiler(
    *,
    lock: ConstructionSpendingBoundaryInputLock,
    records_by_role: dict[str, BitemporalRecord],
    source_hashes: tuple[str, ...],
    upstream: str,
) -> tuple[ReplayArtifact, dict[str, int]]:
    january = _level(records_by_role["january_headline_level"])
    february = _level(records_by_role["february_headline_level"])
    known_decline = january - february
    if known_decline <= 0:
        raise ValueError("two initial C30 levels must establish a positive known decline")
    lower = february - known_decline
    upper = february
    width = upper - lower
    record_ids = tuple(sorted(record.record_id for record in records_by_role.values()))
    sources = tuple(
        record.source
        for record in sorted(records_by_role.values(), key=lambda item: item.record_id)
    )
    variable = "next_total_construction_saar_level_million_dollars"
    parameter = ShockParameter(
        parameter_id=f"{lock.artifact_prefix}-next-construction-spending-level-range",
        target_id=_ENTITY_ID,
        variable=variable,
        unit="million_dollars_saar",
        operation=ShockOperation.SET,
        lower=float(lower),
        upper=float(upper),
        grid_points=2,
        evidence_class=EvidenceClass.INFERRED,
        source_record_ids=record_ids,
        sources=sources,
        derivation=(
            "Use February preliminary-level persistence or one repetition of the only known "
            "decline between January and February initial-release Table 1 levels."
        ),
        limitations=(
            "The 2,526-million-dollar difference compares two initial-release current-month "
            "levels; it is not the official monthly change against revised January.",
            "Two release snapshots and one decline define a stress range, not a forecast, "
            "probability, or confidence interval.",
        ),
    )
    program = ShockProgram(
        program_id=f"{lock.artifact_prefix}-next-construction-spending-level-envelope",
        scenario_id=lock.scenario_id,
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=lock.decision_time,
        parameters=(parameter,),
        hypothesis=(
            "Evaluate February preliminary-level persistence or one repetition of the known "
            "initial-release level decline using only C30 facts available at the boundary."
        ),
        global_limitations=(
            "The endpoints have no likelihood, coverage, causal, inflation, or regime meaning.",
            "Official 90-percent sampling intervals are source metadata, not range inputs.",
            "The May 1 release and all revisions are excluded and evaluated only afterward.",
        ),
    )
    compiled = ShockCompiler(max_trials=2).compile(program)
    state_key = (_ENTITY_ID, variable)
    initial_state = {state_key: float(february)}
    lower_state = ShockCompiler.apply(initial_state, compiled.trials[0])
    upper_state = ShockCompiler.apply(initial_state, compiled.trials[-1])
    metrics = {
        "january_initial_level_million_dollars": january,
        "february_initial_level_million_dollars": february,
        "known_initial_decline_million_dollars": known_decline,
        "lower_level_million_dollars": lower,
        "upper_level_million_dollars": upper,
        "range_width_million_dollars": width,
    }
    artifact = ReplayArtifact.create(
        artifact_id=f"{lock.artifact_prefix}.shockcompiler.construction-spending-level-range",
        engine=EngineName.SHOCKCOMPILER,
        artifact_kind="bounded-census-c30-construction-spending-program",
        status=ArtifactStatus.REPRODUCED,
        evidence_counts={EvidenceClass.INFERRED: 3},
        source_set_historical_replay_eligible=True,
        source_record_ids=record_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=(upstream,),
        payload={
            "input_lock_sha256": lock.lock_sha256,
            "known_initial_release_levels": metrics,
            "naive_baseline": {
                variable: february,
                "definition": "persistence of the February preliminary C30 Table 1 level",
            },
            "bound_construction": {
                "lower_level_million_dollars": lower,
                "upper_level_million_dollars": upper,
                "range_width_million_dollars": width,
                "known_initial_decline_million_dollars": known_decline,
                "endpoint_method": (
                    "latest_preliminary_level_persistence_or_repeat_known_initial_decline"
                ),
                "basis_is_initial_release_levels_not_official_monthly_change": True,
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
            "The endpoints mechanically reuse one initial-release level decline.",
            "The May 1 March value and revised January/February values are absent.",
            "The range is not an official confidence interval, probability, or causal model.",
        ),
    )
    return artifact, metrics


def _run_trialcourt(
    *,
    lock: ConstructionSpendingBoundaryInputLock,
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
            "A retrospectively constructed one-decline C30 construction-spending boundary "
            "qualifies for research eligibility after every adversarial gate."
        ),
        economic_mechanism=(
            "Construction spending is an aggregate nominal survey estimate, but two preliminary "
            "release snapshots and one later outcome cannot establish predictive validity or "
            "construction, inflation, pandemic, policy, regional, or macroeconomic causality."
        ),
        preregistered_at=lock.build_epoch,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        purge_days=2,
        embargo_days=0,
        declared_attempts=1,
        primary_metric="one-initial-decline C30 range width in million dollars SAAR",
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
        output_manifest_sha256=_hash(
            {"construction_spending_range_width_million_dollars": range_width}
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
            "known-initial-level-decline": float(range_width),
            "continuation-range-width": float(range_width),
        },
        notes=(
            "Retrospective method-boundary attempt; p=1 records no inferential test.",
            "One-dollar and zero-return fields are non-trading schema sentinels.",
            "No May 1 C30 fact or revision is in the attempt manifest.",
        ),
    )
    with TrialCourt(clock=lambda: lock.build_epoch) as court:
        registration_receipt = court.register(spec)
        attempt_receipt = court.record_attempt(attempt)
        decision = court.adjudicate(spec.trial_id, candidate_attempt_id=attempt.attempt_id)
        manifest = court.manifest()
    if decision.disposition is not TrialDisposition.REJECT:
        raise ValueError("retrospective construction-spending attempt must fail closed")
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
            claim_id="claim-reported-census-c30-initial-levels",
            statement=(
                "Archived Census C30 releases report preliminary total-construction SAAR "
                f"levels of {metrics['january_initial_level_million_dollars']:,} million "
                "dollars for January and "
                f"{metrics['february_initial_level_million_dollars']:,} million dollars "
                "for February 2020."
            ),
            evidence_class=EvidenceClass.REPORTED,
            support_artifact_ids=(by_engine[EngineName.TIMEVAULT].artifact_id,),
            boundary=(
                "These are exact Table 1 initial-release aggregate estimates, not later revised "
                "values, real volume, projects, or transactions."
            ),
            limitations=("The pack includes only two initial-release vintages.",),
        ),
        ReplayClaim(
            claim_id="claim-inferred-census-c30-level-range",
            statement=(
                "The next-release stress endpoints are February-level persistence or one "
                f"repeat of the known {metrics['known_initial_decline_million_dollars']:,}-"
                "million-dollar initial-release decline: "
                f"[{metrics['lower_level_million_dollars']:,}, "
                f"{metrics['upper_level_million_dollars']:,}] million dollars SAAR."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.SHOCKCOMPILER].artifact_id,),
            boundary=(
                "The endpoints have no probability or coverage guarantee and are distinct "
                "from Census's official monthly change and sampling-confidence intervals."
            ),
            limitations=(
                "The May 1 event and all revisions were not used to set the range.",
                "The range has no construction, inflation, pandemic, or policy causality.",
            ),
        ),
        ReplayClaim(
            claim_id="claim-inferred-census-c30-trial-rejection",
            statement=(
                "TrialCourt rejected the retrospective one-initial-decline C30 attempt."
            ),
            evidence_class=EvidenceClass.INFERRED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="This is an internal method result, not external review.",
            limitations=("The attempt makes no statistical or performance claim.",),
        ),
        ReplayClaim(
            claim_id="claim-simulated-census-c30-schema-sentinels",
            statement="Trial operational values are simulated schema sentinels only.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=(by_engine[EngineName.TRIALCOURT].artifact_id,),
            boundary="The sentinels are not transactions, capital, capacity, or returns.",
            limitations=("They exist solely to exercise mandatory adversarial checks.",),
        ),
        ReplayClaim(
            claim_id="claim-extracted-census-c30-four-engine-pack",
            statement="The pack contains outputs from the four engines relevant to this boundary.",
            evidence_class=EvidenceClass.EXTRACTED,
            support_artifact_ids=(by_engine[EngineName.REPLAYSTUDIO].artifact_id,),
            boundary="Artifact presence proves integration structure, not economic correctness.",
            limitations=("External reproduction and domain review remain separate gates.",),
        ),
    )


def _records_by_role(lock: ConstructionSpendingBoundaryInputLock) -> dict[str, BitemporalRecord]:
    by_id = {record.record_id: record for record in lock.records}
    return {role: by_id[record_id] for role, record_id in lock.roles.model_dump().items()}


def _level(record: BitemporalRecord) -> int:
    value = record.payload["value_million_dollars"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("construction-spending level must be integer million dollars")
    if not 1 <= value <= 10_000_000:
        raise ValueError("construction-spending headline level is outside supported range")
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
