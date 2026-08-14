"""Archived FHFA House Price Index release adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from html.parser import HTMLParser
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
_SCHEDULE_URL = (
    "https://www.fhfa.gov/news/news-release/"
    "fhfa-announces-2020-release-dates-for-house-price-index"
)
_SCHEDULE_PUBLISHED_DATE = date(2019, 8, 20)
_SCHEDULE_CONSERVATIVE_KNOWLEDGE_AT = datetime(2019, 8, 22, tzinfo=UTC)
_GEOGRAPHIES = (
    "U.S.",
    "Pacific",
    "Mountain",
    "West North Central",
    "West South Central",
    "East North Central",
    "East South Central",
    "New England",
    "Middle Atlantic",
    "South Atlantic",
)
_SCHEDULE_ENTRIES = (
    ("2020-01-22", "Monthly Index", "November 2019"),
    ("2020-02-25", "Quarterly and Monthly Index", "December 2019 and 2019Q4"),
    ("2020-03-25", "Monthly Index", "January 2020"),
    ("2020-04-22", "Monthly Index", "February 2020"),
    ("2020-05-26", "Quarterly and Monthly Index", "March 2020 and 2020Q1"),
    ("2020-06-24", "Monthly Index", "April 2020"),
    ("2020-07-22", "Monthly Index", "May 2020"),
    ("2020-08-25", "Quarterly and Monthly Index", "June 2020 and 2020Q2"),
    ("2020-09-23", "Monthly Index", "July 2020"),
    ("2020-10-27", "Monthly Index", "August 2020"),
    ("2020-11-24", "Quarterly and Monthly Index", "September 2020 and 2020Q3"),
    ("2020-12-23", "Monthly Index", "October 2020"),
)
_SCHEDULE_SEMANTIC_FACTS = {
    "publisher": "Federal Housing Finance Agency",
    "published_date": _SCHEDULE_PUBLISHED_DATE.isoformat(),
    "release_time_local": "09:00:00",
    "release_timezone": "America/New_York",
    "entries": _SCHEDULE_ENTRIES,
}
_SCHEDULE_SEMANTIC_SHA256 = hashlib.sha256(
    json.dumps(
        _SCHEDULE_SEMANTIC_FACTS,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


@dataclass(frozen=True, slots=True)
class _RevisionRow:
    label: str
    current_basis_points: tuple[int, ...]
    previous_basis_points: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    reference_month: date
    endpoint_path: str
    report_kind: str
    report_pages: int
    page_rotations: tuple[int, ...]
    press_page_indexes: tuple[int, ...]
    table_page_index: int
    overview_page_index: int
    schedule_page_index: int
    pdf_title: str
    pdf_subject: str
    pdf_creation_date: str
    pdf_modification_date: str
    cover_markers: tuple[str, ...]
    headline_markers: tuple[str, ...]
    next_release_marker: str
    covid_timing_marker: str
    footer_time_label: str
    current_change_basis_points: tuple[int, ...]
    year_over_year_basis_points: tuple[int, ...]
    current_index_values: tuple[str, ...]
    revision_rows: tuple[_RevisionRow, ...]
    snapshot_change_basis_points: tuple[tuple[str, int], ...]
    snapshot_previous_basis_points: tuple[tuple[str, int | None], ...]

    @property
    def reference_label(self) -> str:
        return self.reference_month.strftime("%B %Y")

    @property
    def reference_key(self) -> str:
        return self.reference_month.strftime("%Y-%m")

    @property
    def report_url(self) -> str:
        return f"https://www.fhfa.gov{self.endpoint_path}"


_MONTHLY_ROTATIONS = (0, 0, 0, 0, 90, 90, 90, 0, 90, 0, 0, 90)
_QUARTERLY_ROTATIONS = (
    0,
    0,
    0,
    0,
    0,
    90,
    90,
    90,
    0,
    90,
    90,
    90,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    90,
    0,
    90,
)


_VERIFIED_RELEASES = {
    date(2020, 3, 25): _ReleaseSpec(
        release_date=date(2020, 3, 25),
        reference_month=date(2020, 1, 1),
        endpoint_path="/document/d/hpi/house-price-index-report-january-2020",
        report_kind="monthly",
        report_pages=12,
        page_rotations=_MONTHLY_ROTATIONS,
        press_page_indexes=(2,),
        table_page_index=8,
        overview_page_index=10,
        schedule_page_index=11,
        pdf_title="FHFA Monthly HPI",
        pdf_subject="Monthly HPI",
        pdf_creation_date="D:20200318115301-04'00'",
        pdf_modification_date="D:20200320124635-04'00'",
        cover_markers=(
            "Monthly Report",
            "Data thru January 2020",
            "Released on March 25, 2020",
        ),
        headline_markers=(
            "House Price Index Up 0.3 Percent in January; Up 5.2 Percent from Last Year",
            "U.S. house prices rose in January, up 0.3 percent from the previous month",
            "prices rose 5.2 percent from January 2019 to January 2020",
            "previously reported 0.6 percent increase for December 2019 was revised upward "
            "to 0.7 percent",
        ),
        next_release_marker=(
            "next HPI report will be released April 22, 2020 with monthly data through "
            "February 2020"
        ),
        covid_timing_marker=(
            "Transactions in January were unlikely to reflect much, if any, influence from "
            "the COVID-19 outbreak"
        ),
        footer_time_label="9AM EST",
        current_change_basis_points=(30, 50, -20, -10, 0, 30, 20, 40, 60, 70),
        year_over_year_basis_points=(520, 520, 610, 460, 440, 480, 550, 460, 410, 640),
        current_index_values=(
            "284.4",
            "329.5",
            "389.8",
            "279.9",
            "295.9",
            "235.0",
            "260.3",
            "266.4",
            "252.6",
            "292.0",
        ),
        revision_rows=(
            _RevisionRow(
                label="Nov 19 - Dec 19",
                current_basis_points=(70, 60, 130, 80, 120, 0, 90, 80, 40, 100),
                previous_basis_points=(60, 40, 100, 80, 60, -30, 100, 70, 70, 100),
            ),
        ),
        snapshot_change_basis_points=(("2020-01", 30),),
        snapshot_previous_basis_points=(("2020-01", None),),
    ),
    date(2020, 4, 22): _ReleaseSpec(
        release_date=date(2020, 4, 22),
        reference_month=date(2020, 2, 1),
        endpoint_path="/document/d/hpi/house-price-index-report-february-2020",
        report_kind="monthly",
        report_pages=12,
        page_rotations=_MONTHLY_ROTATIONS,
        press_page_indexes=(2,),
        table_page_index=8,
        overview_page_index=10,
        schedule_page_index=11,
        pdf_title="FHFA Monthly HPI",
        pdf_subject="Monthly HPI",
        pdf_creation_date="D:20200415162251-04'00'",
        pdf_modification_date="D:20200420145006-04'00'",
        cover_markers=(
            "Monthly Report",
            "Data thru February 2020",
            "Released on April 22, 2020",
        ),
        headline_markers=(
            "House Price Index Up 0.7 Percent in February; Up 5.7 Percent from Last Year",
            "U.S. house prices rose in February, up 0.7 percent from the previous month",
            "prices rose 5.7 percent from February 2019 to February 2020",
            "previously reported 0.3 percent increase for January 2020 was revised upward "
            "to 0.5 percent",
        ),
        next_release_marker=(
            "next HPI report will be released May 26, 2020 with data for the first quarter "
            "of 2020 and monthly data through March 2020"
        ),
        covid_timing_marker=(
            "Transactions still do not reflect much, if any, influence from the COVID-19 "
            "outbreak as of February"
        ),
        footer_time_label="9AM ET",
        current_change_basis_points=(70, 80, 100, 90, 30, 100, 70, 40, 120, 40),
        year_over_year_basis_points=(570, 620, 810, 560, 420, 530, 580, 460, 580, 610),
        current_index_values=(
            "287.0",
            "333.0",
            "395.9",
            "283.7",
            "296.0",
            "237.6",
            "263.9",
            "268.0",
            "256.0",
            "293.4",
        ),
        revision_rows=(
            _RevisionRow(
                label="Dec 19 - Jan 20",
                current_basis_points=(50, 80, 30, 30, -40, 50, 70, 50, 70, 70),
                previous_basis_points=(30, 50, -20, -10, 0, 30, 20, 40, 60, 70),
            ),
        ),
        snapshot_change_basis_points=(("2020-01", 50), ("2020-02", 70)),
        snapshot_previous_basis_points=(("2020-01", 30), ("2020-02", None)),
    ),
    date(2020, 5, 26): _ReleaseSpec(
        release_date=date(2020, 5, 26),
        reference_month=date(2020, 3, 1),
        endpoint_path="/document/d/hpi/fhfa-house-price-index-report-2020q1",
        report_kind="quarterly_with_monthly_tables",
        report_pages=28,
        page_rotations=_QUARTERLY_ROTATIONS,
        press_page_indexes=(2, 3),
        table_page_index=25,
        overview_page_index=26,
        schedule_page_index=27,
        pdf_title="FHFA Quarterly HPI",
        pdf_subject="Quarterly HPI",
        pdf_creation_date="D:20200526074939-04'00'",
        pdf_modification_date="D:20200615174605-04'00'",
        cover_markers=(
            "Quarterly Report",
            "2020Q1 & Mar. 2020",
            "May 26, 2020",
        ),
        headline_markers=(
            "U.S. House Prices Rise 1.7 Percent in First Quarter; Up 5.7 Percent from Last Year",
            "seasonally adjusted monthly index for March was up 0.1 percent from February",
            "House prices rose 5.7 percent from the first quarter of 2019 to the first quarter "
            "of 2020",
        ),
        next_release_marker=(
            "next monthly HPI report (including data through April 2020) will be released "
            "June 24, 2020"
        ),
        covid_timing_marker=(
            "data contained within this report is unlikely to reflect the economic impact "
            "of COVID-19"
        ),
        footer_time_label="9AM ET",
        current_change_basis_points=(10, 20, 80, -60, 30, 0, 20, 100, -20, 10),
        year_over_year_basis_points=(590, 610, 850, 450, 420, 550, 660, 680, 540, 620),
        current_index_values=(
            "287.9",
            "333.7",
            "399.9",
            "283.1",
            "297.5",
            "237.8",
            "264.4",
            "272.1",
            "255.1",
            "294.6",
        ),
        revision_rows=(
            _RevisionRow(
                label="Jan 20 - Feb 20",
                current_basis_points=(80, 70, 110, 130, 40, 90, 70, 80, 100, 80),
                previous_basis_points=(70, 80, 100, 90, 30, 100, 70, 40, 120, 40),
            ),
            _RevisionRow(
                label="Dec 19 - Jan 20",
                current_basis_points=(50, 80, 40, 30, -30, 60, 60, 60, 70, 70),
                previous_basis_points=(50, 80, 30, 30, -40, 50, 70, 50, 70, 70),
            ),
        ),
        snapshot_change_basis_points=(
            ("2020-01", 50),
            ("2020-02", 80),
            ("2020-03", 10),
        ),
        snapshot_previous_basis_points=(
            ("2020-01", 50),
            ("2020-02", 70),
            ("2020-03", None),
        ),
    ),
}


class FHFAHPIArchiveAdapter:
    """Retrieve one approved FHFA HPI report plus its preannounced schedule."""

    availability_rule = (
        "FHFA's August 20, 2019 official schedule states that 2020 HPI releases occur at "
        "9 a.m. ET and lists the selected report dates. FinReplay validates that schedule, "
        "resolves 9 a.m. through America/New_York for each date, and requires the matching "
        "dated report PDF. Current HTTP headers and retrieval times are not backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="fhfa.hpi.archived_purchase_only_monthly_change",
        title="FHFA archived House Price Index releases",
        publisher="Federal Housing Finance Agency",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.fhfa.gov/data/hpi"
        ),
        allowed_hosts=("www.fhfa.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the official 2020 schedule and three explicitly approved reports "
            "sequentially; do not crawl or enumerate the FHFA archive."
        ),
        pagination_policy=(
            "Each selection is one complete report PDF plus the bounded official schedule page."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Every reference month remains tied to its report snapshot. January's initial "
            "0.3 percent becomes 0.5 percent in the April report; February's initial 0.7 "
            "percent becomes 0.8 percent in the May report. Later values never overwrite "
            "the initial-release facts."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Full FHFA HTML and PDF responses remain in local content-addressed storage. The "
            "repository retains only minimal reported facts, hashes, URLs, attribution, and "
            "release-snapshot semantics; no redistribution right is inferred."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified FHFA HPI calendar")
        self.http = http
        self.release_date = release_date
        self.spec = _VERIFIED_RELEASES[release_date]
        self.schedule_endpoint = _SCHEDULE_URL
        self.endpoint = self.spec.report_url

    def fetch(self) -> AdapterBatch:
        schedule_response, schedule_content, schedule_retrieved_at = self.http.get(
            self.schedule_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_schedule_url(schedule_response.request_url)
        schedule_content_type = schedule_response.headers.get("Content-Type", "").split(";", 1)[0]
        if schedule_content_type != "text/html":
            raise SourceSchemaError(
                f"unexpected FHFA HPI schedule content type: {schedule_content_type!r}"
            )
        schedule_semantic_sha256 = self._parse_schedule(schedule_content)

        report_response, report_content, report_retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_report_url(report_response.request_url)
        report_content_type = report_response.headers.get("Content-Type", "").split(";", 1)[0]
        if report_content_type != "application/pdf":
            raise SourceSchemaError(
                f"unexpected FHFA HPI report content type: {report_content_type!r}"
            )
        self._parse_pdf(report_content)

        release_at = datetime.combine(
            self.release_date,
            time(9, 0),
            tzinfo=_NEW_YORK,
        ).astimezone(UTC)
        if min(schedule_retrieved_at, report_retrieved_at) < release_at:
            raise SourceSchemaError("selected FHFA HPI report is not yet knowable")
        if release_at <= _SCHEDULE_CONSERVATIVE_KNOWLEDGE_AT:
            raise SourceSchemaError("FHFA HPI schedule was not known before the release")

        report_digest = source_response_sha256(report_content)
        schedule_digest = source_response_sha256(schedule_content)
        source_version = (
            f"FHFA-HPI:{self.spec.reference_key}:{self.spec.report_kind}:"
            f"pdf:{report_digest[:20]}:schedule:{schedule_semantic_sha256[:20]}"
        )
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(report_response.request_url),
            retrieved_at=report_retrieved_at,
            source_version=source_version,
            sha256=report_digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            vintage_as_of=release_at,
            redistribution_note=self.metadata.redistribution_note,
        )
        snapshots = dict(self.spec.snapshot_change_basis_points)
        previous = dict(self.spec.snapshot_previous_basis_points)
        revisions = {
            month: None if old is None else snapshots[month] - old
            for month, old in previous.items()
        }
        geography_changes = dict(
            zip(_GEOGRAPHIES, self.spec.current_change_basis_points, strict=True)
        )
        geography_indexes = dict(
            zip(_GEOGRAPHIES, self.spec.current_index_values, strict=True)
        )
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.spec.reference_month:%Y%m}:"
                "us_purchase_only_hpi_monthly_change"
            ),
            entity_id="fhfa_hpi:us_purchase_only_seasonally_adjusted",
            source=source,
            interval=BitemporalInterval(
                valid_from=datetime.combine(self.spec.reference_month, time.min, tzinfo=UTC),
                published_at=release_at,
                available_at=release_at,
                ingested_at=report_retrieved_at,
                availability_rule=self.availability_rule,
                availability_confidence=1.0,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload={
                "release_date": self.release_date.isoformat(),
                "reference_month": self.spec.reference_key,
                "release_series": "FHFA House Price Index",
                "report_kind": self.spec.report_kind,
                "metric": "us_purchase_only_hpi_monthly_change_basis_points",
                "value_basis_points": self.spec.current_change_basis_points[0],
                "value_percent": _format_percent_value(
                    self.spec.current_change_basis_points[0]
                ),
                "reported_year_over_year_change_basis_points": (
                    self.spec.year_over_year_basis_points[0]
                ),
                "reported_year_over_year_change_percent": _format_percent_value(
                    self.spec.year_over_year_basis_points[0]
                ),
                "reported_monthly_change_by_geography_basis_points": geography_changes,
                "reported_current_index_by_geography": geography_indexes,
                "release_snapshot_monthly_change_basis_points": snapshots,
                "release_snapshot_previous_estimate_basis_points": previous,
                "release_snapshot_revision_delta_basis_points": revisions,
                "release_time_local": "09:00:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": datetime.combine(
                    self.release_date,
                    time(9, 0),
                    tzinfo=_NEW_YORK,
                ).tzname(),
                "official_release_at": release_at.isoformat(),
                "official_schedule_url": schedule_response.request_url,
                "official_schedule_published_date": (
                    _SCHEDULE_PUBLISHED_DATE.isoformat()
                ),
                "official_schedule_conservative_knowledge_at": (
                    _SCHEDULE_CONSERVATIVE_KNOWLEDGE_AT.isoformat()
                ),
                "official_schedule_semantic_sha256": schedule_semantic_sha256,
                "report_footer_release_time_label": self.spec.footer_time_label,
                "report_footer_time_label_differs_from_schedule_wording": (
                    self.spec.footer_time_label != "9AM ET"
                ),
                "purchase_only_index": True,
                "seasonally_adjusted": True,
                "index_base": "January 1991 = 100",
                "report_table_snapshot_verified": True,
                "report_revision_rows_verified": True,
                "covid_timing_statement_present": True,
                "report_pdf_url": report_response.request_url,
                "report_pdf_sha256": report_digest,
                "report_pdf_pages": self.spec.report_pages,
                "report_pdf_page_width_points": 612,
                "report_pdf_page_height_points": 792,
                "report_pdf_page_rotations": list(self.spec.page_rotations),
                "report_pdf_metadata_creation_date": self.spec.pdf_creation_date,
                "report_pdf_metadata_modification_date": (
                    self.spec.pdf_modification_date
                ),
                "report_pdf_metadata_modified_after_release": (
                    self.release_date == date(2020, 5, 26)
                ),
                "availability_method": (
                    "preannounced_2019_schedule_9am_et_and_matching_dated_report"
                ),
                "unit": "Basis Points of Month-over-Month Price Change",
                "snapshot_semantics": (
                    "reported purchase-only seasonally adjusted HPI value in this release"
                ),
            },
        )
        common_warnings = (
            "FHFA announced the selected 2020 dates and 9 a.m. ET time on August 20, 2019; "
            "the date-only schedule page is conservatively treated as known two days later.",
            "The January report footer says 9AM EST while the preannounced schedule and later "
            "reports say 9AM ET; America/New_York applied to the dated ET schedule controls.",
            "Current HTTP headers and retrieval timestamps are not historical publication "
            "timestamps; report identity, schedule facts, and PDF metadata are retained.",
            "The May report PDF metadata records a June 15 modification; its March value is "
            "therefore an official archived report fact, not proof of unchanged May 26 bytes.",
            "January and February revisions remain later release-snapshot facts and never "
            "overwrite their initial reported changes.",
            "The purchase-only HPI uses repeat transactions and Enterprise data; it is not a "
            "universal home-price level, transaction count, appraisal, or causal measure.",
            "The reports describe contract-to-closing and Enterprise-funding lags; no claim is "
            "made that the values contemporaneously measure COVID-19 effects.",
            "Full official schedule HTML and report PDFs remain local download evidence.",
        )
        schedule_version = f"FHFA-HPI-SCHEDULE:2020:{schedule_semantic_sha256[:24]}"
        schedule_receipt = FetchReceipt(
            adapter_id=self.metadata.adapter_id,
            request_url=_HTTP_URL_ADAPTER.validate_python(schedule_response.request_url),
            retrieved_at=schedule_retrieved_at,
            status_code=schedule_response.status_code,
            content_type=schedule_content_type,
            response_sha256=schedule_digest,
            response_bytes=len(schedule_content),
            record_count=0,
            source_version=schedule_version,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            historical_replay_eligible=True,
            warnings=common_warnings,
        )
        report_receipt = FetchReceipt(
            adapter_id=self.metadata.adapter_id,
            request_url=_HTTP_URL_ADAPTER.validate_python(report_response.request_url),
            retrieved_at=report_retrieved_at,
            status_code=report_response.status_code,
            content_type=report_content_type,
            response_sha256=report_digest,
            response_bytes=len(report_content),
            record_count=1,
            source_version=source_version,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            historical_replay_eligible=True,
            warnings=common_warnings,
        )
        return AdapterBatch(
            records=(record,),
            receipts=(schedule_receipt, report_receipt),
            artifacts=(
                RawArtifact(
                    sha256=schedule_digest,
                    content_type=schedule_content_type,
                    content=schedule_content,
                ),
                RawArtifact(
                    sha256=report_digest,
                    content_type=report_content_type,
                    content=report_content,
                ),
            ),
        )

    def _parse_schedule(self, content: bytes) -> str:
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("FHFA HPI schedule is not valid UTF-8") from error
        parser = _VisibleTextParser()
        try:
            parser.feed(decoded)
            parser.close()
        except (ValueError, TypeError) as error:
            raise SourceSchemaError("FHFA HPI schedule HTML could not be parsed") from error
        text = _normalize(" ".join(parser.parts))
        markers = (
            "FHFA Announces 2020 Release Dates for House Price Index",
            "08/20/2019",
            "will be released at 9 a.m. ET on the following dates in 2020",
            "Wednesday, March 25 Monthly Index",
            "Wednesday, April 22 Monthly Index",
            "Tuesday, May 26 Quarterly and Monthly Index",
        )
        if any(marker not in text for marker in markers):
            raise SourceSchemaError("FHFA HPI official schedule facts do not match")
        if text.count("will be released at 9 a.m. ET on the following dates in 2020") != 1:
            raise SourceSchemaError("FHFA HPI official schedule statement is not unique")
        return _SCHEDULE_SEMANTIC_SHA256

    def _parse_pdf(self, content: bytes) -> None:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("FHFA HPI report is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != self.spec.report_pages:
                raise SourceSchemaError(
                    f"FHFA HPI report must contain exactly {self.spec.report_pages} pages"
                )
            pages: list[str] = []
            dimensions: list[tuple[float, float]] = []
            rotations: list[int] = []
            for page in reader.pages:
                dimensions.append(
                    (
                        round(float(page.mediabox.width), 2),
                        round(float(page.mediabox.height), 2),
                    )
                )
                rotations.append(int(page.get("/Rotate", 0)) % 360)
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError("FHFA HPI report has a blank text layer")
                pages.append(_normalize(extracted))
            if any(item != (612.0, 792.0) for item in dimensions):
                raise SourceSchemaError("FHFA HPI report page dimensions do not match")
            if tuple(rotations) != self.spec.page_rotations:
                raise SourceSchemaError("FHFA HPI report page rotations do not match")
            metadata = reader.metadata
            if metadata is None:
                raise SourceSchemaError("FHFA HPI report metadata is missing")
            expected_metadata = {
                "/Author": "Federal Housing Finance Agency",
                "/Title": self.spec.pdf_title,
                "/Subject": self.spec.pdf_subject,
                "/Keywords": "FHFA, house price index, HPI",
                "/CreationDate": self.spec.pdf_creation_date,
                "/ModDate": self.spec.pdf_modification_date,
            }
            if any(metadata.get(key) != value for key, value in expected_metadata.items()):
                raise SourceSchemaError("FHFA HPI report metadata does not match")
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("FHFA HPI report PDF could not be parsed") from error
        self._validate_cover(pages[0])
        self._validate_press_release(
            " ".join(pages[index] for index in self.spec.press_page_indexes)
        )
        self._validate_table(pages[self.spec.table_page_index])
        self._validate_overview(pages[self.spec.overview_page_index])
        self._validate_schedule_footer(pages[self.spec.schedule_page_index])

    def _validate_cover(self, cover: str) -> None:
        if any(marker not in cover for marker in self.spec.cover_markers):
            raise SourceSchemaError("FHFA HPI report cover identity does not match")

    def _validate_press_release(self, press: str) -> None:
        markers = (
            *self.spec.headline_markers,
            self.spec.next_release_marker,
            self.spec.covid_timing_marker,
            "weighted, repeat-sales statistical technique",
            "seasonally adjusted, purchase-only data",
        )
        if any(marker not in press for marker in markers):
            raise SourceSchemaError("FHFA HPI press-release facts do not match")

    def _validate_table(self, table: str) -> None:
        current_label = _month_change_label(self.spec.reference_month)
        current_row = _percent_row(current_label, self.spec.current_change_basis_points)
        year_over_year_label = (
            f"{self.spec.reference_month:%b} 19 - {self.spec.reference_month:%b} 20"
        )
        year_over_year_row = _percent_row(
            year_over_year_label,
            self.spec.year_over_year_basis_points,
        )
        index_row = (
            f"{self.spec.reference_month:%B}-20 "
            + " ".join(self.spec.current_index_values)
        )
        markers = (
            "Monthly Price Change Estimates for U.S. and Census Divisions",
            "Purchase-Only Index (Seasonally Adjusted)",
            current_row,
            year_over_year_row,
            index_row,
            "Monthly Index Values for Latest 18 Months: U.S. and Census Divisions",
            "January 1991 = 100",
            "Source: FHFA",
        )
        if any(marker not in table for marker in markers):
            raise SourceSchemaError("FHFA HPI monthly table values do not match")
        for revision in self.spec.revision_rows:
            current = _percent_row(revision.label, revision.current_basis_points)
            previous = "(Previous Estimate) " + " ".join(
                _format_percent(item) for item in revision.previous_basis_points
            )
            if current not in table or previous not in table:
                raise SourceSchemaError("FHFA HPI revision rows do not match")

    @staticmethod
    def _validate_overview(overview: str) -> None:
        markers = (
            "An overview of the FHFA HPI",
            "broad economic measure of the movement of single-family house prices",
            "indexes cover all 50 states and over 400 American cities",
            "Purchase-Only",
            "Tracks changes in transaction prices for conforming, conventional mortgages",
        )
        if any(marker not in overview for marker in markers):
            raise SourceSchemaError("FHFA HPI methodology overview does not match")

    def _validate_schedule_footer(self, footer: str) -> None:
        markers = (
            "FHFA HPI Release Dates for 2020",
            f"Public releases occur at {self.spec.footer_time_label}",
            "Wednesday, March 25 Monthly Index January 2020",
            "Wednesday, April 22 Monthly Index February 2020",
            "Tuesday, May 26 Quarterly Index (with Monthly Tables) March 2020 and 2020Q1",
        )
        if any(marker not in footer for marker in markers):
            raise SourceSchemaError("FHFA HPI report schedule footer does not match")

    def _validate_schedule_url(self, response_url: str) -> None:
        _validate_exact_fhfa_url(
            response_url,
            expected_path=urlparse(self.schedule_endpoint).path,
            context="schedule",
        )

    def _validate_report_url(self, response_url: str) -> None:
        _validate_exact_fhfa_url(
            response_url,
            expected_path=self.spec.endpoint_path,
            context="report",
        )


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _validate_exact_fhfa_url(response_url: str, *, expected_path: str, context: str) -> None:
    parsed = urlparse(response_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.fhfa.gov"
        or parsed.path != expected_path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SourceSchemaError(f"FHFA HPI {context} response URL does not match request")


def _month_change_label(reference_month: date) -> str:
    prior_year = reference_month.year if reference_month.month > 1 else reference_month.year - 1
    prior_month = 12 if reference_month.month == 1 else reference_month.month - 1
    prior = date(prior_year, prior_month, 1)
    return f"{prior:%b} {prior:%y} - {reference_month:%b} {reference_month:%y}"


def _percent_row(label: str, values: tuple[int, ...]) -> str:
    return f"{label} " + " ".join(_format_percent(item) for item in values)


def _format_percent(value_basis_points: int) -> str:
    return f"{Decimal(value_basis_points) / Decimal(100):.1f}%"


def _format_percent_value(value_basis_points: int) -> str:
    return f"{Decimal(value_basis_points) / Decimal(100):.1f}"


def _normalize(value: str) -> str:
    return " ".join(
        value.replace("\u00a0", " ")
        .replace("\u200b", " ")
        .replace("\ufeff", " ")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .split()
    )
