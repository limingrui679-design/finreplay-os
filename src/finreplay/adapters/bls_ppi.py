"""Paired archived BLS Producer Price Index release adapter."""

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
_TECHNICAL_DEFINITION = (
    "measures the average change over time in prices received (price changes) by producers "
    "for domestically produced goods, services, and construction."
)
_REVISION_MARKER = "All indexes are subject to revision 4 months after original publication."


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    reference_month: date
    release_number: str
    timezone_abbreviation: str
    page_count: int
    technical_page_index: int
    table1_page_index: int
    headline_marker: str
    prior_changes_marker: str
    year_over_year_marker: str
    monthly_change_tenths_percent: int
    prior_month: date
    prior_month_change_tenths_percent: int
    second_prior_month: date
    second_prior_month_change_tenths_percent: int
    year_over_year_change_tenths_percent: int
    table_earlier_unadjusted_index: str
    table_prior_unadjusted_index: str
    table_current_unadjusted_index: str
    table_unadjusted_monthly_change_tenths_percent: int
    table_seasonally_adjusted_changes_tenths_percent: tuple[int, int, int]
    covid_methodology_marker: str | None

    @property
    def reference_label(self) -> str:
        return self.reference_month.strftime("%B %Y")

    @property
    def title_marker(self) -> str:
        return f"PRODUCER PRICE INDEXES - {self.reference_label.upper()}"

    @property
    def table_row_marker(self) -> str:
        changes = " ".join(
            _tenths_text(value)
            for value in self.table_seasonally_adjusted_changes_tenths_percent
        )
        return " ".join(
            (
                "Final demand",
                "100.000",
                self.table_earlier_unadjusted_index,
                self.table_prior_unadjusted_index,
                self.table_current_unadjusted_index,
                _tenths_text(self.year_over_year_change_tenths_percent),
                _tenths_text(self.table_unadjusted_monthly_change_tenths_percent),
                changes,
            )
        )


_MARCH_COVID_MARKER = (
    "The Producer Price Index (PPI) pricing date was March 10. Response rates for March were "
    "consistent with those of February, and no changes in estimation procedures were necessary."
)
_APRIL_COVID_MARKER = (
    "The Producer Price Index (PPI) response rates for April were consistent with those of "
    "March and February, and no changes in estimation procedures were necessary."
)
_COVID_MARKERS = (_MARCH_COVID_MARKER, _APRIL_COVID_MARKER)

_VERIFIED_RELEASES = {
    date(2020, 3, 12): _ReleaseSpec(
        release_date=date(2020, 3, 12),
        reference_month=date(2020, 2, 1),
        release_number="USDL 20-0404",
        timezone_abbreviation="EDT",
        page_count=32,
        technical_page_index=6,
        table1_page_index=13,
        headline_marker=(
            "The Producer Price Index for final demand fell 0.6 percent in February, seasonally "
            "adjusted, the U.S. Bureau of Labor Statistics reported today."
        ),
        prior_changes_marker=(
            "Final demand prices advanced 0.5 percent in January and 0.2 percent in December."
        ),
        year_over_year_marker=(
            "On an unadjusted basis, the final demand index increased 1.3 percent for the 12 "
            "months ended in February."
        ),
        monthly_change_tenths_percent=-6,
        prior_month=date(2020, 1, 1),
        prior_month_change_tenths_percent=5,
        second_prior_month=date(2019, 12, 1),
        second_prior_month_change_tenths_percent=2,
        year_over_year_change_tenths_percent=13,
        table_earlier_unadjusted_index="118.8",
        table_prior_unadjusted_index="119.1",
        table_current_unadjusted_index="118.6",
        table_unadjusted_monthly_change_tenths_percent=-4,
        table_seasonally_adjusted_changes_tenths_percent=(2, 5, -6),
        covid_methodology_marker=None,
    ),
    date(2020, 4, 9): _ReleaseSpec(
        release_date=date(2020, 4, 9),
        reference_month=date(2020, 3, 1),
        release_number="USDL 20-0567",
        timezone_abbreviation="EDT",
        page_count=31,
        technical_page_index=5,
        table1_page_index=12,
        headline_marker=(
            "The Producer Price Index for final demand fell 0.2 percent in March, seasonally "
            "adjusted, the U.S. Bureau of Labor Statistics reported today."
        ),
        prior_changes_marker=(
            "Final demand prices declined 0.6 percent in February and increased 0.5 percent "
            "in January."
        ),
        year_over_year_marker=(
            "On an unadjusted basis, the final demand index advanced 0.7 percent for the 12 "
            "months ended in March."
        ),
        monthly_change_tenths_percent=-2,
        prior_month=date(2020, 2, 1),
        prior_month_change_tenths_percent=-6,
        second_prior_month=date(2020, 1, 1),
        second_prior_month_change_tenths_percent=5,
        year_over_year_change_tenths_percent=7,
        table_earlier_unadjusted_index="118.3",
        table_prior_unadjusted_index="118.6",
        table_current_unadjusted_index="118.5",
        table_unadjusted_monthly_change_tenths_percent=-1,
        table_seasonally_adjusted_changes_tenths_percent=(5, -6, -2),
        covid_methodology_marker=_MARCH_COVID_MARKER,
    ),
    date(2020, 5, 13): _ReleaseSpec(
        release_date=date(2020, 5, 13),
        reference_month=date(2020, 4, 1),
        release_number="USDL 20-0920",
        timezone_abbreviation="EDT",
        page_count=31,
        technical_page_index=5,
        table1_page_index=12,
        headline_marker=(
            "The Producer Price Index for final demand declined 1.3 percent in April, "
            "seasonally adjusted, the U.S. Bureau of Labor Statistics reported today."
        ),
        prior_changes_marker=(
            "Final demand prices fell 0.2 percent in March and 0.6 percent in February."
        ),
        year_over_year_marker=(
            "On an unadjusted basis, the final demand index moved down 1.2 percent for the 12 "
            "months ended in April"
        ),
        monthly_change_tenths_percent=-13,
        prior_month=date(2020, 3, 1),
        prior_month_change_tenths_percent=-2,
        second_prior_month=date(2020, 2, 1),
        second_prior_month_change_tenths_percent=-6,
        year_over_year_change_tenths_percent=-12,
        table_earlier_unadjusted_index="118.4",
        table_prior_unadjusted_index="118.5",
        table_current_unadjusted_index="117.1",
        table_unadjusted_monthly_change_tenths_percent=-12,
        table_seasonally_adjusted_changes_tenths_percent=(-6, -2, -13),
        covid_methodology_marker=_APRIL_COVID_MARKER,
    ),
}


class _TextParser(HTMLParser):
    """Extract visible text without executing page content."""

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


class BLSPPIArchiveAdapter:
    """Retrieve one fixed BLS PPI archived HTML/PDF release pair."""

    availability_rule = (
        "Each selected BLS PPI release explicitly states that transmission is embargoed until "
        "8:30 a.m. EDT on its named release date. FinReplay validates the weekday and EDT "
        "abbreviation against America/New_York, cross-checks the archived HTML and PDF, and "
        "makes the snapshot eligible at that exact time. Current retrieval metadata is never "
        "backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="bls.ppi.archived_final_demand",
        title="BLS archived Producer Price Index final-demand release facts",
        publisher="U.S. Bureau of Labor Statistics",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.bls.gov/bls/news-release/ppi.htm"
        ),
        allowed_hosts=("www.bls.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 HTML/PDF pairs sequentially; "
            "do not crawl or enumerate the archive. Use a contact-shaped research user agent."
        ),
        pagination_policy=(
            "Each selection uses one complete archived HTML release and one complete 31- or "
            "32-page PDF without API pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each release is retained as a versioned snapshot. BLS states that all PPI indexes "
            "are subject to revision four months after original publication. Adjacent-release "
            "prior values are cross-checked, and later values never overwrite earlier facts."
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
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified BLS PPI calendar")
        self.http = http
        self.release_date = release_date
        self.spec = _VERIFIED_RELEASES[release_date]
        stem = f"ppi_{release_date:%m%d%Y}"
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
                f"unexpected BLS PPI HTML content type: {html_content_type!r}"
            )
        pdf_content_type = pdf_response.headers.get("Content-Type", "").split(";", 1)[0]
        if pdf_content_type != "application/pdf":
            raise SourceSchemaError(
                f"unexpected BLS PPI PDF content type: {pdf_content_type!r}"
            )
        html_text, html_encoding = self._parse_html(html_content)
        pdf_text = self._parse_pdf(pdf_content)
        self._validate_release_text(html_text, source_kind="HTML")
        self._validate_release_text(pdf_text, source_kind="PDF")

        release_local = datetime.combine(self.release_date, time(8, 30), tzinfo=_NEW_YORK)
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError("BLS PPI release timezone does not match New York calendar")
        release_at = release_local.astimezone(UTC)
        if html_retrieved_at < release_at or pdf_retrieved_at < release_at:
            raise SourceSchemaError("selected BLS PPI release is not yet knowable")
        retrieved_at = max(html_retrieved_at, pdf_retrieved_at)

        html_digest = source_response_sha256(html_content)
        pdf_digest = source_response_sha256(pdf_content)
        source_version = (
            f"BLS-PPI:{self.spec.reference_month:%Y-%m}:{self.spec.release_number}:"
            f"html:{html_digest[:20]}:pdf:{pdf_digest[:20]}"
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
        previous_value = self._previous_release_same_reference_value()
        revision_delta = (
            None
            if previous_value is None
            else self.spec.prior_month_change_tenths_percent - previous_value
        )
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.spec.reference_month:%Y%m}:"
                "final_demand_monthly_change"
            ),
            entity_id="bls_ppi:final_demand_united_states",
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
                "release_series": "Producer Price Indexes",
                "metric": "final_demand_monthly_change_seasonally_adjusted",
                "value_tenths_percent": self.spec.monthly_change_tenths_percent,
                "value_basis_points": self.spec.monthly_change_tenths_percent * 10,
                "value_percent_text": _tenths_text(
                    self.spec.monthly_change_tenths_percent
                ),
                "prior_month": self.spec.prior_month.strftime("%Y-%m"),
                "prior_month_change_tenths_percent": (
                    self.spec.prior_month_change_tenths_percent
                ),
                "prior_month_value_in_previous_release_tenths_percent": previous_value,
                "prior_month_revision_delta_tenths_percent": revision_delta,
                "second_prior_month": self.spec.second_prior_month.strftime("%Y-%m"),
                "second_prior_month_change_tenths_percent": (
                    self.spec.second_prior_month_change_tenths_percent
                ),
                "year_over_year_change_tenths_percent": (
                    self.spec.year_over_year_change_tenths_percent
                ),
                "table1_current_unadjusted_index": (
                    self.spec.table_current_unadjusted_index
                ),
                "table1_unadjusted_monthly_change_tenths_percent": (
                    self.spec.table_unadjusted_monthly_change_tenths_percent
                ),
                "table1_seasonally_adjusted_change_sequence_tenths_percent": list(
                    self.spec.table_seasonally_adjusted_changes_tenths_percent
                ),
                "revision_window_months": 4,
                "ppi_measurement_boundary": (
                    "average change over time in prices received by domestic producers; "
                    "seller perspective"
                ),
                "covid_methodology_statement_present": (
                    self.spec.covid_methodology_marker is not None
                ),
                "covid_methodology_statement": self.spec.covid_methodology_marker,
                "release_time_local": "08:30:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "official_release_at": release_at.isoformat(),
                "snapshot_semantics": (
                    "headline final-demand monthly change reported in this archived release"
                ),
                "html_pdf_crosscheck_verified": True,
                "release_html_url": html_response.request_url,
                "release_html_sha256": html_digest,
                "release_html_encoding": html_encoding,
                "release_pdf_url": pdf_response.request_url,
                "release_pdf_sha256": pdf_digest,
                "release_pdf_pages": self.spec.page_count,
                "release_pdf_page_width_points": 612,
                "release_pdf_page_height_points": 792,
                "release_pdf_page_rotation_degrees": 0,
                "availability_method": "exact_bls_embargo_end_crosschecked_html_pdf",
                "unit": "Tenths of a Percent",
            },
        )
        warnings = (
            "The exact 8:30 a.m. EDT embargo end is stated in both archived formats and "
            "validated against America/New_York; retrieval headers are not historical timing.",
            "The full HTML and every nonblank PDF page are validated; release identity, headline, "
            "prior changes, Table 1 values, technical definition, and revision rule must agree.",
            "All PPI indexes are subject to revision four months after original publication; "
            "later releases never overwrite earlier snapshots.",
            "PPI measures prices received by domestic producers from the seller perspective; it "
            "is not CPI, a quantity, revenue, profit, transaction, or household-cost measure.",
            "Seasonal adjustment and the reported 12-month change are source facts, not a "
            "FinReplay probability, confidence interval, causal estimate, or forecast.",
            "The March and April COVID-19 text concerns pricing date, response rates, and "
            "estimation procedures; it does not establish causality or unaffected measurement.",
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
            raise SourceSchemaError("BLS PPI HTML is not structurally valid") from error
        text = _normalize(" ".join(parser.parts))
        if not text:
            raise SourceSchemaError("BLS PPI HTML has no visible text")
        return text, encoding

    def _parse_pdf(self, content: bytes) -> str:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("BLS PPI release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != self.spec.page_count:
                raise SourceSchemaError("BLS PPI PDF page count does not match")
            pages = []
            for page in reader.pages:
                geometry = (
                    round(float(page.mediabox.width), 2),
                    round(float(page.mediabox.height), 2),
                )
                if geometry != (612.0, 792.0) or page.rotation != 0:
                    raise SourceSchemaError("BLS PPI PDF page geometry does not match")
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError("BLS PPI PDF has a blank text layer")
                pages.append(_normalize(extracted))
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("BLS PPI release PDF could not be parsed") from error
        if self.spec.title_marker not in pages[0]:
            raise SourceSchemaError("BLS PPI PDF first-page identity does not match")
        if "Technical Note Brief Explanation of Producer Price Indexes" not in pages[
            self.spec.technical_page_index
        ]:
            raise SourceSchemaError("BLS PPI PDF technical-note page does not match")
        if "Table 1. Producer price indexes and percent changes for final demand" not in pages[
            self.spec.table1_page_index
        ]:
            raise SourceSchemaError("BLS PPI PDF Table 1 page does not match")
        if "Table 1. Producer price indexes and percent changes for final demand" not in pages[
            self.spec.table1_page_index + 1
        ]:
            raise SourceSchemaError("BLS PPI PDF Table 1 continuation does not match")
        if "Table 7. Producer price indexes for selected final demand" not in pages[-2]:
            raise SourceSchemaError("BLS PPI PDF Table 7 page does not match")
        if "Table 8. Producer price indexes for selected commodity groupings" not in pages[-1]:
            raise SourceSchemaError("BLS PPI PDF Table 8 page does not match")
        return _normalize(" ".join(pages))

    def _validate_release_text(self, text: str, *, source_kind: str) -> None:
        time_marker = (
            "Transmission of material in this release is embargoed until "
            f"{self.spec.release_number} 8:30 a.m. ({self.spec.timezone_abbreviation}), "
            f"{calendar.day_name[self.release_date.weekday()]}, "
            f"{self.release_date:%B} {self.release_date.day}, {self.release_date:%Y}"
        )
        unique_markers = (
            time_marker,
            self.spec.title_marker,
            self.spec.headline_marker,
            self.spec.prior_changes_marker,
            _TECHNICAL_DEFINITION,
        )
        if any(text.count(marker) != 1 for marker in unique_markers):
            raise SourceSchemaError(f"BLS PPI {source_kind} identity or headline does not match")
        if self.spec.year_over_year_marker not in text:
            raise SourceSchemaError(f"BLS PPI {source_kind} 12-month fact does not match")
        if text.count(_REVISION_MARKER) < 7:
            raise SourceSchemaError(f"BLS PPI {source_kind} revision rule does not match")
        table_text = _normalize(_DOT_LEADERS.sub(" ", text))
        if table_text.count(self.spec.table_row_marker) != 1:
            raise SourceSchemaError(f"BLS PPI {source_kind} Table 1 values do not match")
        for marker in _COVID_MARKERS:
            expected = marker == self.spec.covid_methodology_marker
            if (marker in text) is not expected:
                raise SourceSchemaError(
                    f"BLS PPI {source_kind} COVID-19 methodology statement does not match"
                )

    def _previous_release_same_reference_value(self) -> int | None:
        prior = next(
            (
                candidate
                for candidate in _VERIFIED_RELEASES.values()
                if candidate.reference_month == self.spec.prior_month
            ),
            None,
        )
        if prior is None:
            return None
        if prior.monthly_change_tenths_percent != self.spec.prior_month_change_tenths_percent:
            raise SourceSchemaError("BLS PPI adjacent releases do not preserve the prior value")
        return prior.monthly_change_tenths_percent

    def _validate_response_url(self, response_url: str, *, kind: str) -> None:
        if kind not in {"html", "pdf"}:
            raise ValueError("BLS PPI response kind must be html or pdf")
        suffix = "htm" if kind == "html" else "pdf"
        expected_path = f"/news.release/archives/ppi_{self.release_date:%m%d%Y}.{suffix}"
        parsed = urlparse(response_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("BLS PPI response URL does not match request")


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
                "BLS PPI HTML is neither valid UTF-8 nor Windows-1252"
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
