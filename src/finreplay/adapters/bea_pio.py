"""Archived BEA Personal Income and Outlays release adapter."""

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
    release_number: str
    timezone_abbreviation: str
    slug: str
    pdf_path: str
    pdf_pages: int
    saving_rate_percent: str
    personal_saving_trillion_dollars: str
    prior_month_rate_basis_points: int
    prior_month_previous_release_basis_points: int | None
    personal_income_change_percent: str
    disposable_income_change_percent: str
    pce_change_percent: str
    real_pce_change_percent: str
    personal_income_amount_billion_dollars: str
    disposable_income_amount_billion_dollars: str
    pce_amount_billion_dollars: str
    direction: str
    monthly_table_row: str

    @property
    def reference_label(self) -> str:
        return self.reference_month.strftime("%B %Y")


_VERIFIED_RELEASES = {
    date(2020, 2, 28): _ReleaseSpec(
        release_date=date(2020, 2, 28),
        reference_month=date(2020, 1, 1),
        release_number="BEA 20-08",
        timezone_abbreviation="EST",
        slug="personal-income-and-outlays-january-2020",
        pdf_path="/sites/default/files/2020-02/pi0120_0.pdf",
        pdf_pages=11,
        saving_rate_percent="7.9",
        personal_saving_trillion_dollars="1.33",
        prior_month_rate_basis_points=750,
        prior_month_previous_release_basis_points=None,
        personal_income_change_percent="0.6",
        disposable_income_change_percent="0.6",
        pce_change_percent="0.2",
        real_pce_change_percent="0.1",
        personal_income_amount_billion_dollars="116.5",
        disposable_income_amount_billion_dollars="101.4",
        pce_amount_billion_dollars="29.6",
        direction="increased",
        monthly_table_row="7.8 7.4 7.7 7.8 7.7 7.8 7.5 7.9 44",
    ),
    date(2020, 3, 27): _ReleaseSpec(
        release_date=date(2020, 3, 27),
        reference_month=date(2020, 2, 1),
        release_number="BEA 20-14",
        timezone_abbreviation="EDT",
        slug="personal-income-and-outlays-february-2020",
        pdf_path="/sites/default/files/2020-03/pi0220_1.pdf",
        pdf_pages=11,
        saving_rate_percent="8.2",
        personal_saving_trillion_dollars="1.38",
        prior_month_rate_basis_points=790,
        prior_month_previous_release_basis_points=790,
        personal_income_change_percent="0.6",
        disposable_income_change_percent="0.5",
        pce_change_percent="0.2",
        real_pce_change_percent="0.1",
        personal_income_amount_billion_dollars="106.8",
        disposable_income_amount_billion_dollars="88.7",
        pce_amount_billion_dollars="27.7",
        direction="increased",
        monthly_table_row="7.4 7.7 7.8 7.6 7.7 7.5 7.9 8.2 44",
    ),
    date(2020, 4, 30): _ReleaseSpec(
        release_date=date(2020, 4, 30),
        reference_month=date(2020, 3, 1),
        release_number="BEA 20-20",
        timezone_abbreviation="EDT",
        slug="personal-income-and-outlays-march-2020",
        pdf_path="/sites/default/files/2020-04/pi0320_0_0.pdf",
        pdf_pages=12,
        saving_rate_percent="13.1",
        personal_saving_trillion_dollars="2.17",
        prior_month_rate_basis_points=800,
        prior_month_previous_release_basis_points=820,
        personal_income_change_percent="-2.0",
        disposable_income_change_percent="-2.0",
        pce_change_percent="-7.5",
        real_pce_change_percent="-7.3",
        personal_income_amount_billion_dollars="382.1",
        disposable_income_amount_billion_dollars="334.6",
        pce_amount_billion_dollars="1,127.3",
        direction="decreased",
        monthly_table_row="7.7 7.8 7.6 7.7 7.5 7.7 8.0 13.1 44",
    ),
}


class _ArticleParser(HTMLParser):
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


class BEAPersonalIncomeOutlaysArchiveAdapter:
    """Retrieve one fixed 2020 BEA release as paired archived HTML and PDF."""

    availability_rule = (
        "Each selected BEA archived release page and paired full-release PDF state the same "
        "8:30 a.m. EST/EDT embargo end on a named date. FinReplay validates the timezone "
        "abbreviation against America/New_York and makes the release eligible at that exact "
        "embargo end. Current server Last-Modified headers are not used to backdate the bytes."
    )
    metadata = AdapterMetadata(
        adapter_id="bea.pio.archived_personal_saving_rate",
        title="BEA archived Personal Income and Outlays saving-rate releases",
        publisher="U.S. Bureau of Economic Analysis",
        documentation_url=_HTTP_URL_ADAPTER.validate_python("https://www.bea.gov/news/archive"),
        allowed_hosts=("www.bea.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 HTML/PDF release pairs "
            "sequentially; do not crawl or enumerate the BEA archive."
        ),
        pagination_policy=(
            "Each selection uses one complete archived HTML release and one complete fixed-page "
            "full-release PDF without pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each dated HTML/PDF pair is retained as a versioned release snapshot. The April "
            "release's revision of February's saving rate from 8.2 to 8.0 percent remains only "
            "in that later snapshot and never overwrites the March decision input."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Full BEA HTML and PDF releases remain in local content-addressed storage. The "
            "repository retains only minimal reported facts, URLs, hashes, attribution, and "
            "release-snapshot semantics; no redistribution right is inferred here."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified BEA PIO calendar")
        self.http = http
        self.spec = _VERIFIED_RELEASES[release_date]
        self.release_date = release_date
        self.html_endpoint = f"https://www.bea.gov/news/2020/{self.spec.slug}"
        self.pdf_endpoint = f"https://www.bea.gov{self.spec.pdf_path}"

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
            raise SourceSchemaError(f"unexpected BEA PIO HTML content type: {html_content_type!r}")
        pdf_content_type = pdf_response.headers.get("Content-Type", "").split(";", 1)[0]
        if pdf_content_type != "application/pdf":
            raise SourceSchemaError(f"unexpected BEA PIO PDF content type: {pdf_content_type!r}")
        html_text = self._parse_html(html_content)
        pdf_text = self._parse_pdf(pdf_content)
        self._crosscheck(html_text, pdf_text)

        release_local = datetime.combine(
            self.release_date,
            time(8, 30),
            tzinfo=_NEW_YORK,
        )
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError("BEA PIO release timezone does not match New York calendar")
        release_at = release_local.astimezone(UTC)
        retrieved_at = max(html_retrieved_at, pdf_retrieved_at)
        if retrieved_at < release_at:
            raise SourceSchemaError("selected BEA PIO release is not yet knowable")
        html_digest = source_response_sha256(html_content)
        pdf_digest = source_response_sha256(pdf_content)
        compact_release_number = self.spec.release_number.replace(" ", "-")
        source_version = (
            f"BEA-PIO:{self.spec.reference_month:%Y-%m}:{compact_release_number}:"
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
        value_basis_points = _rate_basis_points(self.spec.saving_rate_percent)
        previous = self.spec.prior_month_previous_release_basis_points
        revision_delta = (
            None if previous is None else self.spec.prior_month_rate_basis_points - previous
        )
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.spec.reference_month:%Y%m}:personal_saving_rate"
            ),
            entity_id="bea_pio:united_states",
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
                "metric": "personal_saving_rate",
                "value_basis_points": value_basis_points,
                "reported_saving_rate_percent": self.spec.saving_rate_percent,
                "personal_saving_trillion_dollars": (self.spec.personal_saving_trillion_dollars),
                "prior_month_rate_in_current_release_basis_points": (
                    self.spec.prior_month_rate_basis_points
                ),
                "prior_month_rate_in_previous_release_basis_points": previous,
                "prior_month_revision_delta_basis_points": revision_delta,
                "personal_income_monthly_change_percent": (
                    self.spec.personal_income_change_percent
                ),
                "disposable_income_monthly_change_percent": (
                    self.spec.disposable_income_change_percent
                ),
                "pce_monthly_change_percent": self.spec.pce_change_percent,
                "real_pce_monthly_change_percent": self.spec.real_pce_change_percent,
                "release_time_local": "08:30:00",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "release_timezone": "America/New_York",
                "official_release_at": release_at.isoformat(),
                "unit": "Basis Points",
                "snapshot_semantics": "headline value reported in this archived release",
                "html_pdf_crosscheck_verified": True,
                "table1_snapshot_verified": True,
                "release_html_url": html_response.request_url,
                "release_html_sha256": html_digest,
                "release_pdf_url": pdf_response.request_url,
                "release_pdf_sha256": pdf_digest,
                "release_pdf_pages": self.spec.pdf_pages,
                "availability_method": "explicit_embargo_end_in_both_html_and_pdf",
            },
        )
        warnings = (
            "The exact 8:30 a.m. EST/EDT embargo end is present in both paired forms and is "
            "validated against America/New_York.",
            "The April release revises February's saving rate to 8.0 percent; the March release "
            "snapshot remains 8.2 percent and is never overwritten.",
            "The March release says the full economic effects of the pandemic cannot be "
            "quantified because impacts are embedded in source data.",
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
            raise SourceSchemaError("BEA PIO page is not an HTML document")
        try:
            decoded = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("BEA PIO page is not valid UTF-8") from error
        marker = f'<article about="/news/2020/{self.spec.slug}">'
        if decoded.count(marker) != 1 or decoded.count("</article>") != 1:
            raise SourceSchemaError("BEA PIO page must contain one matching release article")
        article = decoded.split(marker, 1)[1].split("</article>", 1)[0]
        parser = _ArticleParser()
        try:
            parser.feed(article)
            parser.close()
        except ValueError as error:
            raise SourceSchemaError("BEA PIO release article could not be parsed") from error
        text_value = _normalize_text(" ".join(parser.text_parts))
        expected_embargo = self._embargo_marker().upper()
        markers = (
            self.spec.release_number,
            self._html_title(),
            *self._common_markers(),
        )
        if text_value.upper().count(expected_embargo) != 1:
            raise SourceSchemaError("BEA PIO HTML embargo identity does not match")
        if any(marker not in text_value for marker in markers):
            raise SourceSchemaError("BEA PIO HTML identity or headline values do not match")
        if parser.links.count(self.spec.pdf_path) != 1:
            raise SourceSchemaError("BEA PIO HTML full-release PDF link does not match")
        return text_value

    def _parse_pdf(self, content: bytes) -> str:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("BEA PIO full release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != self.spec.pdf_pages:
                raise SourceSchemaError("BEA PIO full release page count does not match")
            extracted_pages = []
            for page in reader.pages:
                if float(page.mediabox.width) != 612 or float(page.mediabox.height) != 792:
                    raise SourceSchemaError("BEA PIO full release must use US Letter pages")
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError("BEA PIO full release has a blank text layer")
                extracted_pages.append(extracted)
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("BEA PIO full release PDF could not be parsed") from error
        text_value = _normalize_text(" ".join(extracted_pages))
        markers = (
            self._embargo_marker().upper(),
            self.spec.release_number,
            f"Personal Income and Outlays: {self.spec.reference_label}",
            *self._common_markers(),
            (
                "Personal saving as a percentage of disposable personal income "
                f"{self.spec.monthly_table_row}"
            ),
            "Table 1. Personal Income and Its Disposition (Months)",
        )
        upper_text = text_value.upper()
        if upper_text.count(markers[0]) != 1:
            raise SourceSchemaError("BEA PIO PDF embargo identity does not match")
        if any(marker not in text_value for marker in markers[1:]):
            raise SourceSchemaError("BEA PIO PDF identity, headline, or Table 1 does not match")
        return text_value

    def _crosscheck(self, html_text: str, pdf_text: str) -> None:
        markers = self._common_markers()
        if any(marker not in html_text or marker not in pdf_text for marker in markers):
            raise SourceSchemaError("BEA PIO HTML and PDF values do not cross-check")
        if self.release_date == date(2020, 4, 30) and (
            "full economic effects of the COVID-19 pandemic cannot be quantified" not in html_text
            or "full economic effects of the COVID-19 pandemic cannot be quantified" not in pdf_text
        ):
            raise SourceSchemaError("BEA PIO March release limitation is missing")

    def _common_markers(self) -> tuple[str, ...]:
        spec = self.spec
        direction = spec.direction
        amount_direction = direction
        headline = (
            f"Personal income {amount_direction} ${spec.personal_income_amount_billion_dollars} "
            f"billion ({spec.personal_income_change_percent.lstrip('-')} percent) in "
            f"{spec.reference_month:%B}"
        )
        disposable = (
            f"Disposable personal income (DPI) {direction} "
            f"${spec.disposable_income_amount_billion_dollars} billion "
            f"({spec.disposable_income_change_percent.lstrip('-')} percent)"
        )
        pce = (
            f"personal consumption expenditures (PCE) {direction} "
            f"${spec.pce_amount_billion_dollars} billion "
            f"({spec.pce_change_percent.lstrip('-')} percent)"
        )
        saving = f"Personal saving was ${spec.personal_saving_trillion_dollars} trillion"
        rate = f"was {spec.saving_rate_percent} percent (table 1)."
        return (headline, disposable, pce, saving, rate)

    def _embargo_marker(self) -> str:
        spec = self.spec
        return (
            "EMBARGOED UNTIL RELEASE AT 8:30 A.M. "
            f"{spec.timezone_abbreviation}, {spec.release_date:%A, %B %d, %Y}"
        )

    def _html_title(self) -> str:
        separator = "," if self.release_date == date(2020, 2, 28) else ":"
        return f"Personal Income and Outlays{separator} {self.spec.reference_label}"

    def _validate_response_url(self, response_url: str, *, kind: str) -> None:
        parsed = urlparse(response_url)
        expected_path = f"/news/2020/{self.spec.slug}" if kind == "html" else self.spec.pdf_path
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError(f"BEA PIO {kind.upper()} response URL does not match request")


def _normalize_text(value: str) -> str:
    normalized = " ".join(
        value.replace("\u2014", "-").replace("\u2013", "-").replace("\xa0", " ").split()
    )
    return normalized.replace("P ersonal", "Personal").replace("R eal", "Real")


def _rate_basis_points(value: str) -> int:
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise SourceSchemaError("BEA PIO saving rate is not decimal") from error
    basis_points = decimal * 100
    if not decimal.is_finite() or basis_points != basis_points.to_integral_value():
        raise SourceSchemaError("BEA PIO saving rate does not map to whole basis points")
    result = int(basis_points)
    if not 0 <= result <= 100_000:
        raise SourceSchemaError("BEA PIO saving rate is outside the supported range")
    return result
