"""Archived Census/BEA FT-900 international-trade release adapter."""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from typing import Any
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile
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
_EXHIBIT_MEMBER_NAMES = (
    "exh1.xls",
    "exh10.xls",
    "exh11.xls",
    "exh12.xls",
    "exh13.xls",
    "exh14.xls",
    "exh14a.xls",
    "exh15.xls",
    "exh16.xls",
    "exh16a.xls",
    "exh17.xls",
    "exh17a.xls",
    "exh18.xls",
    "exh19.xls",
    "exh1s.xls",
    "exh2.xls",
    "exh20.xls",
    "exh20a.xls",
    "exh20b.xls",
    "exh2as.xls",
    "exh2s.xls",
    "exh3.xls",
    "exh3s.xls",
    "exh4.xls",
    "exh4as.xls",
    "exh4s.xls",
    "exh5.xls",
    "exh6.xls",
    "exh7.xls",
    "exh8.xls",
    "exh9.xls",
)
_ROW_COLUMNS = (
    "balance_total",
    "balance_goods",
    "balance_services",
    "exports_total",
    "exports_goods",
    "exports_services",
    "imports_total",
    "imports_goods",
    "imports_services",
)


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    reference_month: date
    census_release_number: str
    bea_release_number: str
    timezone_abbreviation: str
    headline_deficit_billion: str
    headline_direction: str
    headline_delta_billion: str
    headline_prior_deficit_billion: str
    current_row: tuple[int, ...]
    prior_row_current_release: tuple[int, ...]
    prior_row_previous_release: tuple[int, ...]
    prior_row_label: str
    previous_release_label: str
    snapshot_deficits: tuple[tuple[str, int], ...]
    snapshot_previous_deficits: tuple[tuple[str, int | None], ...]
    next_release_date: date
    pdf_pages: int
    pdf_dimensions: tuple[tuple[float, float, int], ...]
    pdf_author: str
    pdf_creation_date: str
    covid_publication_statement: bool = False

    @property
    def reference_key(self) -> str:
        return self.reference_month.strftime("%Y-%m")

    @property
    def suffix(self) -> str:
        return self.reference_month.strftime("%y%m")

    @property
    def current_deficit_million(self) -> int:
        return -self.current_row[0]

    @property
    def prior_deficit_million(self) -> int:
        return -self.prior_row_current_release[0]

    @property
    def previous_prior_deficit_million(self) -> int:
        return -self.prior_row_previous_release[0]


_VERIFIED_RELEASES = {
    date(2020, 3, 6): _ReleaseSpec(
        release_date=date(2020, 3, 6),
        reference_month=date(2020, 1, 1),
        census_release_number="CB 20-34",
        bea_release_number="BEA 20-09",
        timezone_abbreviation="EST",
        headline_deficit_billion="45.3",
        headline_direction="down",
        headline_delta_billion="3.3",
        headline_prior_deficit_billion="48.6",
        current_row=(-45_338, -67_005, 21_668, 208_569, 136_374, 72_195, 253_906, 203_380, 50_527),
        prior_row_current_release=(
            -48_613,
            -69_652,
            21_038,
            209_476,
            137_784,
            71_692,
            258_089,
            207_436,
            50_653,
        ),
        prior_row_previous_release=(
            -48_880,
            -69_715,
            20_835,
            209_636,
            137_749,
            71_888,
            258_517,
            207_464,
            51_053,
        ),
        prior_row_label="December (R)",
        previous_release_label="December data as published last month:",
        snapshot_deficits=(("2020-01", 45_338),),
        snapshot_previous_deficits=(("2020-01", None),),
        next_release_date=date(2020, 4, 2),
        pdf_pages=63,
        pdf_dimensions=(
            (612.0, 792.0, 35),
            (874.29, 1131.43, 1),
            (886.96, 1147.83, 2),
            (900.0, 1164.71, 1),
            (1037.29, 1342.37, 2),
            (1055.17, 1365.52, 2),
            (1073.68, 1389.47, 1),
            (1092.86, 1414.29, 2),
            (1112.73, 1440.0, 3),
            (1133.33, 1466.67, 4),
            (1154.72, 1494.34, 2),
            (1176.92, 1523.08, 1),
            (1200.0, 1552.94, 3),
            (1224.0, 1584.0, 3),
            (1330.43, 1721.74, 1),
        ),
        pdf_author="kebed001",
        pdf_creation_date="D:20200305152149-05'00'",
    ),
    date(2020, 4, 2): _ReleaseSpec(
        release_date=date(2020, 4, 2),
        reference_month=date(2020, 2, 1),
        census_release_number="CB 20-52",
        bea_release_number="BEA 20-16",
        timezone_abbreviation="EDT",
        headline_deficit_billion="39.9",
        headline_direction="down",
        headline_delta_billion="5.5",
        headline_prior_deficit_billion="45.5",
        current_row=(-39_932, -61_212, 21_280, 207_543, 137_203, 70_341, 247_476, 198_415, 49_061),
        prior_row_current_release=(
            -45_482,
            -67_122,
            21_640,
            208_307,
            136_251,
            72_056,
            253_790,
            203_374,
            50_416,
        ),
        prior_row_previous_release=(
            -45_338,
            -67_005,
            21_668,
            208_569,
            136_374,
            72_195,
            253_906,
            203_380,
            50_527,
        ),
        prior_row_label="January (R)",
        previous_release_label="January data as published last month:",
        snapshot_deficits=(("2020-01", 45_482), ("2020-02", 39_932)),
        snapshot_previous_deficits=(("2020-01", 45_338), ("2020-02", None)),
        next_release_date=date(2020, 5, 5),
        pdf_pages=63,
        pdf_dimensions=(
            (612.0, 792.0, 35),
            (874.29, 1131.43, 1),
            (886.96, 1147.83, 2),
            (1037.29, 1342.37, 2),
            (1055.17, 1365.52, 2),
            (1092.86, 1414.29, 2),
            (1112.73, 1440.0, 2),
            (1133.33, 1466.67, 4),
            (1154.72, 1494.34, 3),
            (1176.92, 1523.08, 1),
            (1200.0, 1552.94, 2),
            (1224.0, 1584.0, 4),
            (1248.98, 1616.33, 1),
            (1302.13, 1685.11, 1),
            (1330.43, 1721.74, 1),
        ),
        pdf_author="kebed001",
        pdf_creation_date="D:20200401132145-04'00'",
    ),
    date(2020, 5, 5): _ReleaseSpec(
        release_date=date(2020, 5, 5),
        reference_month=date(2020, 3, 1),
        census_release_number="CB 20-66",
        bea_release_number="BEA 20-21",
        timezone_abbreviation="EDT",
        headline_deficit_billion="44.4",
        headline_direction="up",
        headline_delta_billion="4.6",
        headline_prior_deficit_billion="39.8",
        current_row=(-44_415, -65_599, 21_184, 187_745, 128_110, 59_636, 232_160, 193_709, 38_451),
        prior_row_current_release=(
            -39_810,
            -61_045,
            21_235,
            207_747,
            137_326,
            70_421,
            247_557,
            198_370,
            49_186,
        ),
        prior_row_previous_release=(
            -39_932,
            -61_212,
            21_280,
            207_543,
            137_203,
            70_341,
            247_476,
            198_415,
            49_061,
        ),
        prior_row_label="February (R)",
        previous_release_label="February data as published last month:",
        snapshot_deficits=(
            ("2020-01", 45_482),
            ("2020-02", 39_810),
            ("2020-03", 44_415),
        ),
        snapshot_previous_deficits=(
            ("2020-01", 45_482),
            ("2020-02", 39_932),
            ("2020-03", None),
        ),
        next_release_date=date(2020, 6, 4),
        pdf_pages=62,
        pdf_dimensions=(
            (612.0, 792.0, 48),
            (941.54, 1218.46, 1),
            (987.1, 1277.42, 1),
            (1003.28, 1298.36, 1),
            (1112.73, 1440.0, 1),
            (1133.33, 1466.67, 5),
            (1176.92, 1523.08, 5),
        ),
        pdf_author="Joseph E Kafchinski",
        pdf_creation_date="D:20200504162713-04'00'",
        covid_publication_statement=True,
    ),
}


class CensusBEAFT900ArchiveAdapter:
    """Retrieve one fixed 2020 Census/BEA FT-900 PDF and XLS ZIP pair."""

    availability_rule = (
        "Each selected joint Census/BEA FT-900 PDF states an exact 8:30 a.m. EST/EDT "
        "release date and time. FinReplay validates that label against America/New_York and "
        "makes the paired PDF/XLS ZIP semantic snapshot eligible at that instant. Current "
        "archive bytes and HTTP headers are present-retrieval evidence and are not backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="census.bea.ft900.archived_trade_balance",
        title="Census/BEA archived U.S. International Trade in Goods and Services releases",
        publisher="U.S. Census Bureau and U.S. Bureau of Economic Analysis",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.census.gov/foreign-trade/Press-Release/ft900_index.html"
        ),
        allowed_hosts=("www.census.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 PDF/XLS ZIP pairs "
            "sequentially; do not crawl or enumerate the historical archive."
        ),
        pagination_policy=(
            "Each selection is one complete 62- or 63-page PDF plus one complete 31-member "
            "XLS ZIP; Exhibit 1 is parsed without paginated requests."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Every statistical month remains tied to its dated FT-900 snapshot. The April "
            "release revises January's deficit from $45,338 million to $45,482 million; the "
            "May release revises February from $39,932 million to $39,810 million. Earlier "
            "release facts are never overwritten."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Full Census/BEA PDFs and XLS ZIPs remain in local content-addressed storage. The "
            "repository retains only minimal reported facts, URLs, hashes, attribution, and "
            "release-snapshot semantics; no redistribution right is inferred."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified Census/BEA FT-900 calendar")
        self.http = http
        self.release_date = release_date
        self.spec = _VERIFIED_RELEASES[release_date]
        root = "https://www.census.gov/foreign-trade/Press-Release/ft900"
        self.pdf_endpoint = f"{root}/ft900_{self.spec.suffix}.pdf"
        self.xls_zip_endpoint = f"{root}/ft900xls_{self.spec.suffix}.zip"

    def fetch(self) -> AdapterBatch:
        pdf_response, pdf_content, pdf_retrieved_at = self.http.get(
            self.pdf_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        zip_response, zip_content, zip_retrieved_at = self.http.get(
            self.xls_zip_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(pdf_response.request_url, kind="pdf")
        self._validate_response_url(zip_response.request_url, kind="zip")
        pdf_content_type = pdf_response.headers.get("Content-Type", "").split(";", 1)[0]
        if pdf_content_type != "application/pdf":
            raise SourceSchemaError(
                f"unexpected Census/BEA FT-900 PDF content type: {pdf_content_type!r}"
            )
        zip_content_type = zip_response.headers.get("Content-Type", "").split(";", 1)[0]
        if zip_content_type not in {"application/zip", "application/octet-stream"}:
            raise SourceSchemaError(
                f"unexpected Census/BEA FT-900 ZIP content type: {zip_content_type!r}"
            )
        self._parse_pdf(pdf_content)
        workbook_facts, exhibit1_content, archive_member_sizes = self._parse_xls_zip(zip_content)
        self._crosscheck_workbook(workbook_facts)

        release_local = datetime.combine(self.release_date, time(8, 30), tzinfo=_NEW_YORK)
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError("Census/BEA FT-900 timezone does not match New York calendar")
        release_at = release_local.astimezone(UTC)
        retrieved_at = max(pdf_retrieved_at, zip_retrieved_at)
        if retrieved_at < release_at:
            raise SourceSchemaError("selected Census/BEA FT-900 release is not yet knowable")

        pdf_digest = source_response_sha256(pdf_content)
        zip_digest = source_response_sha256(zip_content)
        exhibit1_digest = source_response_sha256(exhibit1_content)
        source_version = (
            f"CENSUS-BEA-FT900:{self.spec.reference_key}:"
            f"{self.spec.census_release_number.replace(' ', '')}:"
            f"pdf:{pdf_digest[:20]}:xlszip:{zip_digest[:20]}"
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
        snapshots = dict(self.spec.snapshot_deficits)
        previous_snapshots = dict(self.spec.snapshot_previous_deficits)
        revisions = {
            month: None if prior is None else snapshots[month] - prior
            for month, prior in previous_snapshots.items()
        }
        current = dict(zip(_ROW_COLUMNS, self.spec.current_row, strict=True))
        prior = dict(zip(_ROW_COLUMNS, self.spec.prior_row_current_release, strict=True))
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.spec.reference_month:%Y%m}:"
                "goods_services_deficit_level"
            ),
            entity_id="census_bea_ft900:us_goods_services_deficit",
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
                "reference_month": self.spec.reference_key,
                "census_release_number": self.spec.census_release_number,
                "bea_release_number": self.spec.bea_release_number,
                "release_series": "Monthly U.S. International Trade in Goods and Services",
                "metric": "goods_services_deficit_level_million_dollars",
                "value_million_dollars": self.spec.current_deficit_million,
                "signed_balance_million_dollars": current["balance_total"],
                "reported_headline_deficit_billion_dollars": (self.spec.headline_deficit_billion),
                "reported_headline_direction": self.spec.headline_direction,
                "reported_headline_delta_billion_dollars": self.spec.headline_delta_billion,
                "prior_month": (
                    self.spec.reference_month.replace(day=1) - timedelta(days=1)
                ).strftime("%Y-%m"),
                "prior_month_revised_deficit_million_dollars": (self.spec.prior_deficit_million),
                "prior_month_previous_release_deficit_million_dollars": (
                    self.spec.previous_prior_deficit_million
                ),
                "prior_month_revision_delta_million_dollars": (
                    self.spec.prior_deficit_million - self.spec.previous_prior_deficit_million
                ),
                "goods_balance_million_dollars": current["balance_goods"],
                "services_balance_million_dollars": current["balance_services"],
                "exports_total_million_dollars": current["exports_total"],
                "exports_goods_million_dollars": current["exports_goods"],
                "exports_services_million_dollars": current["exports_services"],
                "imports_total_million_dollars": current["imports_total"],
                "imports_goods_million_dollars": current["imports_goods"],
                "imports_services_million_dollars": current["imports_services"],
                "prior_month_revised_row_million_dollars": prior,
                "release_snapshot_deficit_million_dollars": snapshots,
                "release_snapshot_previous_deficit_million_dollars": previous_snapshots,
                "release_snapshot_revision_delta_million_dollars": revisions,
                "release_time_local": "08:30:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "official_release_at": release_at.isoformat(),
                "next_release_date": self.spec.next_release_date.isoformat(),
                "next_release_time_label": "8:30 a.m. Eastern Time",
                "seasonally_adjusted": True,
                "adjusted_for_price_changes": False,
                "headline_statistical_significance_applicable_or_measurable": False,
                "goods_data_complete_enumeration_of_cbp_documents": True,
                "goods_data_subject_to_sampling_error": False,
                "nonsampling_errors_possible": True,
                "monthly_and_annual_revisions_documented": True,
                "covid_publication_standard_statement_present": (
                    self.spec.covid_publication_statement
                ),
                "pdf_xls_crosscheck_verified": True,
                "pdf_table_snapshot_verified": True,
                "xls_exhibit1_snapshot_verified": True,
                "current_archive_byte_identity_at_release_claimed": False,
                "release_pdf_url": pdf_response.request_url,
                "release_pdf_sha256": pdf_digest,
                "release_pdf_pages": self.spec.pdf_pages,
                "release_pdf_dimension_counts_points": [
                    list(item) for item in self.spec.pdf_dimensions
                ],
                "release_pdf_page_rotations": [0] * self.spec.pdf_pages,
                "release_pdf_metadata_creation_date": self.spec.pdf_creation_date,
                "release_pdf_metadata_modification_date": self.spec.pdf_creation_date,
                "release_xls_zip_url": zip_response.request_url,
                "release_xls_zip_sha256": zip_digest,
                "release_xls_zip_member_count": len(_EXHIBIT_MEMBER_NAMES),
                "release_xls_zip_member_names": list(_EXHIBIT_MEMBER_NAMES),
                "release_xls_zip_member_sizes": archive_member_sizes,
                "release_xls_exhibit1_sha256": exhibit1_digest,
                "release_xls_exhibit1_rows": 55,
                "release_xls_exhibit1_columns": 10,
                "availability_method": "exact_time_in_pdf_values_crosschecked_to_xls_zip",
                "unit": "Million U.S. Dollars of Seasonally Adjusted Deficit",
                "snapshot_semantics": (
                    "reported goods-and-services deficit fact in this archived joint release"
                ),
            },
        )
        warnings = (
            "The exact 8:30 a.m. EST/EDT release time is stated in each PDF and validated "
            "against America/New_York; current HTTP dates are not historical timing evidence.",
            "All PDF pages, the exact 31-member ZIP inventory, and Exhibit 1 dimensions, labels, "
            "million-dollar rows, prior-release row, and arithmetic are validated before output.",
            "The headline is rounded to one decimal billion dollars; the adapter retains exact "
            "Exhibit 1 million-dollar values and never substitutes the rounded display value.",
            "The headline says statistical significance is not applicable or measurable. Its "
            "revision chain is not a FinReplay probability or confidence interval.",
            "Figures are seasonally adjusted but not adjusted for price changes.",
            "Goods are a complete enumeration of collected CBP documents and have no sampling "
            "error, but nonsampling errors and service-estimation limitations still apply.",
            "Monthly and annual revisions remain separate release snapshots and never overwrite "
            "earlier decision-time facts.",
            "The March report's COVID-19 publication-standard statement is not a causal, "
            "complete-response, unaffected-measurement, forecast, or impact claim.",
            "Full archived PDFs and XLS ZIPs remain local download evidence.",
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
                request_url=_HTTP_URL_ADAPTER.validate_python(zip_response.request_url),
                retrieved_at=zip_retrieved_at,
                status_code=zip_response.status_code,
                content_type=zip_content_type,
                response_sha256=zip_digest,
                response_bytes=len(zip_content),
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
                    sha256=zip_digest,
                    content_type=zip_content_type,
                    content=zip_content,
                ),
            ),
        )

    def _parse_pdf(self, content: bytes) -> None:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("Census/BEA FT-900 release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != self.spec.pdf_pages:
                raise SourceSchemaError("Census/BEA FT-900 PDF page count does not match")
            pages: list[str] = []
            dimensions: Counter[tuple[float, float]] = Counter()
            rotations: list[int] = []
            for page in reader.pages:
                dimensions[
                    (
                        round(float(page.mediabox.width), 2),
                        round(float(page.mediabox.height), 2),
                    )
                ] += 1
                rotations.append(page.rotation)
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError("Census/BEA FT-900 PDF has a blank text layer")
                pages.append(extracted)
            dimension_counts = tuple(
                sorted((width, height, count) for (width, height), count in dimensions.items())
            )
            if dimension_counts != self.spec.pdf_dimensions:
                raise SourceSchemaError("Census/BEA FT-900 PDF dimensions do not match")
            if rotations != [0] * self.spec.pdf_pages:
                raise SourceSchemaError("Census/BEA FT-900 PDF page rotations do not match")
            metadata = reader.metadata
            if metadata is None:
                raise SourceSchemaError("Census/BEA FT-900 PDF metadata is missing")
            expected_metadata = {
                "/Author": self.spec.pdf_author,
                "/CreationDate": self.spec.pdf_creation_date,
                "/Creator": "Adobe Acrobat Pro 2017 17.11.30158",
                "/ModDate": self.spec.pdf_creation_date,
                "/Producer": "Adobe Acrobat Pro 2017 17.11.30158",
                "/Title": "",
            }
            if any(metadata.get(key) != value for key, value in expected_metadata.items()):
                raise SourceSchemaError("Census/BEA FT-900 PDF metadata does not match")
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("Census/BEA FT-900 PDF could not be parsed") from error

        first_page = _compact_for_match(pages[0])
        all_text = _compact_for_match(" ".join(pages))
        time_marker = (
            f"FOR RELEASE AT 8:30 AM {self.spec.timezone_abbreviation}, "
            f"{self.release_date:%A, %B} {self.release_date.day}, {self.release_date.year}"
        )
        if first_page.count(_compact_for_match(time_marker)) != 1:
            raise SourceSchemaError("Census/BEA FT-900 release-time identity does not match")
        if any(_compact_for_match(marker) not in all_text for marker in self._pdf_markers()):
            raise SourceSchemaError(
                "Census/BEA FT-900 PDF headline, revision, or methodology does not match"
            )
        covid_marker = _compact_for_match(
            "determined estimates in this release meet publication standards"
        )
        if (covid_marker in first_page) != self.spec.covid_publication_statement:
            raise SourceSchemaError(
                "Census/BEA FT-900 COVID-19 publication statement does not match"
            )

    def _parse_xls_zip(
        self,
        content: bytes,
    ) -> tuple[dict[str, object], bytes, dict[str, int]]:
        if not content.startswith(b"PK\x03\x04"):
            raise SourceSchemaError("Census/BEA FT-900 exhibit archive is not a ZIP document")
        try:
            with ZipFile(BytesIO(content)) as archive:
                infos = archive.infolist()
                names = tuple(info.filename for info in infos)
                if names != _EXHIBIT_MEMBER_NAMES or len(set(names)) != len(names):
                    raise SourceSchemaError("Census/BEA FT-900 ZIP member inventory does not match")
                if any(info.is_dir() or info.flag_bits & 1 for info in infos):
                    raise SourceSchemaError(
                        "Census/BEA FT-900 ZIP has a directory or encrypted member"
                    )
                if any(info.file_size > 2_000_000 for info in infos):
                    raise SourceSchemaError("Census/BEA FT-900 ZIP member is too large")
                if sum(info.file_size for info in infos) > 10_000_000:
                    raise SourceSchemaError("Census/BEA FT-900 ZIP expands beyond the limit")
                if archive.testzip() is not None:
                    raise SourceSchemaError("Census/BEA FT-900 ZIP member CRC does not match")
                exhibit1 = archive.read("exh1.xls")
                sizes = {info.filename: info.file_size for info in infos}
        except SourceSchemaError:
            raise
        except (BadZipFile, KeyError, OSError, RuntimeError, ValueError) as error:
            raise SourceSchemaError("Census/BEA FT-900 ZIP could not be parsed") from error
        return self._parse_xls(exhibit1), exhibit1, sizes

    def _parse_xls(self, content: bytes) -> dict[str, object]:
        if not content.startswith(_OLE_COMPOUND_MAGIC):
            raise SourceSchemaError("Census/BEA FT-900 Exhibit 1 is not a legacy OLE XLS")
        workbook: Any | None = None
        try:
            workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
            if workbook.sheet_names() != ["1"]:
                raise SourceSchemaError("Census/BEA FT-900 Exhibit 1 sheet identity does not match")
            sheet = workbook.sheet_by_name("1")
            if (sheet.nrows, sheet.ncols) != (55, 10):
                raise SourceSchemaError("Census/BEA FT-900 Exhibit 1 dimensions do not match")
            expected_headers = {
                (1, 0): "Part A: Seasonally Adjusted (by Commodity/Service)",
                (2, 0): "Exhibit 1. U.S. International Trade in Goods and Services",
                (4, 0): "Period",
                (4, 1): "Balance",
                (4, 4): "Exports",
                (4, 7): "Imports",
                (5, 1): "Total",
                (5, 2): "Goods (1)",
                (5, 3): "Services",
                (36, 0): "2020",
                (50, 0): self.spec.previous_release_label,
            }
            if any(
                sheet.cell_value(row, col) != value
                for (row, col), value in expected_headers.items()
            ):
                raise SourceSchemaError("Census/BEA FT-900 Exhibit 1 labels do not match")
            current_row_index = 37 + self.spec.reference_month.month
            prior_row_index = 35 if self.spec.reference_month.month == 1 else current_row_index - 1
            if sheet.cell_value(current_row_index, 0) != self.spec.reference_month.strftime("%B"):
                raise SourceSchemaError("Census/BEA FT-900 current-month row does not match")
            if sheet.cell_value(prior_row_index, 0) != self.spec.prior_row_label:
                raise SourceSchemaError("Census/BEA FT-900 prior-month row does not match")
            facts: dict[str, object] = {
                "current_row": _numeric_row(sheet, current_row_index),
                "prior_row_current_release": _numeric_row(sheet, prior_row_index),
                "prior_row_previous_release": _numeric_row(sheet, 51),
                "snapshot_deficits": tuple(
                    (
                        f"2020-{month:02d}",
                        -_whole_number(sheet.cell_value(37 + month, 1)),
                    )
                    for month in range(1, self.spec.reference_month.month + 1)
                ),
            }
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
            raise SourceSchemaError("Census/BEA FT-900 legacy XLS could not be parsed") from error
        finally:
            if workbook is not None:
                workbook.release_resources()

    def _crosscheck_workbook(self, facts: dict[str, object]) -> None:
        expected: dict[str, object] = {
            "current_row": self.spec.current_row,
            "prior_row_current_release": self.spec.prior_row_current_release,
            "prior_row_previous_release": self.spec.prior_row_previous_release,
            "snapshot_deficits": self.spec.snapshot_deficits,
        }
        if facts != expected:
            raise SourceSchemaError("Census/BEA FT-900 PDF and XLS facts do not cross-check")
        if abs(self.spec.current_row[4] + self.spec.current_row[5] - self.spec.current_row[3]) > 1:
            raise SourceSchemaError("Census/BEA FT-900 export components do not sum")
        if abs(self.spec.current_row[7] + self.spec.current_row[8] - self.spec.current_row[6]) > 1:
            raise SourceSchemaError("Census/BEA FT-900 import components do not sum")
        if abs(self.spec.current_row[3] - self.spec.current_row[6] - self.spec.current_row[0]) > 1:
            raise SourceSchemaError("Census/BEA FT-900 total balance arithmetic does not match")
        if abs(self.spec.current_row[1] + self.spec.current_row[2] - self.spec.current_row[0]) > 1:
            raise SourceSchemaError("Census/BEA FT-900 balance components do not sum")
        rounded_current = (Decimal(self.spec.current_deficit_million) / Decimal(1000)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        rounded_prior = (Decimal(self.spec.prior_deficit_million) / Decimal(1000)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        if rounded_current != Decimal(self.spec.headline_deficit_billion):
            raise SourceSchemaError("Census/BEA FT-900 current headline does not round from XLS")
        if rounded_prior != Decimal(self.spec.headline_prior_deficit_billion):
            raise SourceSchemaError("Census/BEA FT-900 prior headline does not round from XLS")
        observed_direction = (
            "up" if self.spec.current_deficit_million > self.spec.prior_deficit_million else "down"
        )
        if observed_direction != self.spec.headline_direction:
            raise SourceSchemaError("Census/BEA FT-900 headline direction does not match XLS")

    def _pdf_markers(self) -> tuple[str, ...]:
        reference = self.spec.reference_month.strftime("%B %Y").upper()
        prior = (self.spec.reference_month.replace(day=1) - timedelta(days=1)).strftime("%B")
        return (
            f"MONTHLY U.S. INTERNATIONAL TRADE IN GOODS AND SERVICES, {reference}",
            f"Release Number: {self.spec.census_release_number}, {self.spec.bea_release_number}",
            f"the goods and services deficit was ${self.spec.headline_deficit_billion} billion "
            f"in {self.spec.reference_month:%B}, {self.spec.headline_direction} "
            f"${self.spec.headline_delta_billion} billion from "
            f"${self.spec.headline_prior_deficit_billion} billion in {prior}, revised",
            "Statistical significance is not applicable or not measurable",
            "Data adjusted for seasonality but not price changes",
            f"Revisions to {prior} exports",
            f"Revisions to {prior} imports",
            self.spec.previous_release_label,
            "Monthly Revisions",
            "Annual Revisions",
            "complete enumeration of documents collected by CBP",
            "not subject to sampling errors",
        )

    def _validate_response_url(self, response_url: str, *, kind: str) -> None:
        parsed = urlparse(response_url)
        filename = (
            f"ft900_{self.spec.suffix}.pdf" if kind == "pdf" else f"ft900xls_{self.spec.suffix}.zip"
        )
        expected_path = f"/foreign-trade/Press-Release/ft900/{filename}"
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError(
                f"Census/BEA FT-900 {kind.upper()} response URL does not match request"
            )


def _numeric_row(sheet: Any, row: int) -> tuple[int, ...]:
    return tuple(_whole_number(sheet.cell_value(row, column)) for column in range(1, 10))


def _whole_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceSchemaError("Census/BEA FT-900 workbook cell is not numeric")
    decimal = Decimal(str(value))
    if not decimal.is_finite() or decimal != decimal.to_integral_value():
        raise SourceSchemaError("Census/BEA FT-900 workbook cell is not a whole number")
    return int(decimal)


def _normalize_text(value: str) -> str:
    return " ".join(
        value.replace("\ufb01", "fi")
        .replace("\ufb02", "fl")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .replace("\u00ad", "")
        .replace("\xa0", " ")
        .split()
    )


def _compact_for_match(value: str) -> str:
    normalized = _normalize_text(value).lower()
    return "".join(
        character for character in normalized if character.isalnum() or character in ".,-$"
    )
