"""Archived Census M3 Advance Durable Goods release adapter."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from io import BytesIO
from urllib.parse import urlparse
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
_STANDARD_PAGE = (612.0, 792.0)
_SAMPLING_MARKER = (
    "The Manufacturers' Shipments, Inventories, and Orders estimates are not based on a "
    "probability sample, so the sampling error of these estimates cannot be measured nor can "
    "the confidence intervals be computed."
)
_REVISION_MARKERS = (
    "Corrections received after the full report will be released in the next month's advance "
    "report.",
    "revisions made later than two months will be reflected in the annual benchmark publication.",
)


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    reference_month: date
    release_number: str
    release_code: str
    timezone_abbreviation: str
    value_basis_points: int
    value_million_dollars: int
    rounded_billion_dollars: str
    headline_delta_billion_dollars: str
    prior_month: date
    prior_change_basis_points: int
    prior_value_million_dollars: int
    older_change_basis_points: int
    older_value_million_dollars: int
    excluding_transportation_change_basis_points: int
    excluding_defense_change_basis_points: int
    transportation_change_basis_points: int
    transportation_level_rounded_million_dollars: int
    shipments_value_million_dollars: int
    shipments_change_basis_points: int
    unfilled_orders_value_million_dollars: int
    unfilled_orders_change_basis_points: int
    inventories_value_million_dollars: int
    inventories_change_basis_points: int
    next_reference_month: date
    next_advance_release_date: date
    next_advance_timezone_abbreviation: str
    full_report_release_date: date
    full_report_timezone_abbreviation: str
    pdf_creation_date: str
    pdf_modification_date: str
    page_dimensions: tuple[tuple[float, float], ...]
    snapshot_changes: tuple[tuple[str, int], ...]
    snapshot_previous_changes: tuple[tuple[str, int | None], ...]
    snapshot_levels: tuple[tuple[str, int], ...]
    snapshot_previous_levels: tuple[tuple[str, int | None], ...]
    covid_publication_statement: bool = False

    @property
    def reference_key(self) -> str:
        return self.reference_month.strftime("%Y-%m")

    @property
    def reference_label(self) -> str:
        return self.reference_month.strftime("%B %Y")

    @property
    def endpoint_path(self) -> str:
        return (
            "/manufacturing/m3/historical_data/pressreleases/adv/2020/"
            f"{self.reference_month:%b%y}adv.pdf".lower()
        )


_VERIFIED_RELEASES = {
    date(2020, 2, 27): _ReleaseSpec(
        release_date=date(2020, 2, 27),
        reference_month=date(2020, 1, 1),
        release_number="CB 20-31",
        release_code="M3-1 (20)-01",
        timezone_abbreviation="EST",
        value_basis_points=-20,
        value_million_dollars=246_199,
        rounded_billion_dollars="246.2",
        headline_delta_billion_dollars="0.4",
        prior_month=date(2019, 12, 1),
        prior_change_basis_points=290,
        prior_value_million_dollars=246_634,
        older_change_basis_points=-310,
        older_value_million_dollars=239_718,
        excluding_transportation_change_basis_points=90,
        excluding_defense_change_basis_points=360,
        transportation_change_basis_points=-220,
        transportation_level_rounded_million_dollars=82_000,
        shipments_value_million_dollars=250_098,
        shipments_change_basis_points=-20,
        unfilled_orders_value_million_dollars=1_157_012,
        unfilled_orders_change_basis_points=0,
        inventories_value_million_dollars=435_379,
        inventories_change_basis_points=0,
        next_reference_month=date(2020, 2, 1),
        next_advance_release_date=date(2020, 3, 25),
        next_advance_timezone_abbreviation="EST",
        full_report_release_date=date(2020, 3, 5),
        full_report_timezone_abbreviation="EST",
        pdf_creation_date="D:20200226105721-05'00'",
        pdf_modification_date="D:20200324112228-04'00'",
        page_dimensions=(
            _STANDARD_PAGE,
            _STANDARD_PAGE,
            _STANDARD_PAGE,
            _STANDARD_PAGE,
            _STANDARD_PAGE,
            (1492.68, 1931.71),
            (1423.26, 1841.86),
        ),
        snapshot_changes=(("2020-01", -20),),
        snapshot_previous_changes=(("2020-01", None),),
        snapshot_levels=(("2020-01", 246_199),),
        snapshot_previous_levels=(("2020-01", None),),
    ),
    date(2020, 3, 25): _ReleaseSpec(
        release_date=date(2020, 3, 25),
        reference_month=date(2020, 2, 1),
        release_number="CB 20-47",
        release_code="M3-1 (20)-02",
        timezone_abbreviation="EDT",
        value_basis_points=120,
        value_million_dollars=249_409,
        rounded_billion_dollars="249.4",
        headline_delta_billion_dollars="2.9",
        prior_month=date(2020, 1, 1),
        prior_change_basis_points=10,
        prior_value_million_dollars=246_541,
        older_change_basis_points=280,
        older_value_million_dollars=246_375,
        excluding_transportation_change_basis_points=-60,
        excluding_defense_change_basis_points=10,
        transportation_change_basis_points=460,
        transportation_level_rounded_million_dollars=87_000,
        shipments_value_million_dollars=252_329,
        shipments_change_basis_points=80,
        unfilled_orders_value_million_dollars=1_158_641,
        unfilled_orders_change_basis_points=10,
        inventories_value_million_dollars=434_881,
        inventories_change_basis_points=0,
        next_reference_month=date(2020, 3, 1),
        next_advance_release_date=date(2020, 4, 24),
        next_advance_timezone_abbreviation="EDT",
        full_report_release_date=date(2020, 4, 2),
        full_report_timezone_abbreviation="EDT",
        pdf_creation_date="D:20200324110955-04'00'",
        pdf_modification_date="D:20200423094958-04'00'",
        page_dimensions=(_STANDARD_PAGE,) * 7,
        snapshot_changes=(("2020-01", 10), ("2020-02", 120)),
        snapshot_previous_changes=(("2020-01", -20), ("2020-02", None)),
        snapshot_levels=(("2020-01", 246_541), ("2020-02", 249_409)),
        snapshot_previous_levels=(("2020-01", 246_199), ("2020-02", None)),
    ),
    date(2020, 4, 24): _ReleaseSpec(
        release_date=date(2020, 4, 24),
        reference_month=date(2020, 3, 1),
        release_number="CB 20-54",
        release_code="M3-1 (20)-03",
        timezone_abbreviation="EDT",
        value_basis_points=-1_440,
        value_million_dollars=213_184,
        rounded_billion_dollars="213.2",
        headline_delta_billion_dollars="36.0",
        prior_month=date(2020, 2, 1),
        prior_change_basis_points=110,
        prior_value_million_dollars=249_167,
        older_change_basis_points=10,
        older_value_million_dollars=246_558,
        excluding_transportation_change_basis_points=-20,
        excluding_defense_change_basis_points=-1_580,
        transportation_change_basis_points=-4_100,
        transportation_level_rounded_million_dollars=51_200,
        shipments_value_million_dollars=240_715,
        shipments_change_basis_points=-450,
        unfilled_orders_value_million_dollars=1_135_165,
        unfilled_orders_change_basis_points=-200,
        inventories_value_million_dollars=437_420,
        inventories_change_basis_points=60,
        next_reference_month=date(2020, 4, 1),
        next_advance_release_date=date(2020, 5, 28),
        next_advance_timezone_abbreviation="EDT",
        full_report_release_date=date(2020, 5, 4),
        full_report_timezone_abbreviation="EDT",
        pdf_creation_date="D:20200423152159-04'00'",
        pdf_modification_date="D:20200527104843-04'00'",
        page_dimensions=(_STANDARD_PAGE,) * 7,
        snapshot_changes=(
            ("2020-01", 10),
            ("2020-02", 110),
            ("2020-03", -1_440),
        ),
        snapshot_previous_changes=(
            ("2020-01", 10),
            ("2020-02", 120),
            ("2020-03", None),
        ),
        snapshot_levels=(
            ("2020-01", 246_558),
            ("2020-02", 249_167),
            ("2020-03", 213_184),
        ),
        snapshot_previous_levels=(
            ("2020-01", 246_541),
            ("2020-02", 249_409),
            ("2020-03", None),
        ),
        covid_publication_statement=True,
    ),
}


class CensusDurableGoodsArchiveAdapter:
    """Retrieve one explicitly approved Census M3 Advance Durable Goods PDF."""

    availability_rule = (
        "Each selected official Census M3 Advance Durable Goods report states an exact 8:30 "
        "a.m. EST/EDT release date and time. FinReplay validates that label against "
        "America/New_York and assigns the report's semantic release facts to that stated time. "
        "The current archived PDF hash and metadata remain present-retrieval evidence; because "
        "each PDF has post-release modification metadata, exact current bytes are not claimed "
        "to be identical to the bytes served at the historical release instant."
    )
    metadata = AdapterMetadata(
        adapter_id="census.m3.archived_advance_durable_goods",
        title="Census archived Advance Durable Goods releases",
        publisher="U.S. Census Bureau",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.census.gov/manufacturing/m3/adv/historical_data/index.html"
        ),
        allowed_hosts=("www.census.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 PDFs sequentially; do not crawl "
            "or enumerate the historical release archive."
        ),
        pagination_policy="Each selected release is one complete seven-page PDF.",
        availability_rule=availability_rule,
        revision_behavior=(
            "Every reference month remains tied to its report snapshot. January's initial "
            "-0.2 percent becomes +0.1 percent in the March report; February's initial +1.2 "
            "percent becomes +1.1 percent in the April report. Later reports and benchmark "
            "revisions never overwrite the earlier facts."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Full Census M3 PDFs remain in local content-addressed storage. The repository "
            "retains only minimal reported facts, URLs, hashes, attribution, and release-"
            "snapshot semantics; no redistribution right is inferred."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified Census M3 durable-goods calendar")
        self.http = http
        self.release_date = release_date
        self.spec = _VERIFIED_RELEASES[release_date]
        self.endpoint = f"https://www.census.gov{self.spec.endpoint_path}"

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/pdf":
            raise SourceSchemaError(
                f"unexpected Census M3 durable-goods content type: {content_type!r}"
            )
        self._parse_pdf(content)
        release_local = datetime.combine(
            self.release_date,
            time(8, 30),
            tzinfo=_NEW_YORK,
        )
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError(
                "Census M3 durable-goods timezone does not match New York calendar"
            )
        release_at = release_local.astimezone(UTC)
        if retrieved_at < release_at:
            raise SourceSchemaError("selected Census M3 durable-goods release is not yet knowable")
        digest = source_response_sha256(content)
        source_version = (
            f"CENSUS-M3-DURABLE:{self.spec.reference_key}:"
            f"{self.spec.release_number.replace(' ', '')}:pdf:{digest[:24]}"
        )
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            vintage_as_of=release_at,
            redistribution_note=self.metadata.redistribution_note,
        )
        changes = dict(self.spec.snapshot_changes)
        previous_changes = dict(self.spec.snapshot_previous_changes)
        change_revisions = {
            month: None if old is None else changes[month] - old
            for month, old in previous_changes.items()
        }
        levels = dict(self.spec.snapshot_levels)
        previous_levels = dict(self.spec.snapshot_previous_levels)
        level_revisions = {
            month: None if old is None else levels[month] - old
            for month, old in previous_levels.items()
        }
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.spec.reference_month:%Y%m}:"
                "total_durable_goods_new_orders_monthly_change"
            ),
            entity_id="census_m3:total_durable_goods_new_orders",
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
                "release_number": self.spec.release_number,
                "release_code": self.spec.release_code,
                "release_series": "Monthly Advance Report on Durable Goods",
                "metric": "total_durable_goods_new_orders_monthly_change_basis_points",
                "value_basis_points": self.spec.value_basis_points,
                "value_percent": _format_basis_points(self.spec.value_basis_points),
                "value_million_dollars": self.spec.value_million_dollars,
                "reported_rounded_value_billion_dollars": (self.spec.rounded_billion_dollars),
                "reported_headline_delta_billion_dollars": (
                    self.spec.headline_delta_billion_dollars
                ),
                "prior_month": self.spec.prior_month.strftime("%Y-%m"),
                "prior_month_revised_change_basis_points": (self.spec.prior_change_basis_points),
                "prior_month_revised_value_million_dollars": (
                    self.spec.prior_value_million_dollars
                ),
                "older_month_change_basis_points": self.spec.older_change_basis_points,
                "older_month_value_million_dollars": self.spec.older_value_million_dollars,
                "excluding_transportation_change_basis_points": (
                    self.spec.excluding_transportation_change_basis_points
                ),
                "excluding_defense_change_basis_points": (
                    self.spec.excluding_defense_change_basis_points
                ),
                "transportation_equipment_change_basis_points": (
                    self.spec.transportation_change_basis_points
                ),
                "transportation_equipment_rounded_level_million_dollars": (
                    self.spec.transportation_level_rounded_million_dollars
                ),
                "shipments_value_million_dollars": self.spec.shipments_value_million_dollars,
                "shipments_change_basis_points": self.spec.shipments_change_basis_points,
                "unfilled_orders_value_million_dollars": (
                    self.spec.unfilled_orders_value_million_dollars
                ),
                "unfilled_orders_change_basis_points": (
                    self.spec.unfilled_orders_change_basis_points
                ),
                "inventories_value_million_dollars": (self.spec.inventories_value_million_dollars),
                "inventories_change_basis_points": self.spec.inventories_change_basis_points,
                "release_snapshot_change_basis_points": changes,
                "release_snapshot_previous_change_basis_points": previous_changes,
                "release_snapshot_revision_delta_basis_points": change_revisions,
                "release_snapshot_new_orders_million_dollars": levels,
                "release_snapshot_previous_new_orders_million_dollars": previous_levels,
                "release_snapshot_level_revision_delta_million_dollars": level_revisions,
                "release_time_local": "08:30:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "official_release_at": release_at.isoformat(),
                "next_advance_release_date": self.spec.next_advance_release_date.isoformat(),
                "next_advance_release_time_label": (
                    f"8:30 a.m. {self.spec.next_advance_timezone_abbreviation}"
                ),
                "full_report_release_date": self.spec.full_report_release_date.isoformat(),
                "full_report_release_time_label": (
                    f"10:00 a.m. {self.spec.full_report_timezone_abbreviation}"
                ),
                "seasonally_adjusted": True,
                "adjusted_for_price_changes": False,
                "text_describes_not_adjusted_for_inflation": True,
                "new_and_unfilled_orders_exclude_semiconductor_manufacturing": True,
                "probability_sample": False,
                "sampling_error_measurable": False,
                "confidence_intervals_computable": False,
                "statistical_significance_measurable": False,
                "annual_benchmark_notice_present": True,
                "covid_publication_standard_statement_present": (
                    self.spec.covid_publication_statement
                ),
                "pdf_table_snapshot_verified": True,
                "current_pdf_byte_identity_at_release_claimed": False,
                "report_pdf_url": response.request_url,
                "report_pdf_sha256": digest,
                "report_pdf_pages": 7,
                "report_pdf_page_dimensions_points": [
                    list(item) for item in self.spec.page_dimensions
                ],
                "report_pdf_page_rotations": [0] * 7,
                "report_pdf_metadata_creation_date": self.spec.pdf_creation_date,
                "report_pdf_metadata_modification_date": (self.spec.pdf_modification_date),
                "report_pdf_metadata_modified_after_release": True,
                "availability_method": (
                    "exact_time_in_report_for_semantic_facts_current_pdf_bytes_retrieval_only"
                ),
                "unit": "Basis Points of Month-over-Month New Orders Change",
                "snapshot_semantics": (
                    "reported total durable-goods new-orders fact in this archived release"
                ),
            },
        )
        warnings = (
            "The exact 8:30 a.m. EST/EDT report time is validated against America/New_York; "
            "current HTTP dates are not backdated.",
            "Each current archived PDF has modification metadata after its stated release. The "
            "raw hash proves current official evidence, not byte identity at release.",
            "Headline values and changes are cross-checked against Table 1 exact million-dollar "
            "values and revision rows within the complete seven-page PDF.",
            "M3 is not a probability sample; sampling error, confidence intervals, and headline "
            "statistical significance are not measurable.",
            "Figures are seasonally adjusted but not adjusted for inflation or price changes.",
            "Later full reports, advance reports, and benchmarks may revise values; every report "
            "snapshot stays separate.",
            "The March report's COVID-19 publication-standard statement is source text, not a "
            "causal, complete-response, unaffected-measurement, or forecast claim.",
            "Full archived PDFs remain local download evidence.",
        )
        receipt = FetchReceipt(
            adapter_id=self.metadata.adapter_id,
            request_url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
            retrieved_at=retrieved_at,
            status_code=response.status_code,
            content_type=content_type,
            response_sha256=digest,
            response_bytes=len(content),
            record_count=1,
            source_version=source_version,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            historical_replay_eligible=True,
            warnings=warnings,
        )
        artifact = RawArtifact(sha256=digest, content_type=content_type, content=content)
        return AdapterBatch(records=(record,), receipts=(receipt,), artifacts=(artifact,))

    def _parse_pdf(self, content: bytes) -> None:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("Census M3 durable-goods release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 7:
                raise SourceSchemaError(
                    "Census M3 durable-goods release must contain exactly seven pages"
                )
            pages: list[str] = []
            dimensions: list[tuple[float, float]] = []
            rotations: list[int] = []
            for page in reader.pages:
                dimensions.append(
                    (round(float(page.mediabox.width), 2), round(float(page.mediabox.height), 2))
                )
                rotations.append(page.rotation)
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError(
                        "Census M3 durable-goods release has a blank text layer"
                    )
                pages.append(_normalize(extracted))
            if tuple(dimensions) != self.spec.page_dimensions:
                raise SourceSchemaError(
                    "Census M3 durable-goods release page dimensions do not match"
                )
            if rotations != [0] * 7:
                raise SourceSchemaError("Census M3 durable-goods page rotations do not match")
            metadata = reader.metadata
            if metadata is None:
                raise SourceSchemaError("Census M3 durable-goods PDF metadata is missing")
            metadata_checks = {
                "/Author": "Nathan R Scarlett (CENSUS/EID FED)",
                "/Company": "U.S. Department of Commerce",
                "/Creator": "Acrobat PDFMaker 17 for Word",
                "/Producer": "Adobe PDF Library 15.0",
                "/CreationDate": self.spec.pdf_creation_date,
                "/ModDate": self.spec.pdf_modification_date,
                "/Title": "",
            }
            if any(metadata.get(key) != value for key, value in metadata_checks.items()):
                raise SourceSchemaError("Census M3 durable-goods PDF metadata does not match")
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError(
                "Census M3 durable-goods release PDF could not be parsed"
            ) from error
        if "EXPLANATORY NOTES" not in pages[2]:
            raise SourceSchemaError("Census M3 durable-goods explanatory page does not match")
        if "BENCHMARK NOTICE" not in pages[3]:
            raise SourceSchemaError("Census M3 durable-goods benchmark page does not match")
        if "Table 1. Durable Goods Manufacturers' Shipments and New Orders" not in pages[5]:
            raise SourceSchemaError("Census M3 durable-goods Table 1 page does not match")
        if (
            "Table 2. Durable Goods Manufacturers' Unfilled Orders and Total Inventories"
            not in (pages[6])
        ):
            raise SourceSchemaError("Census M3 durable-goods Table 2 page does not match")
        self._validate_first_page(pages[0])
        self._validate_release_schedule(
            pages[1] if self.release_date != date(2020, 4, 24) else pages[2]
        )
        self._validate_methodology(" ".join(pages[2:5]))
        self._validate_table1(pages[5])
        self._validate_table2(pages[6])

    def _validate_first_page(self, first: str) -> None:
        time_marker = (
            f"FOR RELEASE AT 8:30 AM {self.spec.timezone_abbreviation}, "
            f"{calendar.day_name[self.release_date.weekday()].upper()}, "
            f"{self.release_date:%B %d, %Y}".upper()
        )
        direction = "increased" if self.spec.value_basis_points >= 0 else "decreased"
        excluding_transportation_direction = (
            "increased"
            if self.spec.excluding_transportation_change_basis_points >= 0
            else "decreased"
        )
        excluding_defense_direction = (
            "increased" if self.spec.excluding_defense_change_basis_points >= 0 else "decreased"
        )
        identity_markers = (
            time_marker,
            (
                "MONTHLY ADVANCE REPORT ON DURABLE GOODS MANUFACTURERS' SHIPMENTS, "
                "INVENTORIES AND ORDERS"
            ),
            self.spec.reference_label.upper(),
            f"Release Number: {self.spec.release_number} {self.spec.release_code}",
            (
                f"New orders for manufactured durable goods in "
                f"{self.spec.reference_month:%B} {direction} "
                f"${self.spec.headline_delta_billion_dollars} billion or "
                f"{abs(self.spec.value_basis_points) / 100:.1f} percent to "
                f"${self.spec.rounded_billion_dollars} billion"
            ),
            (
                f"followed a {abs(self.spec.prior_change_basis_points) / 100:.1f} percent "
                f"{self.spec.prior_month:%B} "
                f"{('increase' if self.spec.prior_change_basis_points >= 0 else 'decrease')}"
            ),
            (
                f"Excluding transportation, new orders {excluding_transportation_direction} "
                f"{abs(self.spec.excluding_transportation_change_basis_points) / 100:.1f} percent"
            ),
            (
                f"Excluding defense, new orders {excluding_defense_direction} "
                f"{abs(self.spec.excluding_defense_change_basis_points) / 100:.1f} percent"
            ),
            _SAMPLING_MARKER,
        )
        if any(marker not in first for marker in identity_markers):
            raise SourceSchemaError(
                "Census M3 durable-goods headline or release identity does not match"
            )
        if first.count(time_marker) != 1 or first.count(self.spec.release_code) != 1:
            raise SourceSchemaError("Census M3 durable-goods release identity is not unique")
        covid_marker = "determined estimates in this release meet publication standards"
        if (covid_marker in first) != self.spec.covid_publication_statement:
            raise SourceSchemaError(
                "Census M3 durable-goods COVID-19 statement does not match calendar"
            )

    def _validate_release_schedule(self, page: str) -> None:
        required = (
            (
                "Revised and more detailed estimates, plus nondurable goods data, will be "
                f"published on {self.spec.full_report_release_date:%B} "
                f"{self.spec.full_report_release_date.day}, "
                f"{self.spec.full_report_release_date:%Y}, at 10:00 a.m. "
                f"{self.spec.full_report_timezone_abbreviation}."
            ),
            (
                "The Advance Report on durable goods for "
                f"{self.spec.next_reference_month:%B} is scheduled for release on "
                f"{self.spec.next_advance_release_date:%B} "
                f"{self.spec.next_advance_release_date.day}, "
                f"{self.spec.next_advance_release_date:%Y} at 8:30 a.m. "
                f"{self.spec.next_advance_timezone_abbreviation}."
            ),
        )
        expected_month = (self.spec.reference_month.month % 12) + 1
        expected_year = self.spec.reference_month.year + (
            1 if self.spec.reference_month.month == 12 else 0
        )
        if (self.spec.next_reference_month.month, self.spec.next_reference_month.year) != (
            expected_month,
            expected_year,
        ):
            raise SourceSchemaError("Census M3 durable-goods next-release calendar mismatch")
        if any(marker not in page for marker in required):
            raise SourceSchemaError("Census M3 durable-goods release schedule does not match")

    def _validate_methodology(self, text: str) -> None:
        required = (
            "Figures in text are adjusted for seasonality, but not for inflation.",
            "Figures on new and unfilled orders exclude data for semiconductor manufacturing.",
            "The M3 panel is not based on a probability sample",
            "provided with sample surveys cannot be measured",
            *_REVISION_MARKERS,
            "BENCHMARK NOTICE",
            "Revised historical data from the Manufacturers' Shipments, Inventories, and Orders",
        )
        if any(marker not in text for marker in required):
            raise SourceSchemaError("Census M3 durable-goods methodology does not match")

    def _validate_table1(self, table: str) -> None:
        total_orders = re.compile(
            r"New Orders4\D+"
            rf"{self.spec.value_million_dollars:,}\s+"
            rf"{self.spec.prior_value_million_dollars:,}\s+"
            rf"{self.spec.older_value_million_dollars:,}\s+"
            rf"{re.escape(_format_basis_points(self.spec.value_basis_points))}\s+"
            rf"{re.escape(_format_basis_points(self.spec.prior_change_basis_points))}\s+"
            rf"{re.escape(_format_basis_points(self.spec.older_change_basis_points))}\b"
        )
        total_shipments = re.compile(
            r"Total: Shipments\D+"
            rf"{self.spec.shipments_value_million_dollars:,}\s+"
            r"[\d,]+\s+[\d,]+\s+"
            rf"{re.escape(_format_basis_points(self.spec.shipments_change_basis_points))}\b"
        )
        if not total_orders.search(table) or not total_shipments.search(table):
            raise SourceSchemaError("Census M3 durable-goods Table 1 values do not cross-check")

    def _validate_table2(self, table: str) -> None:
        unfilled = re.compile(
            r"Total: Unfilled Orders\s*4?\D+"
            rf"{self.spec.unfilled_orders_value_million_dollars:,}\s+"
            r"[\d,]+\s+[\d,]+\s+"
            rf"{re.escape(_format_basis_points(self.spec.unfilled_orders_change_basis_points))}\b"
        )
        inventories = re.compile(
            r"Total Inventories\D+"
            rf"{self.spec.inventories_value_million_dollars:,}\s+"
            r"[\d,]+\s+[\d,]+\s+"
            rf"{re.escape(_format_basis_points(self.spec.inventories_change_basis_points))}\b"
        )
        if not unfilled.search(table) or not inventories.search(table):
            raise SourceSchemaError("Census M3 durable-goods Table 2 values do not cross-check")

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != self.spec.endpoint_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("Census M3 durable-goods response URL does not match request")


def _normalize(value: str) -> str:
    return " ".join(
        value.replace("\u00a0", " ")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2212", "-")
        .split()
    )


def _format_basis_points(value: int) -> str:
    return f"{value / 100:.1f}"
