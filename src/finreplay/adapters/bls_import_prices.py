"""Paired archived BLS U.S. Import Price Index release adapter."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
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
_DOT_LEADERS = re.compile(r"(?:\.\s*){2,}")
_TECHNICAL_MARKER = (
    "Import and Export Goods and Services Price Indexes - All indexes use a modified "
    "Laspeyres formula and are not seasonally adjusted."
)
_MEASUREMENT_MARKER = (
    "Import Price Goods Indexes - Items are classified by the Harmonized Tariff Schedule "
    "of the United States Annotated (TSUSA). Import prices are based on U.S. dollar prices "
    "paid by the U.S. importer."
)
_EXPORT_MEASUREMENT_MARKER = (
    "Export Price Goods Indexes - Items are classified by the Harmonized Schedule B "
    "classification system of the U.S. Bureau of the Census. The prices used are generally "
    'either "free alongside ship" (f.a.s.) factory or "free on board" (f.o.b.) transaction '
    "prices, depending on the practices of the individual industry."
)
_REVISION_MARKER = "Data may be revised in each of the 3 months after original publication."
_COVID_MARKER = (
    "Coronavirus (COVID-19) Impact on March 2020 Import and Export Price Index Survey Data "
    "The import and export price quotes are requested for transactions occurring as close "
    "to the first day of the month as possible. While not directly related to the COVID-19 "
    "pandemic, response rates for March were approximately 6.5 percentage points lower than "
    "March 2019. No changes in estimation procedures were necessary."
)


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    reference_month: date
    release_number: str
    timezone_abbreviation: str
    headline_marker: str
    monthly_change_tenths_percent: int
    prior_month: date
    prior_month_change_tenths_percent: int
    second_prior_month: date
    second_prior_month_change_tenths_percent: int
    year_over_year_change_tenths_percent: int
    prior_unadjusted_index: str
    current_unadjusted_index: str
    table_monthly_changes_tenths_percent: tuple[int, int, int, int]
    covid_methodology_marker: str | None

    @property
    def reference_label(self) -> str:
        return self.reference_month.strftime("%B %Y")

    @property
    def title_marker(self) -> str:
        return f"U.S. IMPORT AND EXPORT PRICE INDEXES - {self.reference_label.upper()}"

    @property
    def embargo_marker(self) -> str:
        return (
            f"8:30 a.m. ({self.timezone_abbreviation}) "
            f"{calendar.day_name[self.release_date.weekday()]}, "
            f"{self.release_date:%B} {self.release_date.day}, {self.release_date:%Y}"
        )

    @property
    def table_row_marker(self) -> str:
        changes = " ".join(
            _tenths_text(value) for value in self.table_monthly_changes_tenths_percent
        )
        return " ".join(
            (
                "All commodities",
                "100.000",
                self.prior_unadjusted_index,
                self.current_unadjusted_index,
                _tenths_text(self.year_over_year_change_tenths_percent),
                changes,
            )
        )


_VERIFIED_RELEASES = {
    date(2020, 2, 14): _ReleaseSpec(
        release_date=date(2020, 2, 14),
        reference_month=date(2020, 1, 1),
        release_number="USDL-20-0247",
        timezone_abbreviation="EST",
        headline_marker=(
            "U.S. import prices were unchanged in January, the U.S. Bureau of Labor "
            "Statistics reported today, following 0.2-percent advances the 2 previous months."
        ),
        monthly_change_tenths_percent=0,
        prior_month=date(2019, 12, 1),
        prior_month_change_tenths_percent=2,
        second_prior_month=date(2019, 11, 1),
        second_prior_month_change_tenths_percent=2,
        year_over_year_change_tenths_percent=3,
        prior_unadjusted_index="125.0",
        current_unadjusted_index="125.0",
        table_monthly_changes_tenths_percent=(-4, 2, 2, 0),
        covid_methodology_marker=None,
    ),
    date(2020, 3, 13): _ReleaseSpec(
        release_date=date(2020, 3, 13),
        reference_month=date(2020, 2, 1),
        release_number="USDL-20-0405",
        timezone_abbreviation="EDT",
        headline_marker=(
            "U.S. import prices declined 0.5 percent in February, the U.S. Bureau of Labor "
            "Statistics reported today, after ticking up 0.1 percent in January."
        ),
        monthly_change_tenths_percent=-5,
        prior_month=date(2020, 1, 1),
        prior_month_change_tenths_percent=1,
        second_prior_month=date(2019, 12, 1),
        second_prior_month_change_tenths_percent=2,
        year_over_year_change_tenths_percent=-12,
        prior_unadjusted_index="125.0",
        current_unadjusted_index="124.4",
        table_monthly_changes_tenths_percent=(2, 2, 1, -5),
        covid_methodology_marker=None,
    ),
    date(2020, 4, 14): _ReleaseSpec(
        release_date=date(2020, 4, 14),
        reference_month=date(2020, 3, 1),
        release_number="USDL-20-0610",
        timezone_abbreviation="EDT",
        headline_marker=(
            "Prices for U.S. imports fell 2.3 percent in March, the U.S. Bureau of Labor "
            "Statistics reported today, following a 0.7-percent decline the previous month."
        ),
        monthly_change_tenths_percent=-23,
        prior_month=date(2020, 2, 1),
        prior_month_change_tenths_percent=-7,
        second_prior_month=date(2020, 1, 1),
        second_prior_month_change_tenths_percent=2,
        year_over_year_change_tenths_percent=-41,
        prior_unadjusted_index="124.3",
        current_unadjusted_index="121.4",
        table_monthly_changes_tenths_percent=(2, 2, -7, -23),
        covid_methodology_marker=_COVID_MARKER,
    ),
}

_VERIFIED_EXPORT_RELEASES = {
    date(2020, 2, 14): _ReleaseSpec(
        release_date=date(2020, 2, 14),
        reference_month=date(2020, 1, 1),
        release_number="USDL-20-0247",
        timezone_abbreviation="EST",
        headline_marker=(
            "Prices for U.S. exports advanced 0.7 percent in January, after declining "
            "0.2 percent the previous month."
        ),
        monthly_change_tenths_percent=7,
        prior_month=date(2019, 12, 1),
        prior_month_change_tenths_percent=-2,
        second_prior_month=date(2019, 11, 1),
        second_prior_month_change_tenths_percent=1,
        year_over_year_change_tenths_percent=5,
        prior_unadjusted_index="125.0",
        current_unadjusted_index="125.9",
        table_monthly_changes_tenths_percent=(0, 1, -2, 7),
        covid_methodology_marker=None,
    ),
    date(2020, 3, 13): _ReleaseSpec(
        release_date=date(2020, 3, 13),
        reference_month=date(2020, 2, 1),
        release_number="USDL-20-0405",
        timezone_abbreviation="EDT",
        headline_marker=(
            "Prices for U.S. exports decreased 1.1 percent in February, after advancing "
            "0.6 percent the previous month."
        ),
        monthly_change_tenths_percent=-11,
        prior_month=date(2020, 1, 1),
        prior_month_change_tenths_percent=6,
        second_prior_month=date(2019, 12, 1),
        second_prior_month_change_tenths_percent=-2,
        year_over_year_change_tenths_percent=-13,
        prior_unadjusted_index="125.8",
        current_unadjusted_index="124.4",
        table_monthly_changes_tenths_percent=(1, -2, 6, -11),
        covid_methodology_marker=None,
    ),
    date(2020, 4, 14): _ReleaseSpec(
        release_date=date(2020, 4, 14),
        reference_month=date(2020, 3, 1),
        release_number="USDL-20-0610",
        timezone_abbreviation="EDT",
        headline_marker=(
            "U.S. export prices decreased 1.6 percent in March, after falling 1.1 percent "
            "in February."
        ),
        monthly_change_tenths_percent=-16,
        prior_month=date(2020, 2, 1),
        prior_month_change_tenths_percent=-11,
        second_prior_month=date(2020, 1, 1),
        second_prior_month_change_tenths_percent=6,
        year_over_year_change_tenths_percent=-36,
        prior_unadjusted_index="124.4",
        current_unadjusted_index="122.4",
        table_monthly_changes_tenths_percent=(-2, 6, -11, -16),
        covid_methodology_marker=_COVID_MARKER,
    ),
}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0 and data.strip():
            self.parts.append(data)


class BLSImportPriceArchiveAdapter:
    """Retrieve one fixed archived BLS Import Price Index HTML/PDF pair."""

    verified_releases = _VERIFIED_RELEASES
    series_label = "import-price"
    entity_id = "bls_import_price_index:all_imports_united_states"
    record_suffix = "all_imports_monthly_change"
    metric = "all_imports_monthly_change_not_seasonally_adjusted"
    table_page_index = 4
    table_page_marker = "Table 1. U.S. import price indexes and percent changes"
    table_error_label = "Table 1"
    table_warning_label = "Table 1"
    table_payload_prefix = "table1"
    measurement_marker = _MEASUREMENT_MARKER
    measurement_boundary = (
        "U.S. dollar prices paid by U.S. importers; generally f.o.b. foreign-port "
        "or c.i.f. U.S.-port transaction prices, aggregated as a modified "
        "Laspeyres price index"
    )
    snapshot_semantics = "all-import monthly change reported in this archived release"
    aggregate_boundary_warning = (
        "The all-import price index aggregates U.S.-importer transaction prices and is not "
        "an import quantity, nominal trade value, tariff, CPI, firm result, or return."
    )
    availability_rule = (
        "Each selected BLS Import and Export Price Index release states that transmission is "
        "embargoed until 8:30 a.m. EST or EDT on its named release date. FinReplay validates "
        "the weekday and timezone abbreviation against America/New_York, cross-checks the "
        "complete archived HTML and PDF, and makes the snapshot eligible at that exact time. "
        "Current retrieval metadata is never backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="bls.import_prices.archived_all_imports",
        title="BLS archived all-import price-index release facts",
        publisher="U.S. Bureau of Labor Statistics",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.bls.gov/bls/news-release/ximpim.htm"
        ),
        allowed_hosts=("www.bls.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 HTML/PDF pairs sequentially; "
            "do not crawl or enumerate the archive. Use a contact-shaped research user agent."
        ),
        pagination_policy=(
            "Each selection uses one complete archived HTML release and one complete 18-page "
            "PDF without API pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each release is retained as a versioned snapshot. BLS states that monthly data "
            "may be revised in each of the three months after original publication. Adjacent "
            "release values are cross-checked, and later revisions never overwrite first reports."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.REDISTRIBUTABLE,
        redistribution_note=(
            "BLS-published material is public domain except identified third-party material. "
            "Attribute the U.S. Bureau of Labor Statistics, retain archive URLs and release "
            "dates, and do not use the protected BLS emblem."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in self.verified_releases:
            raise ValueError(
                f"release date is not in the verified BLS {self.series_label} calendar"
            )
        self.http = http
        self.release_date = release_date
        self.spec = self.verified_releases[release_date]
        stem = f"ximpim_{release_date:%m%d%Y}"
        self.html_endpoint = f"https://www.bls.gov/news.release/archives/{stem}.htm"
        self.pdf_endpoint = f"https://www.bls.gov/news.release/archives/{stem}.pdf"

    def fetch(self) -> AdapterBatch:
        html_response, html_content, html_retrieved_at = self.http.get(
            self.html_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        pdf_response, pdf_content, pdf_retrieved_at = self.http.get(
            self.pdf_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(html_response.request_url, kind="html")
        self._validate_response_url(pdf_response.request_url, kind="pdf")
        html_content_type = html_response.headers.get("Content-Type", "").split(";", 1)[0]
        if html_content_type not in {"text/html", "application/xhtml+xml"}:
            raise SourceSchemaError(
                f"unexpected BLS {self.series_label} HTML content type: {html_content_type!r}"
            )
        pdf_content_type = pdf_response.headers.get("Content-Type", "").split(";", 1)[0]
        if pdf_content_type != "application/pdf":
            raise SourceSchemaError(
                f"unexpected BLS {self.series_label} PDF content type: {pdf_content_type!r}"
            )
        html_text, html_encoding = self._parse_html(html_content)
        pdf_text = self._parse_pdf(pdf_content)
        self._validate_release_text(html_text, source_kind="HTML")
        self._validate_release_text(pdf_text, source_kind="PDF")

        release_local = datetime.combine(self.release_date, time(8, 30), tzinfo=_NEW_YORK)
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError(
                f"BLS {self.series_label} timezone does not match New York calendar"
            )
        release_at = release_local.astimezone(UTC)
        if html_retrieved_at < release_at or pdf_retrieved_at < release_at:
            raise SourceSchemaError(
                f"selected BLS {self.series_label} release is not yet knowable"
            )
        retrieved_at = max(html_retrieved_at, pdf_retrieved_at)

        html_digest = source_response_sha256(html_content)
        pdf_digest = source_response_sha256(pdf_content)
        source_version = (
            f"BLS-MXP:{self.spec.reference_month:%Y-%m}:{self.spec.release_number}:"
            f"html:{html_digest[:20]}:pdf:{pdf_digest[:20]}"
        )
        previous_value = self._previous_release_same_reference_value()
        revision_delta = (
            None
            if previous_value is None
            else self.spec.prior_month_change_tenths_percent - previous_value
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
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.spec.reference_month:%Y%m}:"
                f"{self.record_suffix}"
            ),
            entity_id=self.entity_id,
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
                "release_series": "U.S. Import and Export Price Indexes",
                "metric": self.metric,
                "value_tenths_percent": self.spec.monthly_change_tenths_percent,
                "value_basis_points": self.spec.monthly_change_tenths_percent * 10,
                "value_percent_text": _tenths_text(self.spec.monthly_change_tenths_percent),
                "prior_month": self.spec.prior_month.strftime("%Y-%m"),
                "prior_month_change_tenths_percent": (self.spec.prior_month_change_tenths_percent),
                "prior_month_value_in_previous_release_tenths_percent": previous_value,
                "prior_month_revision_delta_tenths_percent": revision_delta,
                "second_prior_month": self.spec.second_prior_month.strftime("%Y-%m"),
                "second_prior_month_change_tenths_percent": (
                    self.spec.second_prior_month_change_tenths_percent
                ),
                "year_over_year_change_tenths_percent": (
                    self.spec.year_over_year_change_tenths_percent
                ),
                f"{self.table_payload_prefix}_prior_unadjusted_index": (
                    self.spec.prior_unadjusted_index
                ),
                f"{self.table_payload_prefix}_current_unadjusted_index": (
                    self.spec.current_unadjusted_index
                ),
                f"{self.table_payload_prefix}_monthly_change_sequence_tenths_percent": list(
                    self.spec.table_monthly_changes_tenths_percent
                ),
                "revision_window_months": 3,
                "index_formula": "modified Laspeyres",
                "seasonally_adjusted": False,
                "measurement_boundary": self.measurement_boundary,
                "covid_methodology_statement_present": (
                    self.spec.covid_methodology_marker is not None
                ),
                "covid_methodology_statement": self.spec.covid_methodology_marker,
                "release_time_local": "08:30:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "official_release_at": release_at.isoformat(),
                "snapshot_semantics": self.snapshot_semantics,
                "html_pdf_crosscheck_verified": True,
                "release_html_url": html_response.request_url,
                "release_html_sha256": html_digest,
                "release_html_encoding": html_encoding,
                "release_pdf_url": pdf_response.request_url,
                "release_pdf_sha256": pdf_digest,
                "release_pdf_pages": 18,
                "release_pdf_page_width_points": 612,
                "release_pdf_page_height_points": 792,
                "release_pdf_page_rotation_degrees": 0,
                "availability_method": "exact_bls_embargo_end_crosschecked_html_pdf",
                "unit": "Tenths of a Percent",
            },
        )
        warnings = (
            "The exact 8:30 a.m. EST/EDT embargo end is stated in both archived formats and "
            "validated against America/New_York; retrieval headers are not historical timing.",
            "The full HTML and every nonblank PDF page are validated; release identity, "
            f"headline, {self.table_warning_label} values, technical definition, and revision "
            "rule must agree.",
            "Monthly data may be revised for three releases after original publication; later "
            "values never overwrite earlier snapshots.",
            self.aggregate_boundary_warning,
            "The index is not seasonally adjusted; annual changes and detailed categories are "
            "source facts, not FinReplay range inputs, probabilities, or causal estimates.",
            "The March COVID-19 text reports timing, response-rate, and estimation-procedure "
            "facts; it does not establish pandemic causality or unaffected measurement.",
        )
        receipts = (
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(html_response.request_url),
                retrieved_at=html_retrieved_at,
                status_code=html_response.status_code,
                content_type=html_content_type,
                response_sha256=html_digest,
                response_bytes=len(html_content),
                record_count=0,
                source_version=source_version,
                temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
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
        )
        return AdapterBatch(
            records=(record,),
            receipts=receipts,
            artifacts=(
                RawArtifact(
                    sha256=html_digest,
                    content_type=html_content_type,
                    content=html_content,
                ),
                RawArtifact(
                    sha256=pdf_digest,
                    content_type=pdf_content_type,
                    content=pdf_content,
                ),
            ),
        )

    def _parse_html(self, content: bytes) -> tuple[str, str]:
        decoded, encoding = _decode_html(content)
        parser = _TextParser()
        try:
            parser.feed(decoded)
            parser.close()
        except (AssertionError, ValueError) as error:
            raise SourceSchemaError(
                f"BLS {self.series_label} HTML is not structurally valid"
            ) from error
        text = _normalize(" ".join(parser.parts))
        if not text:
            raise SourceSchemaError(f"BLS {self.series_label} HTML has no visible text")
        return text, encoding

    def _parse_pdf(self, content: bytes) -> str:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError(f"BLS {self.series_label} release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 18:
                raise SourceSchemaError(
                    f"BLS {self.series_label} PDF page count does not match"
                )
            pages = []
            for page in reader.pages:
                geometry = (
                    round(float(page.mediabox.width), 2),
                    round(float(page.mediabox.height), 2),
                )
                if geometry != (612.0, 792.0) or page.rotation != 0:
                    raise SourceSchemaError(
                        f"BLS {self.series_label} PDF page geometry does not match"
                    )
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError(
                        f"BLS {self.series_label} PDF has a blank text layer"
                    )
                pages.append(_normalize(extracted))
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError(
                f"BLS {self.series_label} release PDF could not be parsed"
            ) from error
        if self.spec.title_marker not in pages[0]:
            raise SourceSchemaError(
                f"BLS {self.series_label} PDF first-page identity does not match"
            )
        if self.table_page_marker not in pages[self.table_page_index]:
            raise SourceSchemaError(
                f"BLS {self.series_label} PDF {self.table_error_label} page does not match"
            )
        if "Table 10. U.S. international price indexes" not in pages[15]:
            raise SourceSchemaError(
                f"BLS {self.series_label} PDF Table 10 page does not match"
            )
        if "TECHNICAL NOTE Import and Export Goods and Services Price Indexes" not in pages[16]:
            raise SourceSchemaError(
                f"BLS {self.series_label} PDF technical-note page does not match"
            )
        if "Import Price Indexes by Locality of Origin" not in pages[17]:
            raise SourceSchemaError(
                f"BLS {self.series_label} PDF final technical page does not match"
            )
        return _normalize(" ".join(pages))

    def _validate_release_text(self, text: str, *, source_kind: str) -> None:
        unique_markers = (
            "Transmission of material in this release is embargoed until",
            self.spec.embargo_marker,
            self.spec.release_number,
            self.spec.title_marker,
            self.spec.headline_marker,
            _TECHNICAL_MARKER,
            self.measurement_marker,
        )
        if any(text.count(marker) != 1 for marker in unique_markers):
            raise SourceSchemaError(
                f"BLS {self.series_label} {source_kind} identity or headline does not match"
            )
        if text.count(_REVISION_MARKER) < 7:
            raise SourceSchemaError(
                f"BLS {self.series_label} {source_kind} revision rule does not match"
            )
        table_text = _normalize(_DOT_LEADERS.sub(" ", text))
        if table_text.count(self.spec.table_row_marker) != 1:
            raise SourceSchemaError(
                f"BLS {self.series_label} {source_kind} {self.table_error_label} values "
                "do not match"
            )
        if (_COVID_MARKER in text) is (self.spec.covid_methodology_marker is None):
            raise SourceSchemaError(
                f"BLS {self.series_label} {source_kind} COVID-19 methodology statement "
                "does not match"
            )

    def _previous_release_same_reference_value(self) -> int | None:
        prior = next(
            (
                candidate
                for candidate in self.verified_releases.values()
                if candidate.reference_month == self.spec.prior_month
            ),
            None,
        )
        if prior is None:
            return None
        return prior.monthly_change_tenths_percent

    def _validate_response_url(self, response_url: str, *, kind: str) -> None:
        if kind not in {"html", "pdf"}:
            raise ValueError(f"BLS {self.series_label} response kind must be html or pdf")
        suffix = "htm" if kind == "html" else "pdf"
        expected_path = f"/news.release/archives/ximpim_{self.release_date:%m%d%Y}.{suffix}"
        parsed = urlparse(response_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError(
                f"BLS {self.series_label} response URL does not match request"
            )


class BLSExportPriceArchiveAdapter(BLSImportPriceArchiveAdapter):
    """Retrieve one fixed archived BLS all-export price HTML/PDF pair."""

    verified_releases = _VERIFIED_EXPORT_RELEASES
    series_label = "export-price"
    entity_id = "bls_export_price_index:all_exports_united_states"
    record_suffix = "all_exports_monthly_change"
    metric = "all_exports_monthly_change_not_seasonally_adjusted"
    table_page_index = 5
    table_page_marker = "Table 2. U.S. export price indexes and percent changes"
    table_error_label = "Table 2"
    table_warning_label = "Table 2"
    table_payload_prefix = "table2"
    measurement_marker = _EXPORT_MEASUREMENT_MARKER
    measurement_boundary = (
        "U.S. export transaction prices, generally f.a.s. factory or f.o.b., classified "
        "under Schedule B and aggregated as a modified Laspeyres price index"
    )
    snapshot_semantics = "all-export monthly change reported in this archived release"
    aggregate_boundary_warning = (
        "The all-export price index aggregates U.S.-export transaction prices and is not "
        "an export quantity, nominal trade value, tariff, PPI, firm result, or return."
    )
    metadata = AdapterMetadata(
        adapter_id="bls.export_prices.archived_all_exports",
        title="BLS archived all-export price-index release facts",
        publisher="U.S. Bureau of Labor Statistics",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.bls.gov/bls/news-release/ximpim.htm"
        ),
        allowed_hosts=("www.bls.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 HTML/PDF pairs sequentially; "
            "do not crawl or enumerate the archive. Use a contact-shaped research user agent."
        ),
        pagination_policy=(
            "Each selection uses one complete archived HTML release and one complete 18-page "
            "PDF without API pagination."
        ),
        availability_rule=BLSImportPriceArchiveAdapter.availability_rule,
        revision_behavior=(
            "Each release is retained as a versioned snapshot. BLS states that monthly data "
            "may be revised in each of the three months after original publication. Adjacent "
            "release values are cross-checked, and later revisions never overwrite first reports."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.REDISTRIBUTABLE,
        redistribution_note=(
            "BLS-published material is public domain except identified third-party material. "
            "Attribute the U.S. Bureau of Labor Statistics, retain archive URLs and release "
            "dates, and do not use the protected BLS emblem."
        ),
    )


def _tenths_text(value: int) -> str:
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    return f"{sign}{magnitude // 10}.{magnitude % 10}"


def _decode_html(content: bytes) -> tuple[str, str]:
    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try:
            return content.decode("windows-1252"), "windows-1252"
        except UnicodeDecodeError as error:
            raise SourceSchemaError(
                "BLS international-price HTML is neither valid UTF-8 nor Windows-1252"
            ) from error


def _normalize(value: str) -> str:
    return " ".join(
        value.replace("\u00a0", " ")
        .replace("\ufb01", "fi")
        .replace("\ufb02", "fl")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("|", " ")
        .split()
    )
