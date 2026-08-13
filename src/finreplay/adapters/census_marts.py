"""Archived Census MARTS retail-and-food-services release adapter."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import xlrd
from pydantic import HttpUrl, TypeAdapter
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from xlrd.biffh import XLRDError
from xlrd.compdoc import CompDocError

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
_OLE_COMPOUND_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    reference_month: date
    release_number: str
    timezone_abbreviation: str
    monthly_change_percent: str
    monthly_margin_percent: str
    sales_billion: str
    adjusted_sales_million: int
    year_over_year_change_percent: str
    year_over_year_margin_percent: str
    prior_month_change_percent: str
    prior_month_previous_release_percent: str
    prior_month_margin_percent: str
    prior_month_previous_margin_percent: str
    adjusted_prior_month_sales_million: int
    pdf_pages: int
    advance_header: str
    prior_reference_label: str
    covid_publication_statement: bool = False

    @property
    def reference_label(self) -> str:
        return self.reference_month.strftime("%B %Y")


_VERIFIED_RELEASES = {
    date(2020, 2, 14): _ReleaseSpec(
        release_date=date(2020, 2, 14),
        reference_month=date(2020, 1, 1),
        release_number="CB20-22",
        timezone_abbreviation="EST",
        monthly_change_percent="0.3",
        monthly_margin_percent="0.4",
        sales_billion="529.8",
        adjusted_sales_million=529_766,
        year_over_year_change_percent="4.4",
        year_over_year_margin_percent="0.7",
        prior_month_change_percent="0.2",
        prior_month_previous_release_percent="0.3",
        prior_month_margin_percent="0.2",
        prior_month_previous_margin_percent="0.4",
        adjusted_prior_month_sales_million=528_367,
        pdf_pages=6,
        advance_header="Jan. 2020 Advance",
        prior_reference_label="Dec. 2019",
    ),
    date(2020, 3, 17): _ReleaseSpec(
        release_date=date(2020, 3, 17),
        reference_month=date(2020, 2, 1),
        release_number="CB20-36",
        timezone_abbreviation="EDT",
        monthly_change_percent="-0.5",
        monthly_margin_percent="0.4",
        sales_billion="528.1",
        adjusted_sales_million=528_113,
        year_over_year_change_percent="4.3",
        year_over_year_margin_percent="0.7",
        prior_month_change_percent="0.6",
        prior_month_previous_release_percent="0.3",
        prior_month_margin_percent="0.3",
        prior_month_previous_margin_percent="0.4",
        adjusted_prior_month_sales_million=530_930,
        pdf_pages=6,
        advance_header="Feb. 2020 Advance",
        prior_reference_label="Jan. 2020",
    ),
    date(2020, 4, 15): _ReleaseSpec(
        release_date=date(2020, 4, 15),
        reference_month=date(2020, 3, 1),
        release_number="CB20-56",
        timezone_abbreviation="EDT",
        monthly_change_percent="-8.7",
        monthly_margin_percent="0.4",
        sales_billion="483.1",
        adjusted_sales_million=483_066,
        year_over_year_change_percent="-6.2",
        year_over_year_margin_percent="0.7",
        prior_month_change_percent="-0.4",
        prior_month_previous_release_percent="-0.5",
        prior_month_margin_percent="0.2",
        prior_month_previous_margin_percent="0.4",
        adjusted_prior_month_sales_million=529_262,
        pdf_pages=7,
        advance_header="Mar. 2020 Advance",
        prior_reference_label="Feb. 2020",
        covid_publication_statement=True,
    ),
}

_VERIFIED_PAGE_DIMENSIONS = {
    date(2020, 2, 14): ((612.0, 792.0),) * 6,
    date(2020, 3, 17): ((612.0, 792.0),) * 6,
    date(2020, 4, 15): (
        (612.0, 792.0),
        (612.0, 792.0),
        (612.0, 792.0),
        (612.0, 792.0),
        (805.26, 1042.11),
        (765.0, 990.0),
        (805.26, 1042.11),
    ),
}


class CensusMARTSArchiveAdapter:
    """Retrieve one fixed 2020 Census MARTS release as paired PDF and XLS."""

    availability_rule = (
        "Each selected Census MARTS PDF states an exact 8:30 a.m. EST/EDT release time and "
        "date. FinReplay validates the timezone abbreviation against America/New_York and "
        "makes the paired PDF/XLS snapshot eligible at that exact stated time. Current server "
        "headers are retrieval metadata only and are not backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="census.marts.archived_retail_sales",
        title="Census archived Advance Monthly Retail Trade Survey releases",
        publisher="U.S. Census Bureau",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.census.gov/retail/marts/historic_releases.html"
        ),
        allowed_hosts=("www2.census.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 PDF/XLS release pairs "
            "sequentially; do not crawl or enumerate the historical release directory."
        ),
        pagination_policy=(
            "Each selection uses one complete six- or seven-page release PDF and one complete "
            "three-sheet legacy XLS workbook without pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each dated PDF/XLS pair is retained as a versioned release snapshot. The March "
            "release's revision of January from 0.3 to 0.6 percent and the April release's "
            "revision of February from -0.5 to -0.4 percent remain only in their later "
            "snapshots; earlier releases are never overwritten."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Full Census PDF and XLS releases remain in local content-addressed storage. The "
            "repository retains only minimal reported facts, URLs, hashes, attribution, and "
            "release-snapshot semantics; no redistribution right is inferred."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified Census MARTS calendar")
        self.http = http
        self.release_date = release_date
        self.spec = _VERIFIED_RELEASES[release_date]
        suffix = self.spec.reference_month.strftime("%y%m")
        self.pdf_endpoint = (
            f"https://www2.census.gov/retail/releases/historical/marts/adv{suffix}.pdf"
        )
        self.xls_endpoint = (
            f"https://www2.census.gov/retail/releases/historical/marts/rs{suffix}.xls"
        )

    def fetch(self) -> AdapterBatch:
        pdf_response, pdf_content, pdf_retrieved_at = self.http.get(
            self.pdf_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        xls_response, xls_content, xls_retrieved_at = self.http.get(
            self.xls_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(pdf_response.request_url, kind="pdf")
        self._validate_response_url(xls_response.request_url, kind="xls")
        pdf_content_type = pdf_response.headers.get("Content-Type", "").split(";", 1)[0]
        if pdf_content_type != "application/pdf":
            raise SourceSchemaError(f"unexpected MARTS PDF content type: {pdf_content_type!r}")
        xls_content_type = xls_response.headers.get("Content-Type", "").split(";", 1)[0]
        if xls_content_type not in {
            "application/vnd.ms-excel",
            "application/octet-stream",
        }:
            raise SourceSchemaError(f"unexpected MARTS XLS content type: {xls_content_type!r}")
        self._parse_pdf(pdf_content)
        workbook_facts = self._parse_xls(xls_content)
        self._crosscheck_workbook(workbook_facts)

        release_local = datetime.combine(
            self.release_date,
            time(8, 30),
            tzinfo=_NEW_YORK,
        )
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError("MARTS release timezone does not match New York calendar")
        release_at = release_local.astimezone(UTC)
        retrieved_at = max(pdf_retrieved_at, xls_retrieved_at)
        if retrieved_at < release_at:
            raise SourceSchemaError("selected MARTS release is not yet knowable")
        pdf_digest = source_response_sha256(pdf_content)
        xls_digest = source_response_sha256(xls_content)
        source_version = (
            f"CENSUS-MARTS:{self.spec.reference_month:%Y-%m}:{self.spec.release_number}:"
            f"pdf:{pdf_digest[:20]}:xls:{xls_digest[:20]}"
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
        value_basis_points = _percent_basis_points(self.spec.monthly_change_percent)
        prior_current = _percent_basis_points(self.spec.prior_month_change_percent)
        prior_previous = _percent_basis_points(self.spec.prior_month_previous_release_percent)
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.spec.reference_month:%Y%m}:monthly_change"
            ),
            entity_id="census_marts:retail_and_food_services_total",
            source=source,
            interval=BitemporalInterval(
                valid_from=datetime.combine(self.spec.reference_month, time.min, tzinfo=UTC),
                published_at=release_at,
                available_at=release_at,
                ingested_at=retrieved_at,
                availability_rule=self.availability_rule,
                availability_confidence=1.0,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload={
                "release_date": self.release_date.isoformat(),
                "reference_month": self.spec.reference_month.strftime("%Y-%m"),
                "release_number": self.spec.release_number,
                "release_series": "Advance Monthly Retail Trade Survey",
                "metric": "retail_and_food_services_monthly_change",
                "value_basis_points": value_basis_points,
                "reported_monthly_change_percent": self.spec.monthly_change_percent,
                "reported_monthly_margin_90_percent": self.spec.monthly_margin_percent,
                "reported_sales_billion_dollars": self.spec.sales_billion,
                "xls_adjusted_sales_million_dollars": self.spec.adjusted_sales_million,
                "year_over_year_change_percent": self.spec.year_over_year_change_percent,
                "year_over_year_margin_90_percent": self.spec.year_over_year_margin_percent,
                "prior_month_change_in_current_release_basis_points": prior_current,
                "prior_month_change_in_previous_release_basis_points": prior_previous,
                "prior_month_revision_delta_basis_points": prior_current - prior_previous,
                "prior_month_margin_90_percent": self.spec.prior_month_margin_percent,
                "prior_month_previous_margin_90_percent": (
                    self.spec.prior_month_previous_margin_percent
                ),
                "xls_adjusted_prior_month_sales_million_dollars": (
                    self.spec.adjusted_prior_month_sales_million
                ),
                "table3_monthly_change_median_standard_error_percent": "0.2",
                "table3_average_revision_percent": "0.1",
                "table3_median_absolute_revision_percent": "0.1",
                "release_time_local": "08:30:00",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "release_timezone": "America/New_York",
                "official_release_at": release_at.isoformat(),
                "scheduled_annual_revision_at": "2020-04-27T14:00:00+00:00",
                "covid_publication_standard_statement_present": (
                    self.spec.covid_publication_statement
                ),
                "unit": "Basis Points",
                "snapshot_semantics": "headline value reported in this archived release",
                "pdf_xls_crosscheck_verified": True,
                "pdf_table_snapshot_verified": True,
                "xls_table_snapshot_verified": True,
                "release_pdf_url": pdf_response.request_url,
                "release_pdf_sha256": pdf_digest,
                "release_pdf_pages": self.spec.pdf_pages,
                "release_xls_url": xls_response.request_url,
                "release_xls_sha256": xls_digest,
                "release_xls_sheet_names": ["Table 1.", "Table 2.", "Table 3."],
                "availability_method": "exact_time_in_pdf_and_values_crosschecked_to_xls",
            },
        )
        warnings = (
            "The exact 8:30 a.m. EST/EDT release time is stated in the PDF and validated "
            "against America/New_York; current HTTP headers are not treated as historical time.",
            "The PDF headline and legacy XLS Table 1, Table 2, and Table 3 values are validated "
            "independently before one reported record is emitted.",
            "Census margins are 90-percent sampling-error intervals and are not FinReplay "
            "forecast ranges or probability statements.",
            "The March release revises January from 0.3 to 0.6 percent, and the April release "
            "revises February from -0.5 to -0.4 percent; snapshots are never overwritten.",
            "All three releases announced an April 27 annual revision; later revised series "
            "are outside these release snapshots.",
            "The April release states that Census monitored COVID-19 response and data quality "
            "and found the release met publication standards; no pandemic causality is inferred.",
            "Full archived PDF and XLS files remain local download evidence.",
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
                record_count=1,
                source_version=source_version,
                temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(xls_response.request_url),
                retrieved_at=xls_retrieved_at,
                status_code=xls_response.status_code,
                content_type=xls_content_type,
                response_sha256=xls_digest,
                response_bytes=len(xls_content),
                record_count=0,
                source_version=source_version,
                temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
        )
        return AdapterBatch(
            records=(record,),
            receipts=receipts,
            artifacts=(
                RawArtifact(
                    sha256=pdf_digest,
                    content_type=pdf_content_type,
                    content=pdf_content,
                ),
                RawArtifact(
                    sha256=xls_digest,
                    content_type=xls_content_type,
                    content=xls_content,
                ),
            ),
        )

    def _parse_pdf(self, content: bytes) -> None:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("MARTS release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != self.spec.pdf_pages:
                raise SourceSchemaError("MARTS release PDF page count does not match")
            extracted_pages = []
            observed_dimensions = []
            for page in reader.pages:
                observed_dimensions.append(
                    (round(float(page.mediabox.width), 2), round(float(page.mediabox.height), 2))
                )
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError("MARTS release has a blank text layer")
                extracted_pages.append(extracted)
            if tuple(observed_dimensions) != _VERIFIED_PAGE_DIMENSIONS[self.release_date]:
                raise SourceSchemaError("MARTS release page dimensions do not match")
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("MARTS release PDF could not be parsed") from error
        first_page = _compact_for_match(extracted_pages[0])
        text_value = _compact_for_match(" ".join(extracted_pages))
        markers = self._pdf_markers()
        if any(_compact_for_match(marker) not in text_value for marker in markers):
            raise SourceSchemaError("MARTS PDF headline, revision, or table values do not match")
        time_marker = (
            f"FOR RELEASE AT 8:30 AM {self.spec.timezone_abbreviation}, "
            f"{self.release_date:%A, %B %d, %Y}"
        )
        if first_page.count(_compact_for_match(time_marker)) != 1:
            raise SourceSchemaError("MARTS PDF release-time identity does not match")
        if self.spec.covid_publication_statement and _compact_for_match(
            "determined estimates in this release meet publication standards"
        ) not in first_page:
            raise SourceSchemaError("MARTS COVID-19 publication-standard statement is missing")

    def _parse_xls(self, content: bytes) -> dict[str, object]:
        if not content.startswith(_OLE_COMPOUND_MAGIC):
            raise SourceSchemaError("MARTS workbook is not a legacy OLE XLS document")
        try:
            workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
            if workbook.sheet_names() != ["Table 1.", "Table 2.", "Table 3."]:
                raise SourceSchemaError("MARTS workbook sheet identity does not match")
            table1 = workbook.sheet_by_name("Table 1.")
            table2 = workbook.sheet_by_name("Table 2.")
            table3 = workbook.sheet_by_name("Table 3.")
            if (table1.nrows, table1.ncols) != (87, 14):
                raise SourceSchemaError("MARTS Table 1 dimensions do not match")
            if (table2.nrows, table2.ncols) != (89, 11):
                raise SourceSchemaError("MARTS Table 2 dimensions do not match")
            if (table3.nrows, table3.ncols) != (43, 8):
                raise SourceSchemaError("MARTS Table 3 dimensions do not match")
            if table1.cell_value(0, 0) != (
                "Table 1.  Estimated Monthly Sales for Retail and Food Services, by Kind of "
                "Business"
            ):
                raise SourceSchemaError("MARTS Table 1 title does not match")
            if table2.cell_value(0, 0) != (
                "Table 2.  Estimated Change in Monthly Sales for Retail and Food Services, by "
                "Kind of Business"
            ):
                raise SourceSchemaError("MARTS Table 2 title does not match")
            if f"Estimates {self.spec.reference_month:%b. %Y}" not in table3.cell_value(0, 0):
                raise SourceSchemaError("MARTS Table 3 release month does not match")
            if table2.cell_value(7, 2) != self.spec.advance_header:
                raise SourceSchemaError("MARTS Table 2 advance header does not match")
            if table2.cell_value(10, 2) != self.spec.prior_reference_label:
                raise SourceSchemaError("MARTS Table 2 prior-month header does not match")
            total_rows = ((table1, 10), (table2, 13), (table3, 8))
            for sheet, row in total_rows:
                if "Retail & food services" not in str(sheet.cell_value(row, 1)):
                    raise SourceSchemaError(
                        "MARTS workbook total-series row identity does not match"
                    )
            facts: dict[str, object] = {
                "adjusted_sales_million": _whole_number(table1.cell_value(11, 9)),
                "adjusted_prior_month_sales_million": _whole_number(
                    table1.cell_value(11, 10)
                ),
                "monthly_change_percent": _decimal_text(table2.cell_value(14, 2)),
                "year_over_year_change_percent": _decimal_text(table2.cell_value(14, 3)),
                "prior_month_change_percent": _decimal_text(table2.cell_value(14, 4)),
                "median_cv_percent": _decimal_text(table3.cell_value(9, 2)),
                "monthly_change_median_standard_error_percent": _decimal_text(
                    table3.cell_value(9, 3)
                ),
                "average_revision_percent": _decimal_text(table3.cell_value(9, 6)),
                "median_absolute_revision_percent": _decimal_text(table3.cell_value(9, 7)),
            }
            workbook.release_resources()
            return facts
        except SourceSchemaError:
            raise
        except (
            CompDocError,
            IndexError,
            struct.error,
            TypeError,
            ValueError,
            XLRDError,
        ) as error:
            raise SourceSchemaError("MARTS legacy XLS workbook could not be parsed") from error

    def _crosscheck_workbook(self, facts: dict[str, object]) -> None:
        expected: dict[str, object] = {
            "adjusted_sales_million": self.spec.adjusted_sales_million,
            "adjusted_prior_month_sales_million": self.spec.adjusted_prior_month_sales_million,
            "monthly_change_percent": self.spec.monthly_change_percent,
            "year_over_year_change_percent": self.spec.year_over_year_change_percent,
            "prior_month_change_percent": self.spec.prior_month_change_percent,
            "median_cv_percent": "0.7",
            "monthly_change_median_standard_error_percent": "0.2",
            "average_revision_percent": "0.1",
            "median_absolute_revision_percent": "0.1",
        }
        if facts != expected:
            raise SourceSchemaError("MARTS PDF and XLS facts do not cross-check")
        rounded_billion = (
            (Decimal(self.spec.adjusted_sales_million) / Decimal(1000))
            .quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        )
        if rounded_billion != Decimal(self.spec.sales_billion):
            raise SourceSchemaError("MARTS PDF billion-dollar headline does not round from XLS")

    def _pdf_markers(self) -> tuple[str, ...]:
        direction = (
            "an increase"
            if not self.spec.monthly_change_percent.startswith("-")
            else "a decrease"
        )
        current_word = "up" if not self.spec.prior_month_change_percent.startswith("-") else "down"
        previous_word = (
            "up" if not self.spec.prior_month_previous_release_percent.startswith("-") else "down"
        )
        current_value = self.spec.prior_month_change_percent.removeprefix("-")
        previous_value = self.spec.prior_month_previous_release_percent.removeprefix("-")
        return (
            f"ADVANCE MONTHLY SALES FOR RETAIL AND FOOD SERVICES, {self.spec.reference_label}",
            f"Release Number: {self.spec.release_number}",
            f"were ${self.spec.sales_billion} billion, {direction} of "
            f"{self.spec.monthly_change_percent.removeprefix('-')} percent "
            f"(±{self.spec.monthly_margin_percent} percent)",
            f"{self.spec.year_over_year_change_percent.removeprefix('-')} percent "
            f"(±{self.spec.year_over_year_margin_percent} percent)",
            f"was revised from {previous_word} {previous_value} percent "
            f"(±{self.spec.prior_month_previous_margin_percent} percent) to {current_word} "
            f"{current_value} percent (±{self.spec.prior_month_margin_percent} percent)",
            "Table 2. Estimated Change in Monthly Sales for Retail and Food Services",
            "Table 3. Estimated Measures of Sampling Variability and Revision to Advance Estimates",
            "scheduled for release on April 27, 2020 at 10:00 a.m. EDT",
        )

    def _validate_response_url(self, response_url: str, *, kind: str) -> None:
        parsed = urlparse(response_url)
        suffix = self.spec.reference_month.strftime("%y%m")
        expected_name = f"adv{suffix}.pdf" if kind == "pdf" else f"rs{suffix}.xls"
        expected_path = f"/retail/releases/historical/marts/{expected_name}"
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError(f"MARTS {kind.upper()} response URL does not match request")


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
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise SourceSchemaError("MARTS monthly change is not decimal") from error
    basis_points = decimal * 100
    if not decimal.is_finite() or basis_points != basis_points.to_integral_value():
        raise SourceSchemaError("MARTS monthly change does not map to whole basis points")
    result = int(basis_points)
    if not -100_000 <= result <= 100_000:
        raise SourceSchemaError("MARTS monthly change is outside the supported range")
    return result


def _decimal_text(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceSchemaError("MARTS workbook numeric cell is not numeric")
    decimal = Decimal(str(value))
    if not decimal.is_finite():
        raise SourceSchemaError("MARTS workbook numeric cell is not finite")
    return format(decimal, "f")


def _whole_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceSchemaError("MARTS workbook sales cell is not numeric")
    decimal = Decimal(str(value))
    if not decimal.is_finite() or decimal != decimal.to_integral_value():
        raise SourceSchemaError("MARTS workbook sales cell is not a whole number")
    return int(decimal)
