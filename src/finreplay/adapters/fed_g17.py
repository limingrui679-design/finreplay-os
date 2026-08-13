"""Archived Federal Reserve G.17 industrial-production release adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
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


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    reference_month: date
    timezone_abbreviation: str
    monthly_change_percent: str
    prior_month_change_percent: str
    prior_month_previous_release_percent: str
    total_index: str
    capacity_utilization_percent: str
    manufacturing_change_percent: str
    mining_change_percent: str
    utilities_change_percent: str
    year_over_year_change_percent: str
    headline_marker: str
    capacity_marker: str
    manufacturing_marker: str
    mining_marker: str
    utilities_marker: str
    table_total_row: str
    table_previous_row: str

    @property
    def reference_label(self) -> str:
        return self.reference_month.strftime("%B")


_VERIFIED_RELEASES = {
    date(2020, 2, 14): _ReleaseSpec(
        release_date=date(2020, 2, 14),
        reference_month=date(2020, 1, 1),
        timezone_abbreviation="EST",
        monthly_change_percent="-0.3",
        prior_month_change_percent="-0.4",
        prior_month_previous_release_percent="-0.3",
        total_index="109.2",
        capacity_utilization_percent="76.8",
        manufacturing_change_percent="-0.1",
        mining_change_percent="1.2",
        utilities_change_percent="-4.0",
        year_over_year_change_percent="-0.8",
        headline_marker="Industrial production declined 0.3 percent in January",
        capacity_marker=(
            "Capacity utilization for the industrial sector fell 0.3 percentage point in "
            "January to 76.8 percent"
        ),
        manufacturing_marker="Manufacturing output decreased 0.1 percent in January",
        mining_marker="Mining output advanced 1.2 percent in January",
        utilities_marker="output of utilities fell 4.0 percent in January",
        table_total_row=(
            "Total index 109.9 109.5 109.0 110.0 109.5 109.2 .7 -.3 -.4 .9 -.4 -.3 -.8"
        ),
        table_previous_row="Previous estimates 110.0 109.4 108.9 109.8 109.4 .8 -.5 -.5 .8 -.3",
    ),
    date(2020, 3, 17): _ReleaseSpec(
        release_date=date(2020, 3, 17),
        reference_month=date(2020, 2, 1),
        timezone_abbreviation="EDT",
        monthly_change_percent="0.6",
        prior_month_change_percent="-0.5",
        prior_month_previous_release_percent="-0.3",
        total_index="109.6",
        capacity_utilization_percent="77.0",
        manufacturing_change_percent="0.1",
        mining_change_percent="-1.5",
        utilities_change_percent="7.1",
        year_over_year_change_percent="0.0",
        headline_marker=(
            "Industrial production rose 0.6 percent in February after falling 0.5 percent in "
            "January"
        ),
        capacity_marker=(
            "Capacity utilization for the industrial sector increased 0.4 percentage point in "
            "February to 77.0 percent"
        ),
        manufacturing_marker="Manufacturing output edged up 0.1 percent in February",
        mining_marker="Mining output fell 1.5 percent in February",
        utilities_marker="output of utilities jumped 7.1 percent in February",
        table_total_row=(
            "Total index 109.5 109.0 110.0 109.6 109.0 109.6 -.3 -.4 .9 -.4 -.5 .6 .0"
        ),
        table_previous_row=("Previous estimates 109.5 109.0 110.0 109.5 109.2 -.3 -.4 .9 -.4 -.3"),
    ),
    date(2020, 4, 15): _ReleaseSpec(
        release_date=date(2020, 4, 15),
        reference_month=date(2020, 3, 1),
        timezone_abbreviation="EDT",
        monthly_change_percent="-5.4",
        prior_month_change_percent="0.5",
        prior_month_previous_release_percent="0.6",
        total_index="103.7",
        capacity_utilization_percent="72.7",
        manufacturing_change_percent="-6.3",
        mining_change_percent="-2.0",
        utilities_change_percent="-3.9",
        year_over_year_change_percent="-5.5",
        headline_marker="Total industrial production fell 5.4 percent in March",
        capacity_marker=(
            "Capacity utilization for the industrial sector decreased 4.3 percentage points "
            "to 72.7 percent in March"
        ),
        manufacturing_marker="Manufacturing output dropped 6.3 percent in March",
        mining_marker="Mining output fell 2.0 percent, with the largest decreases",
        utilities_marker="output of utilities declined 3.9 percent in March",
        table_total_row=(
            "Total index 109.0 110.1 109.6 109.1 109.6 103.7 -.4 .9 -.4 -.5 .5 -5.4 -5.5"
        ),
        table_previous_row=("Previous estimates 109.0 110.0 109.6 109.0 109.6 -.4 .9 -.4 -.5 .6"),
    ),
}


class _ReleasePageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[str] = []

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


class FederalReserveG17ArchiveAdapter:
    """Retrieve one fixed 2020 G.17 release as paired archived HTML and PDF."""

    availability_rule = (
        "Each selected Federal Reserve G.17 PDF states an exact 9:15 a.m. EST/EDT release time "
        "and date; its paired archived HTML confirms the same release date and facts. FinReplay "
        "validates the timezone abbreviation against America/New_York and makes the pair "
        "eligible at that exact PDF-stated time. Current server headers are not backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="federalreserve.g17.archived_industrial_production",
        title="Federal Reserve archived G.17 industrial-production releases",
        publisher="Board of Governors of the Federal Reserve System",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.federalreserve.gov/releases/g17/release_dates.htm"
        ),
        allowed_hosts=("www.federalreserve.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 HTML/PDF release pairs "
            "sequentially; do not crawl or enumerate the G.17 archive."
        ),
        pagination_policy=(
            "Each selection uses one complete archived HTML release and one complete 19-page "
            "statistical-release PDF without pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each dated HTML/PDF pair is retained as a versioned release snapshot. The March "
            "release's revision of January from -0.3 to -0.5 percent and the April release's "
            "revision of February from 0.6 to 0.5 percent remain only in their later snapshots."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Full Federal Reserve HTML and PDF releases remain in local content-addressed "
            "storage. The repository retains only minimal reported facts, URLs, hashes, "
            "attribution, and release-snapshot semantics; no redistribution right is inferred."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified Federal Reserve G.17 calendar")
        self.http = http
        self.spec = _VERIFIED_RELEASES[release_date]
        self.release_date = release_date
        release_path = f"/releases/g17/{release_date:%Y%m%d}"
        self.html_endpoint = f"https://www.federalreserve.gov{release_path}/default.htm"
        self.pdf_endpoint = f"https://www.federalreserve.gov{release_path}/g17.pdf"

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
        if html_content_type != "text/html":
            raise SourceSchemaError(f"unexpected G.17 HTML content type: {html_content_type!r}")
        pdf_content_type = pdf_response.headers.get("Content-Type", "").split(";", 1)[0]
        if pdf_content_type != "application/pdf":
            raise SourceSchemaError(f"unexpected G.17 PDF content type: {pdf_content_type!r}")
        html_text = self._parse_html(html_content)
        pdf_text = self._parse_pdf(pdf_content)
        self._crosscheck(html_text, pdf_text)

        release_local = datetime.combine(
            self.release_date,
            time(9, 15),
            tzinfo=_NEW_YORK,
        )
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError("G.17 release timezone does not match New York calendar")
        release_at = release_local.astimezone(UTC)
        retrieved_at = max(html_retrieved_at, pdf_retrieved_at)
        if retrieved_at < release_at:
            raise SourceSchemaError("selected G.17 release is not yet knowable")
        html_digest = source_response_sha256(html_content)
        html_fact_digest = self._html_fact_sha256()
        pdf_digest = source_response_sha256(pdf_content)
        source_version = (
            f"FED-G17:{self.spec.reference_month:%Y-%m}:G17-419:"
            f"html-facts:{html_fact_digest[:20]}:pdf:{pdf_digest[:20]}"
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
            entity_id="federal_reserve_g17:total_industrial_production",
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
                "release_series": "G.17 (419)",
                "metric": "total_industrial_production_monthly_change",
                "value_basis_points": value_basis_points,
                "reported_monthly_change_percent": self.spec.monthly_change_percent,
                "total_index_2012_equals_100": self.spec.total_index,
                "capacity_utilization_percent": self.spec.capacity_utilization_percent,
                "manufacturing_monthly_change_percent": self.spec.manufacturing_change_percent,
                "mining_monthly_change_percent": self.spec.mining_change_percent,
                "utilities_monthly_change_percent": self.spec.utilities_change_percent,
                "year_over_year_change_percent": self.spec.year_over_year_change_percent,
                "prior_month_change_in_current_release_basis_points": prior_current,
                "prior_month_change_in_previous_release_basis_points": prior_previous,
                "prior_month_revision_delta_basis_points": prior_current - prior_previous,
                "release_time_local": "09:15:00",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "release_timezone": "America/New_York",
                "official_release_at": release_at.isoformat(),
                "unit": "Basis Points",
                "snapshot_semantics": "headline value reported in this archived release",
                "html_pdf_crosscheck_verified": True,
                "summary_table_snapshot_verified": True,
                "release_html_url": html_response.request_url,
                "release_html_fact_sha256": html_fact_digest,
                "release_pdf_url": pdf_response.request_url,
                "release_pdf_sha256": pdf_digest,
                "release_pdf_pages": 19,
                "availability_method": "exact_time_in_pdf_and_release_date_in_both_forms",
            },
        )
        warnings = (
            "The exact 9:15 a.m. EST/EDT release time is stated in the PDF and validated "
            "against America/New_York; the paired HTML confirms the release date and facts.",
            "The official HTML response can contain changing Cloudflare shell tokens. Each "
            "receipt retains its raw-response hash, while the normalized release-fact hash and "
            "canonical PDF hash keep the economic snapshot stable.",
            "The March release revises January from -0.3 to -0.5 percent, and the April release "
            "revises February from 0.6 to 0.5 percent; earlier snapshots are never overwritten.",
            "The April release describes special late-month estimation inputs; this adapter "
            "does not infer pandemic, policy, industry, or household causality.",
            "Only the explicitly verified January, February, and March 2020 reference months "
            "are supported.",
            "Full archived HTML and PDF files remain local download evidence.",
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
        artifacts = (
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
        )
        return AdapterBatch(records=(record,), receipts=receipts, artifacts=artifacts)

    def _parse_html(self, content: bytes) -> str:
        if not content.lstrip().lower().startswith(b"<!doctype html"):
            raise SourceSchemaError("G.17 release page is not an HTML document")
        try:
            decoded = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("G.17 release page is not valid UTF-8") from error
        if decoded.lower().count("<html") != 1 or decoded.lower().count("</html>") != 1:
            raise SourceSchemaError("G.17 release page must contain one HTML document")
        parser = _ReleasePageParser()
        try:
            parser.feed(decoded)
            parser.close()
        except ValueError as error:
            raise SourceSchemaError("G.17 release page could not be parsed") from error
        text_value = _normalize_text(" ".join(parser.text_parts))
        release_marker = f"Release Date: {self.release_date:%B %d, %Y}"
        update_marker = f"Last Update: {self.release_date:%B %d, %Y}"
        if text_value.count(release_marker) != 1 or text_value.count(update_marker) != 1:
            raise SourceSchemaError("G.17 HTML release date identity does not match")
        if parser.links.count("g17.pdf") != 1:
            raise SourceSchemaError("G.17 HTML PDF link does not match")
        if "Industrial Production and Capacity Utilization - G.17" not in text_value:
            raise SourceSchemaError("G.17 HTML release identity does not match")
        compact = _compact_for_match(text_value)
        if any(_compact_for_match(marker) not in compact for marker in self._common_markers()):
            raise SourceSchemaError("G.17 HTML headline or summary-table values do not match")
        return text_value

    def _parse_pdf(self, content: bytes) -> str:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("G.17 statistical release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 19:
                raise SourceSchemaError("G.17 statistical release page count does not match")
            extracted_pages = []
            for page in reader.pages:
                if float(page.mediabox.width) != 612 or float(page.mediabox.height) != 792:
                    raise SourceSchemaError("G.17 statistical release must use US Letter pages")
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError("G.17 statistical release has a blank text layer")
                extracted_pages.append(extracted)
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("G.17 statistical release PDF could not be parsed") from error
        first_page = _normalize_text(extracted_pages[0])
        text_value = _normalize_text(" ".join(extracted_pages))
        time_marker = (
            f"For release at 9:15 a.m. ({self.spec.timezone_abbreviation}) "
            f"{self.release_date:%B %d, %Y}"
        )
        compact_first = _compact_for_match(first_page)
        if compact_first.count(_compact_for_match(time_marker)) != 1:
            raise SourceSchemaError("G.17 PDF release-time identity does not match")
        identity_markers = (
            "G.17 (419)",
            "Industrial Production and Capacity Utilization",
        )
        if any(_compact_for_match(marker) not in compact_first for marker in identity_markers):
            raise SourceSchemaError("G.17 PDF release identity does not match")
        compact = _compact_for_match(text_value)
        if any(_compact_for_match(marker) not in compact for marker in self._common_markers()):
            raise SourceSchemaError("G.17 PDF headline or summary-table values do not match")
        return text_value

    def _crosscheck(self, html_text: str, pdf_text: str) -> None:
        html_compact = _compact_for_match(html_text)
        pdf_compact = _compact_for_match(pdf_text)
        if any(
            _compact_for_match(marker) not in html_compact
            or _compact_for_match(marker) not in pdf_compact
            for marker in self._common_markers()
        ):
            raise SourceSchemaError("G.17 HTML and PDF values do not cross-check")
        if self.release_date == date(2020, 4, 15) and (
            _compact_for_match("incorporated data on stay-at-home orders") not in html_compact
            or _compact_for_match("incorporated data on stay-at-home orders") not in pdf_compact
        ):
            raise SourceSchemaError("G.17 March release estimation limitation is missing")

    def _common_markers(self) -> tuple[str, ...]:
        return (
            self.spec.headline_marker,
            f"At {self.spec.total_index} percent of its 2012 average",
            self.spec.capacity_marker,
            self.spec.manufacturing_marker,
            self.spec.mining_marker,
            self.spec.utilities_marker,
            self.spec.table_total_row,
            self.spec.table_previous_row,
        )

    def _html_fact_sha256(self) -> str:
        canonical = "\n".join(
            (
                self.release_date.isoformat(),
                self.spec.reference_month.isoformat(),
                "09:15:00",
                self.spec.timezone_abbreviation,
                self.html_endpoint,
                self.pdf_endpoint,
                *self._common_markers(),
            )
        ).encode()
        return source_response_sha256(canonical)

    def _validate_response_url(self, response_url: str, *, kind: str) -> None:
        parsed = urlparse(response_url)
        filename = "default.htm" if kind == "html" else "g17.pdf"
        expected_path = f"/releases/g17/{self.release_date:%Y%m%d}/{filename}"
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError(f"G.17 {kind.upper()} response URL does not match request")


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
        character for character in normalized if character.isalnum() or character in ".-"
    )


def _percent_basis_points(value: str) -> int:
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise SourceSchemaError("G.17 monthly change is not decimal") from error
    basis_points = decimal * 100
    if not decimal.is_finite() or basis_points != basis_points.to_integral_value():
        raise SourceSchemaError("G.17 monthly change does not map to whole basis points")
    result = int(basis_points)
    if not -100_000 <= result <= 100_000:
        raise SourceSchemaError("G.17 monthly change is outside the supported range")
    return result
