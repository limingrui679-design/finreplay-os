"""Archived Census Construction Spending release adapter."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO
from urllib.parse import urlparse
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

from pydantic import HttpUrl, TypeAdapter
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from finreplay.adapters.base import (
    AdapterBatch,
    AdapterMetadata,
    AuthenticationMode,
    FetchReceipt,
    RawArtifact,
    SafeHttpClient,
    SourceSchemaError,
    source_response_sha256,
)
from finreplay.contracts import (
    BitemporalInterval,
    BitemporalRecord,
    EvidenceClass,
    LicenseClass,
    SourceReference,
    TemporalCoverage,
)

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)
_NEW_YORK = ZoneInfo("America/New_York")
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_WORKBOOK_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
)
_WORKSHEET_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
)
_SHARED_STRINGS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
)


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    reference_month: date
    release_number: str
    timezone_abbreviation: str
    monthly_change_percent: str
    monthly_margin_percent: str
    year_over_year_change_percent: str
    year_over_year_margin_percent: str
    current_total_million: int
    current_private_million: int
    current_private_change_percent: str
    current_public_million: int
    current_public_change_percent: str
    prior_total_million: int
    unadjusted_current_million: int
    year_to_date_current_million: int
    year_to_date_prior_year_million: int
    year_to_date_change_percent: str
    table3_total_monthly_estimate_cv_percent: str
    table3_total_year_to_date_estimate_cv_percent: str
    table3_total_year_to_date_change_standard_error_percent: str
    table3_total_month_to_month_change_standard_error_percent: str
    table3_total_month_to_month_prior_year_standard_error_percent: str
    snapshot_levels: tuple[tuple[str, int], ...]
    snapshot_statuses: tuple[tuple[str, str], ...]
    snapshot_previous_levels: tuple[tuple[str, int | None], ...]
    workbook_sheet_names: tuple[str, ...]
    workbook_dimensions: tuple[tuple[str, str], ...]
    annual_total_current_million: int | None
    annual_total_prior_million: int | None
    annual_change_percent: str | None
    annual_cv_percent: str | None
    annual_revision_notice_present: bool
    covid_publication_standard_statement_present: bool
    future_imputation_revision_notice_present: bool

    @property
    def reference_label(self) -> str:
        return self.reference_month.strftime("%B %Y")

    @property
    def snapshot_levels_dict(self) -> dict[str, int]:
        return dict(self.snapshot_levels)

    @property
    def snapshot_statuses_dict(self) -> dict[str, str]:
        return dict(self.snapshot_statuses)

    @property
    def snapshot_previous_levels_dict(self) -> dict[str, int | None]:
        return dict(self.snapshot_previous_levels)

    @property
    def snapshot_revision_deltas_dict(self) -> dict[str, int | None]:
        levels = self.snapshot_levels_dict
        return {
            month: None if previous is None else levels[month] - previous
            for month, previous in self.snapshot_previous_levels
        }


_VERIFIED_RELEASES = {
    date(2020, 3, 2): _ReleaseSpec(
        release_date=date(2020, 3, 2),
        reference_month=date(2020, 1, 1),
        release_number="CB20-35",
        timezone_abbreviation="EST",
        monthly_change_percent="1.8",
        monthly_margin_percent="0.8",
        year_over_year_change_percent="6.8",
        year_over_year_margin_percent="1.3",
        current_total_million=1_369_223,
        current_private_million=1_022_738,
        current_private_change_percent="1.5",
        current_public_million=346_486,
        current_public_change_percent="2.6",
        prior_total_million=1_345_467,
        unadjusted_current_million=94_902,
        year_to_date_current_million=94_902,
        year_to_date_prior_year_million=88_772,
        year_to_date_change_percent="6.9",
        table3_total_monthly_estimate_cv_percent="0.7",
        table3_total_year_to_date_estimate_cv_percent="0.6",
        table3_total_year_to_date_change_standard_error_percent="0.8",
        table3_total_month_to_month_change_standard_error_percent="0.5",
        table3_total_month_to_month_prior_year_standard_error_percent="0.8",
        snapshot_levels=(("2020-01", 1_369_223),),
        snapshot_statuses=(("2020-01", "preliminary"),),
        snapshot_previous_levels=(("2020-01", None),),
        workbook_sheet_names=("Table1", "Table2", "Table3", "Table4"),
        workbook_dimensions=(
            ("Table1", "A1:I76"),
            ("Table2", "A1:J76"),
            ("Table3", "A1:I69"),
            ("Table4", "A1:E77"),
        ),
        annual_total_current_million=1_306_035,
        annual_total_prior_million=1_307_248,
        annual_change_percent="-0.1",
        annual_cv_percent="0.4",
        annual_revision_notice_present=False,
        covid_publication_standard_statement_present=False,
        future_imputation_revision_notice_present=False,
    ),
    date(2020, 4, 1): _ReleaseSpec(
        release_date=date(2020, 4, 1),
        reference_month=date(2020, 2, 1),
        release_number="CB20-48",
        timezone_abbreviation="EDT",
        monthly_change_percent="-1.3",
        monthly_margin_percent="0.8",
        year_over_year_change_percent="6.0",
        year_over_year_margin_percent="1.2",
        current_total_million=1_366_697,
        current_private_million=1_025_821,
        current_private_change_percent="-1.2",
        current_public_million=340_876,
        current_public_change_percent="-1.5",
        prior_total_million=1_384_486,
        unadjusted_current_million=96_999,
        year_to_date_current_million=193_460,
        year_to_date_prior_year_million=178_772,
        year_to_date_change_percent="8.2",
        table3_total_monthly_estimate_cv_percent="0.7",
        table3_total_year_to_date_estimate_cv_percent="0.6",
        table3_total_year_to_date_change_standard_error_percent="0.7",
        table3_total_month_to_month_change_standard_error_percent="0.5",
        table3_total_month_to_month_prior_year_standard_error_percent="0.7",
        snapshot_levels=(("2020-01", 1_384_486), ("2020-02", 1_366_697)),
        snapshot_statuses=(("2020-01", "revised"), ("2020-02", "preliminary")),
        snapshot_previous_levels=(("2020-01", 1_369_223), ("2020-02", None)),
        workbook_sheet_names=("Table1", "Table2", "Table3", "Table4"),
        workbook_dimensions=(
            ("Table1", "A1:I76"),
            ("Table2", "A1:J76"),
            ("Table3", "A1:I69"),
            ("Table4", "A1:E77"),
        ),
        annual_total_current_million=1_306_855,
        annual_total_prior_million=1_307_248,
        annual_change_percent="0.0",
        annual_cv_percent="0.4",
        annual_revision_notice_present=True,
        covid_publication_standard_statement_present=False,
        future_imputation_revision_notice_present=False,
    ),
    date(2020, 5, 1): _ReleaseSpec(
        release_date=date(2020, 5, 1),
        reference_month=date(2020, 3, 1),
        release_number="CB20-68",
        timezone_abbreviation="EDT",
        monthly_change_percent="0.9",
        monthly_margin_percent="0.8",
        year_over_year_change_percent="4.7",
        year_over_year_margin_percent="1.3",
        current_total_million=1_360_512,
        current_private_million=1_012_543,
        current_private_change_percent="0.7",
        current_public_million=347_969,
        current_public_change_percent="1.6",
        prior_total_million=1_348_386,
        unadjusted_current_million=105_193,
        year_to_date_current_million=297_021,
        year_to_date_prior_year_million=278_455,
        year_to_date_change_percent="6.7",
        table3_total_monthly_estimate_cv_percent="0.7",
        table3_total_year_to_date_estimate_cv_percent="0.5",
        table3_total_year_to_date_change_standard_error_percent="0.7",
        table3_total_month_to_month_change_standard_error_percent="0.5",
        table3_total_month_to_month_prior_year_standard_error_percent="0.8",
        snapshot_levels=(
            ("2020-01", 1_382_963),
            ("2020-02", 1_348_386),
            ("2020-03", 1_360_512),
        ),
        snapshot_statuses=(
            ("2020-01", "revised"),
            ("2020-02", "revised"),
            ("2020-03", "preliminary"),
        ),
        snapshot_previous_levels=(
            ("2020-01", 1_384_486),
            ("2020-02", 1_366_697),
            ("2020-03", None),
        ),
        workbook_sheet_names=("Table1", "Table2", "Table3"),
        workbook_dimensions=(
            ("Table1", "A1:I76"),
            ("Table2", "A1:J76"),
            ("Table3", "A1:I69"),
        ),
        annual_total_current_million=None,
        annual_total_prior_million=None,
        annual_change_percent=None,
        annual_cv_percent=None,
        annual_revision_notice_present=True,
        covid_publication_standard_statement_present=True,
        future_imputation_revision_notice_present=True,
    ),
}


class CensusC30ArchiveAdapter:
    """Retrieve one fixed 2020 Census Construction Spending PDF/XLSX pair."""

    availability_rule = (
        "Each selected Census Construction Spending PDF states an exact 10:00 a.m. EST/EDT "
        "release date and time. FinReplay validates that abbreviation against "
        "America/New_York and makes the paired PDF/XLSX snapshot eligible at the exact stated "
        "time. Current server headers are retrieval metadata only and are not backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="census.c30.archived_construction_spending",
        title="Census archived Monthly Construction Spending releases",
        publisher="U.S. Census Bureau",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.census.gov/construction/c30/prpdf.html"
        ),
        allowed_hosts=("www.census.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 PDF/XLSX release pairs "
            "sequentially; do not crawl or enumerate the historical archive."
        ),
        pagination_policy=(
            "Each selection uses one complete six-page PDF and one complete three- or "
            "four-sheet XLSX workbook without pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Every reference month is retained as a versioned release snapshot. April's "
            "revision of January and May's revisions of January and February remain separate "
            "versions; earlier decision-time values are never overwritten."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Full Census PDF and XLSX releases remain in local content-addressed storage. "
            "The repository retains only minimal reported facts, URLs, hashes, attribution, "
            "and release-snapshot semantics; no redistribution right is inferred."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified Census C30 calendar")
        self.http = http
        self.release_date = release_date
        self.spec = _VERIFIED_RELEASES[release_date]
        suffix = self.spec.reference_month.strftime("%Y%m")
        self.pdf_endpoint = f"https://www.census.gov/construction/c30/pdf/pr{suffix}.pdf"
        self.xlsx_endpoint = f"https://www.census.gov/construction/c30/xls/pr{suffix}.xlsx"

    def fetch(self) -> AdapterBatch:
        pdf_response, pdf_content, pdf_retrieved_at = self.http.get(
            self.pdf_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        xlsx_response, xlsx_content, xlsx_retrieved_at = self.http.get(
            self.xlsx_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(pdf_response.request_url, kind="pdf")
        self._validate_response_url(xlsx_response.request_url, kind="xlsx")
        pdf_content_type = pdf_response.headers.get("Content-Type", "").split(";", 1)[0]
        if pdf_content_type != "application/pdf":
            raise SourceSchemaError(f"unexpected Census C30 PDF content type: {pdf_content_type!r}")
        xlsx_content_type = xlsx_response.headers.get("Content-Type", "").split(";", 1)[0]
        if xlsx_content_type not in {
            "application/octet-stream",
            "application/zip",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }:
            raise SourceSchemaError(
                f"unexpected Census C30 XLSX content type: {xlsx_content_type!r}"
            )
        self._parse_pdf(pdf_content)
        workbook_facts = self._parse_xlsx(xlsx_content)
        self._crosscheck_workbook(workbook_facts)

        release_local = datetime.combine(self.release_date, time(10, 0), tzinfo=_NEW_YORK)
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError("Census C30 release timezone does not match New York calendar")
        release_at = release_local.astimezone(UTC)
        retrieved_at = max(pdf_retrieved_at, xlsx_retrieved_at)
        if retrieved_at < release_at:
            raise SourceSchemaError("selected Census C30 release is not yet knowable")
        pdf_digest = source_response_sha256(pdf_content)
        xlsx_digest = source_response_sha256(xlsx_content)
        source_version = (
            f"CENSUS-C30:{self.spec.reference_month:%Y-%m}:{self.spec.release_number}:"
            f"pdf:{pdf_digest[:20]}:xlsx:{xlsx_digest[:20]}"
        )
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(pdf_response.request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=pdf_digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            vintage_as_of=release_at,
            redistribution_note=self.metadata.redistribution_note,
        )
        records = self._records(
            source=source,
            release_at=release_at,
            retrieved_at=retrieved_at,
            pdf_url=pdf_response.request_url,
            pdf_sha256=pdf_digest,
            xlsx_url=xlsx_response.request_url,
            xlsx_sha256=xlsx_digest,
        )
        warnings = (
            "The exact 10:00 a.m. EST/EDT release time is stated in each PDF and validated "
            "against America/New_York; current HTTP headers are not historical timing evidence.",
            "Six complete PDF pages and every declared XLSX sheet, dimension, header, and "
            "selected table cell are validated before any reported record is emitted.",
            "Exact Table 1 million-dollar values are retained; rounded billion-dollar headline "
            "values are cross-checked but not substituted for table values.",
            "The data are annual rates adjusted for seasonality but not price changes, so they "
            "are not real construction volume or investment returns.",
            "Census 90-percent intervals cover sampling variability only and are not FinReplay "
            "forecast ranges, probabilities, or causal evidence.",
            "April announces the May annual revision, and May contains revised historical "
            "snapshots; no later value overwrites an earlier release.",
            "The May release says Census monitored COVID-19 response and data quality and found "
            "the estimates met publication standards; no pandemic causality is inferred.",
            "Full archived PDF and XLSX files remain local download evidence.",
        )
        receipts = (
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(pdf_response.request_url),
                retrieved_at=pdf_retrieved_at,
                status_code=pdf_response.status_code,
                content_type=pdf_content_type,
                response_sha256=pdf_digest,
                response_bytes=len(pdf_content),
                record_count=len(records),
                source_version=source_version,
                temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(xlsx_response.request_url),
                retrieved_at=xlsx_retrieved_at,
                status_code=xlsx_response.status_code,
                content_type=xlsx_content_type,
                response_sha256=xlsx_digest,
                response_bytes=len(xlsx_content),
                record_count=0,
                source_version=source_version,
                temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
        )
        return AdapterBatch(
            records=records,
            receipts=receipts,
            artifacts=(
                RawArtifact(
                    sha256=pdf_digest,
                    content_type=pdf_content_type,
                    content=pdf_content,
                ),
                RawArtifact(
                    sha256=xlsx_digest,
                    content_type=xlsx_content_type,
                    content=xlsx_content,
                ),
            ),
        )

    def _records(
        self,
        *,
        source: SourceReference,
        release_at: datetime,
        retrieved_at: datetime,
        pdf_url: str,
        pdf_sha256: str,
        xlsx_url: str,
        xlsx_sha256: str,
    ) -> tuple[BitemporalRecord, ...]:
        statuses = self.spec.snapshot_statuses_dict
        previous = self.spec.snapshot_previous_levels_dict
        revisions = self.spec.snapshot_revision_deltas_dict
        monthly_change_basis_points = _percent_basis_points(self.spec.monthly_change_percent)
        payload_common = {
            "release_date": self.release_date.isoformat(),
            "release_reference_month": self.spec.reference_month.strftime("%Y-%m"),
            "release_number": self.spec.release_number,
            "release_series": "Monthly Construction Spending",
            "metric": "total_construction_saar_level_million_dollars",
            "release_snapshot_total_construction_saar_million_dollars": (
                self.spec.snapshot_levels_dict
            ),
            "release_snapshot_estimate_statuses": statuses,
            "release_snapshot_previous_release_same_reference_million_dollars": previous,
            "release_snapshot_revision_delta_million_dollars": revisions,
            "reported_current_month_total_saar_million_dollars": (
                self.spec.current_total_million
            ),
            "reported_current_month_total_saar_billion_dollars": _rounded_billion(
                self.spec.current_total_million
            ),
            "reported_prior_month_revised_total_saar_million_dollars": (
                self.spec.prior_total_million
            ),
            "reported_current_month_change_percent": self.spec.monthly_change_percent,
            "reported_current_month_change_basis_points": monthly_change_basis_points,
            "reported_current_month_margin_90_percent": self.spec.monthly_margin_percent,
            "reported_current_month_change_significant_at_90_percent": True,
            "reported_year_over_year_change_percent": (
                self.spec.year_over_year_change_percent
            ),
            "reported_year_over_year_margin_90_percent": (
                self.spec.year_over_year_margin_percent
            ),
            "reported_private_saar_million_dollars": self.spec.current_private_million,
            "reported_private_monthly_change_percent": (
                self.spec.current_private_change_percent
            ),
            "reported_public_saar_million_dollars": self.spec.current_public_million,
            "reported_public_monthly_change_percent": self.spec.current_public_change_percent,
            "table2_unadjusted_current_month_million_dollars": (
                self.spec.unadjusted_current_million
            ),
            "table2_year_to_date_current_million_dollars": (
                self.spec.year_to_date_current_million
            ),
            "table2_year_to_date_prior_year_million_dollars": (
                self.spec.year_to_date_prior_year_million
            ),
            "table2_year_to_date_change_percent": self.spec.year_to_date_change_percent,
            "table3_total_monthly_estimate_cv_percent": (
                self.spec.table3_total_monthly_estimate_cv_percent
            ),
            "table3_total_year_to_date_estimate_cv_percent": (
                self.spec.table3_total_year_to_date_estimate_cv_percent
            ),
            "table3_total_year_to_date_change_standard_error_percent": (
                self.spec.table3_total_year_to_date_change_standard_error_percent
            ),
            "table3_total_month_to_month_change_standard_error_percent": (
                self.spec.table3_total_month_to_month_change_standard_error_percent
            ),
            "table3_total_month_to_month_prior_year_standard_error_percent": (
                self.spec.table3_total_month_to_month_prior_year_standard_error_percent
            ),
            "table4_present": self.spec.annual_total_current_million is not None,
            "table4_annual_total_current_million_dollars": (
                self.spec.annual_total_current_million
            ),
            "table4_annual_total_prior_million_dollars": self.spec.annual_total_prior_million,
            "table4_annual_change_percent": self.spec.annual_change_percent,
            "table4_annual_cv_percent": self.spec.annual_cv_percent,
            "release_time_local": "10:00:00",
            "release_timezone_abbreviation": self.spec.timezone_abbreviation,
            "release_timezone": "America/New_York",
            "official_release_at": release_at.isoformat(),
            "annual_revision_notice_present": self.spec.annual_revision_notice_present,
            "covid_publication_standard_statement_present": (
                self.spec.covid_publication_standard_statement_present
            ),
            "future_imputation_revision_notice_present": (
                self.spec.future_imputation_revision_notice_present
            ),
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
            "release_pdf_url": pdf_url,
            "release_pdf_sha256": pdf_sha256,
            "release_pdf_pages": 6,
            "release_pdf_page_width_points": 612,
            "release_pdf_page_height_points": 792,
            "release_pdf_page_rotation_degrees": 0,
            "release_xlsx_url": xlsx_url,
            "release_xlsx_sha256": xlsx_sha256,
            "release_xlsx_sheet_names": list(self.spec.workbook_sheet_names),
            "release_xlsx_dimensions": dict(self.spec.workbook_dimensions),
            "availability_method": "exact_time_in_pdf_and_values_crosschecked_to_xlsx",
        }
        records = []
        for month, value in self.spec.snapshot_levels:
            status = statuses[month]
            reference = date.fromisoformat(f"{month}-01")
            records.append(
                BitemporalRecord(
                    record_id=(
                        f"{self.metadata.adapter_id}:{reference:%Y%m}:"
                        "total_construction_saar_level"
                    ),
                    entity_id="census_c30:total_construction_value_put_in_place",
                    source=source,
                    interval=BitemporalInterval(
                        valid_from=datetime.combine(reference, time.min, tzinfo=UTC),
                        published_at=release_at,
                        available_at=release_at,
                        revised_at=release_at if status == "revised" else None,
                        ingested_at=retrieved_at,
                        availability_rule=self.availability_rule,
                        availability_confidence=1.0,
                    ),
                    evidence_class=EvidenceClass.REPORTED,
                    payload_schema_version="1.1.0",
                    payload={
                        **payload_common,
                        "reference_month": month,
                        "value_million_dollars": value,
                        "estimate_status": status,
                        "status_marker": "r" if status == "revised" else "p",
                        "previous_release_same_reference_value_million_dollars": previous[
                            month
                        ],
                        "revision_delta_million_dollars": revisions[month],
                    },
                )
            )
        return tuple(records)

    def _parse_pdf(self, content: bytes) -> None:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("Census C30 release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 6:
                raise SourceSchemaError("Census C30 release PDF page count does not match")
            pages = []
            for page in reader.pages:
                width = round(float(page.mediabox.width), 2)
                height = round(float(page.mediabox.height), 2)
                if (width, height) != (612.0, 792.0) or page.rotation != 0:
                    raise SourceSchemaError("Census C30 release page geometry does not match")
                text_value = page.extract_text()
                if not isinstance(text_value, str) or not text_value.strip():
                    raise SourceSchemaError("Census C30 release has a blank text layer")
                pages.append(text_value)
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("Census C30 release PDF could not be parsed") from error

        first_page = _compact_for_match(pages[0])
        time_marker = (
            f"FOR RELEASE AT 10:00 AM {self.spec.timezone_abbreviation}, "
            f"{self.release_date:%A, %B} {self.release_date.day}, {self.release_date:%Y}"
        )
        first_markers = (
            time_marker,
            f"MONTHLY CONSTRUCTION SPENDING, {self.spec.reference_label.upper()}",
            f"Release Number: {self.spec.release_number}",
            (
                f"{self.spec.reference_label.upper()} "
                f"${_rounded_billion(self.spec.current_total_million)} billion"
            ),
            (
                f"{abs(Decimal(self.spec.monthly_change_percent))} percent "
                f"(±{self.spec.monthly_margin_percent} percent) "
                f"{'above' if Decimal(self.spec.monthly_change_percent) > 0 else 'below'}"
            ),
            f"{abs(Decimal(self.spec.year_over_year_change_percent))} percent "
            f"(±{self.spec.year_over_year_margin_percent} percent)",
        )
        if any(_compact_for_match(marker) not in first_page for marker in first_markers):
            raise SourceSchemaError("Census C30 PDF release identity or headline does not match")

        if self.release_date == date(2020, 5, 1):
            page_titles = (
                "MONTHLY CONSTRUCTION SPENDING, MARCH 2020",
                "EXPLANATORY NOTES",
                "RESOURCES",
                "Table 1. Value of Construction Put in Place in the United States",
                "Table 2. Value of Construction Put in Place in the United States",
                "Table 3. Coefficients of Variation and Standard Errors",
            )
        else:
            page_titles = (
                f"MONTHLY CONSTRUCTION SPENDING, {self.spec.reference_label.upper()}",
                "EXPLANATORY NOTES",
                "Table 1. Value of Construction Put in Place in the United States",
                "Table 2. Value of Construction Put in Place in the United States",
                "Table 3. Coefficients of Variation and Standard Errors",
                "Table 4. Annual Value of Construction Put in Place in the United States",
            )
        for index, marker in enumerate(page_titles):
            if _compact_for_match(marker) not in _compact_for_match(pages[index]):
                raise SourceSchemaError("Census C30 PDF page-title sequence does not match")

        table1 = _compact_for_match(pages[3] if self.release_date == date(2020, 5, 1) else pages[2])
        for value in (
            *self.spec.snapshot_levels_dict.values(),
            self.spec.current_private_million,
            self.spec.current_public_million,
        ):
            if _compact_for_match(f"{value:,}") not in table1:
                raise SourceSchemaError("Census C30 PDF Table 1 values do not match")
        table2 = _compact_for_match(pages[4] if self.release_date == date(2020, 5, 1) else pages[3])
        for value in (
            self.spec.unadjusted_current_million,
            self.spec.year_to_date_current_million,
            self.spec.year_to_date_prior_year_million,
        ):
            if _compact_for_match(f"{value:,}") not in table2:
                raise SourceSchemaError("Census C30 PDF Table 2 values do not match")
        table3 = _compact_for_match(pages[5] if self.release_date == date(2020, 5, 1) else pages[4])
        expected_table3_row = " ".join(
            (
                "Total Construction",
                self.spec.table3_total_monthly_estimate_cv_percent,
                self.spec.table3_total_year_to_date_estimate_cv_percent,
                self.spec.table3_total_year_to_date_change_standard_error_percent,
                self.spec.table3_total_month_to_month_change_standard_error_percent,
                self.spec.table3_total_month_to_month_prior_year_standard_error_percent,
            )
        )
        if _compact_for_match(expected_table3_row) not in table3:
            raise SourceSchemaError("Census C30 PDF Table 3 values do not match")
        if self.spec.annual_total_current_million is not None:
            table4 = _compact_for_match(pages[5])
            for annual_value in (
                self.spec.annual_total_current_million,
                self.spec.annual_total_prior_million,
            ):
                assert annual_value is not None
                if _compact_for_match(f"{annual_value:,}") not in table4:
                    raise SourceSchemaError("Census C30 PDF Table 4 values do not match")
        full_text = _compact_for_match(" ".join(pages))
        annual_notice = _compact_for_match(
            "With the May 2020 release, unadjusted data will be revised back to January 2018"
        )
        if (annual_notice in full_text) is not self.spec.annual_revision_notice_present:
            raise SourceSchemaError("Census C30 annual-revision notice does not match")
        covid_marker = _compact_for_match(
            "determined estimates in this release meet publication standards"
        )
        if (
            covid_marker in full_text
        ) is not self.spec.covid_publication_standard_statement_present:
            raise SourceSchemaError("Census C30 COVID-19 publication statement does not match")
        imputation_marker = _compact_for_match(
            "will be revised to reflect changes made to the imputation methodology"
        )
        if (
            imputation_marker in full_text
        ) is not self.spec.future_imputation_revision_notice_present:
            raise SourceSchemaError("Census C30 imputation-revision notice does not match")
        required_notes = (
            "subject to sampling variability as well as nonsampling error",
            "All ranges given are 90 percent confidence intervals",
            "average absolute percent changes from preliminary estimate to first revision",
            "Data are at an annual rate, adjusted for seasonality but not price changes",
        )
        if any(_compact_for_match(marker) not in full_text for marker in required_notes):
            raise SourceSchemaError("Census C30 methodology markers do not match")

    def _parse_xlsx(self, content: bytes) -> dict[str, object]:
        sheets = _xlsx_sheets(content)
        expected_names = self.spec.workbook_sheet_names
        if tuple(sheets) != expected_names:
            raise SourceSchemaError("Census C30 workbook sheet identity does not match")
        observed_dimensions = tuple((name, sheets[name][0]) for name in expected_names)
        if observed_dimensions != self.spec.workbook_dimensions:
            raise SourceSchemaError("Census C30 workbook dimensions do not match")
        table1 = sheets["Table1"][1]
        table2 = sheets["Table2"][1]
        table3 = sheets["Table3"][1]
        facts: dict[str, object] = {
            "table1_title": table1.get("A1"),
            "table1_current_header": table1.get("B5"),
            "table1_total_label": table1.get("A7"),
            "current_total_million": _whole_number(table1.get("B7")),
            "prior_total_million": _whole_number(table1.get("C7")),
            "monthly_change_percent": _decimal_text(table1.get("H7")),
            "year_over_year_change_percent": _decimal_text(table1.get("I7")),
            "private_label": table1.get("A29"),
            "current_private_million": _whole_number(table1.get("B29")),
            "current_private_change_percent": _decimal_text(table1.get("H29")),
            "public_label": table1.get("A48"),
            "current_public_million": _whole_number(table1.get("B48")),
            "current_public_change_percent": _decimal_text(table1.get("H48")),
            "table2_title": table2.get("A1"),
            "unadjusted_current_million": _whole_number(table2.get("B7")),
            "year_to_date_current_million": _whole_number(table2.get("H7")),
            "year_to_date_prior_year_million": _whole_number(table2.get("I7")),
            "year_to_date_change_percent": _decimal_text(table2.get("J7")),
            "table3_title": table3.get("A1"),
            "monthly_estimate_cv_percent": _decimal_text(table3.get("B7")),
            "year_to_date_estimate_cv_percent": _decimal_text(table3.get("C7")),
            "year_to_date_change_standard_error_percent": _decimal_text(
                table3.get("D7")
            ),
            "month_change_standard_error_percent": _decimal_text(table3.get("E7")),
            "month_change_prior_year_standard_error_percent": _decimal_text(
                table3.get("F7")
            ),
        }
        for month, _value in self.spec.snapshot_levels:
            column = _snapshot_column(self.spec, month)
            facts[f"snapshot:{month}:value"] = _whole_number(table1.get(f"{column}7"))
        if "Table4" in sheets:
            table4 = sheets["Table4"][1]
            facts.update(
                {
                    "table4_title": table4.get("A1"),
                    "annual_total_current_million": _whole_number(table4.get("B6")),
                    "annual_total_prior_million": _whole_number(table4.get("C6")),
                    "annual_change_percent": _decimal_text(table4.get("D6")),
                    "annual_cv_percent": _decimal_text(table4.get("E6")),
                }
            )
        return facts

    def _crosscheck_workbook(self, facts: dict[str, object]) -> None:
        expected: dict[str, object] = {
            "table1_title": (
                "Table 1. Value of Construction Put in Place in the United States, "
                "Seasonally Adjusted Annual Rate"
            ),
            "table1_current_header": (
                f"{self.spec.reference_month:%b}\n"
                f"{self.spec.reference_month:%Y}p"
            ),
            "table1_total_label": "Total Construction",
            "current_total_million": self.spec.current_total_million,
            "prior_total_million": self.spec.prior_total_million,
            "monthly_change_percent": self.spec.monthly_change_percent,
            "year_over_year_change_percent": self.spec.year_over_year_change_percent,
            "private_label": "Total Private Construction1",
            "current_private_million": self.spec.current_private_million,
            "current_private_change_percent": self.spec.current_private_change_percent,
            "public_label": "Total Public Construction2",
            "current_public_million": self.spec.current_public_million,
            "current_public_change_percent": self.spec.current_public_change_percent,
            "table2_title": (
                "Table 2. Value of Construction Put in Place in the United States, "
                "Not Seasonally Adjusted"
            ),
            "unadjusted_current_million": self.spec.unadjusted_current_million,
            "year_to_date_current_million": self.spec.year_to_date_current_million,
            "year_to_date_prior_year_million": self.spec.year_to_date_prior_year_million,
            "year_to_date_change_percent": self.spec.year_to_date_change_percent,
            "table3_title": (
                "Table 3. Coefficients of Variation and Standard Errors by Type of Construction"
            ),
            "monthly_estimate_cv_percent": (
                self.spec.table3_total_monthly_estimate_cv_percent
            ),
            "year_to_date_estimate_cv_percent": (
                self.spec.table3_total_year_to_date_estimate_cv_percent
            ),
            "year_to_date_change_standard_error_percent": (
                self.spec.table3_total_year_to_date_change_standard_error_percent
            ),
            "month_change_standard_error_percent": (
                self.spec.table3_total_month_to_month_change_standard_error_percent
            ),
            "month_change_prior_year_standard_error_percent": (
                self.spec.table3_total_month_to_month_prior_year_standard_error_percent
            ),
        }
        expected.update(
            {
                f"snapshot:{month}:value": value
                for month, value in self.spec.snapshot_levels
            }
        )
        if self.spec.annual_total_current_million is not None:
            expected.update(
                {
                    "table4_title": (
                        "Table 4. Annual Value of Construction Put in Place in the United States"
                    ),
                    "annual_total_current_million": self.spec.annual_total_current_million,
                    "annual_total_prior_million": self.spec.annual_total_prior_million,
                    "annual_change_percent": self.spec.annual_change_percent,
                    "annual_cv_percent": self.spec.annual_cv_percent,
                }
        )
        if facts != expected:
            raise SourceSchemaError("Census C30 PDF and XLSX facts do not cross-check")
        calculated_change = (
            (Decimal(self.spec.current_total_million) / Decimal(self.spec.prior_total_million) - 1)
            * 100
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if calculated_change != Decimal(self.spec.monthly_change_percent):
            raise SourceSchemaError("Census C30 level arithmetic does not match reported change")
        if abs(
            self.spec.current_private_million
            + self.spec.current_public_million
            - self.spec.current_total_million
        ) > 1:
            raise SourceSchemaError("Census C30 private/public totals exceed rounding tolerance")

    def _validate_response_url(self, response_url: str, *, kind: str) -> None:
        parsed = urlparse(response_url)
        suffix = self.spec.reference_month.strftime("%Y%m")
        expected_path = (
            f"/construction/c30/pdf/pr{suffix}.pdf"
            if kind == "pdf"
            else f"/construction/c30/xls/pr{suffix}.xlsx"
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError(
                f"Census C30 {kind.upper()} response URL does not match request"
            )


def _xlsx_sheets(content: bytes) -> dict[str, tuple[str, dict[str, str]]]:
    if not content.startswith(b"PK\x03\x04"):
        raise SourceSchemaError("Census C30 workbook is not an XLSX ZIP document")
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            if not 10 <= len(infos) <= 80:
                raise SourceSchemaError("Census C30 XLSX entry count is outside bounds")
            if archive.testzip() is not None:
                raise SourceSchemaError("Census C30 XLSX CRC validation failed")
            total_uncompressed = 0
            for info in infos:
                path = info.filename
                if (
                    info.flag_bits & 1
                    or path.startswith("/")
                    or ".." in path.split("/")
                    or info.file_size > 1_000_000
                ):
                    raise SourceSchemaError("Census C30 XLSX contains an unsafe entry")
                total_uncompressed += info.file_size
                if info.file_size > 4096 and info.compress_size == 0:
                    raise SourceSchemaError("Census C30 XLSX compression metadata is invalid")
                if info.file_size > max(info.compress_size * 200, 4096):
                    raise SourceSchemaError("Census C30 XLSX compression ratio is unsafe")
            if total_uncompressed > 5_000_000:
                raise SourceSchemaError("Census C30 XLSX uncompressed size is outside bounds")
            names = set(archive.namelist())
            if len(names) != len(infos):
                raise SourceSchemaError("Census C30 XLSX entry name is duplicated")
            required = {
                "[Content_Types].xml",
                "_rels/.rels",
                "xl/workbook.xml",
                "xl/_rels/workbook.xml.rels",
                "xl/sharedStrings.xml",
            }
            if not required <= names:
                raise SourceSchemaError("Census C30 XLSX core files are missing")
            content_types_root = ElementTree.fromstring(
                archive.read("[Content_Types].xml")
            )
            overrides: dict[str, str] = {}
            for override in content_types_root.findall(
                f"{{{_CONTENT_TYPES_NS}}}Override"
            ):
                part_name = override.attrib.get("PartName", "")
                content_type = override.attrib.get("ContentType", "")
                if not part_name or not content_type or part_name in overrides:
                    raise SourceSchemaError(
                        "Census C30 XLSX content-type manifest is invalid"
                    )
                overrides[part_name] = content_type
            if (
                overrides.get("/xl/workbook.xml") != _WORKBOOK_CONTENT_TYPE
                or overrides.get("/xl/sharedStrings.xml")
                != _SHARED_STRINGS_CONTENT_TYPE
            ):
                raise SourceSchemaError(
                    "Census C30 XLSX core content types do not match"
                )
            package_relationships = ElementTree.fromstring(
                archive.read("_rels/.rels")
            )
            office_document_targets = []
            for relation in package_relationships.findall(
                f"{{{_PACKAGE_REL_NS}}}Relationship"
            ):
                target = relation.attrib.get("Target", "")
                if (
                    relation.attrib.get("TargetMode") == "External"
                    or not target
                    or target.startswith("/")
                    or "\\" in target
                    or ".." in target.split("/")
                ):
                    raise SourceSchemaError(
                        "Census C30 XLSX package relationship is unsafe"
                    )
                if relation.attrib.get("Type") == f"{_OFFICE_REL_NS}/officeDocument":
                    office_document_targets.append(target)
            if office_document_targets != ["xl/workbook.xml"]:
                raise SourceSchemaError(
                    "Census C30 XLSX workbook package relationship does not match"
                )
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relationships = ElementTree.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            )
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(
                    node.text or ""
                    for node in item.iter()
                    if node.tag == f"{{{_MAIN_NS}}}t"
                )
                for item in shared_root.findall(f"{{{_MAIN_NS}}}si")
            ]
            targets: dict[str, tuple[str, str]] = {}
            for relation in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship"):
                if relation.attrib.get("TargetMode") == "External":
                    raise SourceSchemaError("Census C30 XLSX has an external relationship")
                target = relation.attrib.get("Target", "")
                relation_id = relation.attrib.get("Id", "")
                relation_type = relation.attrib.get("Type", "")
                if relation_id and target:
                    if relation_id in targets:
                        raise SourceSchemaError(
                            "Census C30 XLSX relationship identifier is duplicated"
                        )
                    targets[relation_id] = (target, relation_type)
            sheets_element = workbook.find(f"{{{_MAIN_NS}}}sheets")
            if sheets_element is None:
                raise SourceSchemaError("Census C30 XLSX sheet manifest is missing")
            result: dict[str, tuple[str, dict[str, str]]] = {}
            for sheet in sheets_element.findall(f"{{{_MAIN_NS}}}sheet"):
                name = sheet.attrib.get("name", "")
                relation_id = sheet.attrib.get(f"{{{_OFFICE_REL_NS}}}id", "")
                target, relation_type = targets.get(relation_id, ("", ""))
                if relation_type != f"{_OFFICE_REL_NS}/worksheet":
                    raise SourceSchemaError(
                        "Census C30 XLSX sheet relationship type is invalid"
                    )
                if (
                    not target
                    or target.startswith("/")
                    or "\\" in target
                    or ".." in target.split("/")
                ):
                    raise SourceSchemaError(
                        "Census C30 XLSX sheet relationship target is unsafe"
                    )
                candidate = posixpath.normpath(target)
                normalized = (
                    candidate
                    if candidate.startswith("xl/")
                    else posixpath.join("xl", candidate)
                )
                if not normalized.startswith("xl/worksheets/") or normalized not in names:
                    raise SourceSchemaError(
                        "Census C30 XLSX sheet relationship is invalid"
                    )
                if overrides.get(f"/{normalized}") != _WORKSHEET_CONTENT_TYPE:
                    raise SourceSchemaError(
                        "Census C30 XLSX worksheet content type does not match"
                    )
                root = ElementTree.fromstring(archive.read(normalized))
                dimension = root.find(f"{{{_MAIN_NS}}}dimension")
                dimension_ref = "" if dimension is None else dimension.attrib.get("ref", "")
                cells: dict[str, str] = {}
                for cell in root.iter(f"{{{_MAIN_NS}}}c"):
                    reference = cell.attrib.get("r", "")
                    if not reference:
                        raise SourceSchemaError("Census C30 XLSX cell reference is missing")
                    value_node = cell.find(f"{{{_MAIN_NS}}}v")
                    value = "" if value_node is None else value_node.text or ""
                    cell_type = cell.attrib.get("t")
                    if cell_type == "s":
                        index = int(value)
                        if not 0 <= index < len(shared_strings):
                            raise SourceSchemaError(
                                "Census C30 XLSX shared-string index is invalid"
                            )
                        value = shared_strings[index]
                    elif cell_type == "inlineStr":
                        value = "".join(
                            node.text or ""
                            for node in cell.iter()
                            if node.tag == f"{{{_MAIN_NS}}}t"
                        )
                    elif cell_type not in {None, "n", "str"}:
                        raise SourceSchemaError("Census C30 XLSX cell type is unsupported")
                    if reference in cells:
                        raise SourceSchemaError(
                            "Census C30 XLSX cell reference is duplicated"
                        )
                    cells[reference] = value
                if name in result:
                    raise SourceSchemaError("Census C30 XLSX sheet name is duplicated")
                result[name] = (dimension_ref, cells)
            return result
    except SourceSchemaError:
        raise
    except (
        BadZipFile,
        ElementTree.ParseError,
        IndexError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        raise SourceSchemaError("Census C30 XLSX workbook could not be parsed") from error


def _snapshot_column(spec: _ReleaseSpec, month: str) -> str:
    current = spec.reference_month
    reference = date.fromisoformat(f"{month}-01")
    difference = (current.year - reference.year) * 12 + current.month - reference.month
    columns = {0: "B", 1: "C", 2: "D"}
    if difference not in columns:
        raise SourceSchemaError("Census C30 snapshot month is outside Table 1 columns")
    return columns[difference]


def _normalize_text(value: str) -> str:
    return " ".join(
        value.replace("\ufb01", "fi")
        .replace("\ufb02", "fl")
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2212", "-")
        .replace("\xa0", " ")
        .split()
    )


def _compact_for_match(value: str) -> str:
    normalized = _normalize_text(value).lower()
    return "".join(
        character
        for character in normalized
        if character.isalnum() or character in ".-+$±"
    )


def _percent_basis_points(value: str) -> int:
    if not value or len(value) > 32:
        raise SourceSchemaError("Census C30 percent change is not bounded decimal text")
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise SourceSchemaError("Census C30 percent change is not decimal") from error
    basis_points = decimal * 100
    if not decimal.is_finite() or basis_points != basis_points.to_integral_value():
        raise SourceSchemaError("Census C30 percent change does not map to whole basis points")
    result = int(basis_points)
    if not -100_000 <= result <= 100_000:
        raise SourceSchemaError("Census C30 percent change is outside the supported range")
    return result


def _decimal_text(value: object) -> str:
    if (
        isinstance(value, bool)
        or not isinstance(value, str)
        or not value
        or len(value) > 32
    ):
        raise SourceSchemaError("Census C30 workbook numeric cell is missing")
    try:
        decimal = Decimal(value)
        one_decimal = decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise SourceSchemaError("Census C30 workbook numeric cell is not decimal") from error
    if not decimal.is_finite():
        raise SourceSchemaError("Census C30 workbook numeric cell is not finite")
    if abs(decimal - one_decimal) > Decimal("0.000000000001"):
        raise SourceSchemaError(
            "Census C30 workbook numeric cell has unsupported precision"
        )
    return format(one_decimal, "f")


def _whole_number(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, str)
        or not value
        or len(value) > 32
    ):
        raise SourceSchemaError("Census C30 workbook whole-number cell is missing")
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise SourceSchemaError("Census C30 workbook whole-number cell is not decimal") from error
    if (
        not decimal.is_finite()
        or decimal != decimal.to_integral_value()
        or not Decimal(0) <= decimal <= Decimal(10_000_000)
    ):
        raise SourceSchemaError("Census C30 workbook cell is not a whole number")
    return int(decimal)


def _rounded_billion(value_million: int) -> str:
    return format(
        (Decimal(value_million) / Decimal(1000)).quantize(
            Decimal("0.1"),
            rounding=ROUND_HALF_UP,
        ),
        "f",
    )
