"""Archived Federal Reserve G.19 consumer-credit release adapter."""

# ruff: noqa: E501  # Exact official table-row markers intentionally remain unwrapped.

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
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
_NOTES_MARKER = (
    "Starting with the April 2020 G.19 Consumer Credit release, scheduled to be published "
    "on June 5, 2020, the release will no longer report the levels and flows of on-book "
    "loan balances and off-book securitized loan balances as separate line items."
)
_SIMPLE_RATE_MARKER = (
    "percent changes are at a simple annual rate and are calculated from unrounded data"
)
_LEGEND_MARKER = "r=revised. p=preliminary. n.a.=not available. ...=not applicable."


@dataclass(frozen=True, slots=True)
class _MonthFact:
    reference_month: date
    status_marker: str
    total_change_percent: str
    revolving_change_percent: str
    nonrevolving_change_percent: str
    total_flow_billion: str
    revolving_flow_billion: str
    nonrevolving_flow_billion: str
    total_outstanding_billion: str
    revolving_outstanding_billion: str
    nonrevolving_outstanding_billion: str
    previous_revolving_change_percent: str | None = None

    @property
    def estimate_status(self) -> str:
        return "preliminary" if self.status_marker == "p" else "revised"


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    headline_month: date
    timezone_abbreviation: str
    headline_marker: str
    table_header_marker: str
    table_rows: tuple[str, ...]
    facts: tuple[_MonthFact, ...]

    @property
    def headline_label(self) -> str:
        return self.headline_month.strftime("%B %Y")


_VERIFIED_RELEASES = {
    date(2020, 3, 6): _ReleaseSpec(
        release_date=date(2020, 3, 6),
        headline_month=date(2020, 1, 1),
        timezone_abbreviation="EST",
        headline_marker=(
            "In January, consumer credit increased at a seasonally adjusted annual rate of "
            "3-1/2 percent. Revolving credit decreased at an annual rate of 3-1/4 percent, "
            "while nonrevolving credit increased at an annual rate of 5-3/4 percent."
        ),
        table_header_marker="Novr Decr Janp",
        table_rows=(
            "Total percent change (annual rate)2 7.1 6.8 5.1 4.7 4.5 5.4 4.3 4.2 4.9 4.5 2.7 5.8 3.4",
            "Revolving 5.4 6.8 5.6 3.1 3.8 5.0 1.5 5.2 3.6 4.6 -5.0 12.2 -3.3",
            "Nonrevolving3 7.7 6.9 4.9 5.4 4.8 5.6 5.3 3.8 5.3 4.4 5.5 3.6 5.8",
            "Total flow (annual rate)2,4 233.8 233.1 184.0 181.8 181.0 214.7 171.2 168.5 199.7 184.8 114.2 243.0 144.3",
            "Revolving 48.0 61.2 54.2 31.6 39.7 52.0 15.9 54.8 38.6 49.4 -54.3 132.3 -36.4",
            "Nonrevolving3 185.9 171.9 129.9 150.2 141.3 162.7 155.2 113.6 161.1 135.4 168.4 110.7 180.7",
            "Total outstanding 3,411.0 3,644.1 3,828.2 4,009.7 4,190.7 4,009.7 4,052.5 4,094.6 4,144.5 4,190.7 4,170.5 4,190.7 4,202.7",
            "Revolving 906.7 968.0 1,022.1 1,053.5 1,093.2 1,053.5 1,057.5 1,071.2 1,080.8 1,093.2 1,082.2 1,093.2 1,090.1",
            "Nonrevolving3 2,504.3 2,676.2 2,806.1 2,956.2 3,097.5 2,956.2 2,995.0 3,023.4 3,063.7 3,097.5 3,088.3 3,097.5 3,112.6",
        ),
        facts=(
            _MonthFact(
                reference_month=date(2020, 1, 1),
                status_marker="p",
                total_change_percent="3.4",
                revolving_change_percent="-3.3",
                nonrevolving_change_percent="5.8",
                total_flow_billion="144.3",
                revolving_flow_billion="-36.4",
                nonrevolving_flow_billion="180.7",
                total_outstanding_billion="4202.7",
                revolving_outstanding_billion="1090.1",
                nonrevolving_outstanding_billion="3112.6",
            ),
        ),
    ),
    date(2020, 4, 7): _ReleaseSpec(
        release_date=date(2020, 4, 7),
        headline_month=date(2020, 2, 1),
        timezone_abbreviation="EDT",
        headline_marker=(
            "In February, consumer credit increased at a seasonally adjusted annual rate of "
            "6-1/2 percent. Revolving credit increased at an annual rate of 4-1/2 percent, "
            "while nonrevolving credit increased at an annual rate of 7 percent."
        ),
        table_header_marker="Decr Janr Febp",
        table_rows=(
            "Total percent change (annual rate)2 7.1 6.8 5.1 4.8 4.5 5.1 4.6 4.3 4.8 4.0 6.0 3.5 6.4",
            "Revolving 5.5 6.8 5.6 3.1 3.8 4.1 2.5 4.6 4.3 3.4 12.6 -2.7 4.6",
            "Nonrevolving3 7.6 6.9 4.9 5.3 4.8 5.5 5.3 4.2 5.0 4.2 3.7 5.6 7.0",
            "Total flow (annual rate)2,4 233.7 233.1 185.6 182.0 180.4 202.4 182.7 174.8 197.0 167.2 252.0 144.7 268.0",
            "Revolving 48.7 61.3 54.4 32.0 39.7 42.8 26.3 49.0 46.2 37.3 136.9 -29.4 50.4",
            "Nonrevolving3 185.0 171.8 131.2 150.0 140.7 159.6 156.4 125.8 150.8 129.9 115.0 174.2 217.6",
            "Total outstanding 3,410.3 3,643.4 3,829.0 4,010.7 4,191.1 4,010.7 4,056.4 4,100.1 4,149.3 4,191.1 4,191.1 4,203.2 4,225.5",
            "Revolving 907.2 968.5 1,022.9 1,054.6 1,094.3 1,054.6 1,061.2 1,073.5 1,085.0 1,094.3 1,094.3 1,091.9 1,096.1",
            "Nonrevolving3 2,503.1 2,674.9 2,806.1 2,956.1 3,096.8 2,956.1 2,995.1 3,026.6 3,064.3 3,096.8 3,096.8 3,111.3 3,129.4",
        ),
        facts=(
            _MonthFact(
                reference_month=date(2020, 1, 1),
                status_marker="r",
                total_change_percent="3.5",
                revolving_change_percent="-2.7",
                nonrevolving_change_percent="5.6",
                total_flow_billion="144.7",
                revolving_flow_billion="-29.4",
                nonrevolving_flow_billion="174.2",
                total_outstanding_billion="4203.2",
                revolving_outstanding_billion="1091.9",
                nonrevolving_outstanding_billion="3111.3",
                previous_revolving_change_percent="-3.3",
            ),
            _MonthFact(
                reference_month=date(2020, 2, 1),
                status_marker="p",
                total_change_percent="6.4",
                revolving_change_percent="4.6",
                nonrevolving_change_percent="7.0",
                total_flow_billion="268.0",
                revolving_flow_billion="50.4",
                nonrevolving_flow_billion="217.6",
                total_outstanding_billion="4225.5",
                revolving_outstanding_billion="1096.1",
                nonrevolving_outstanding_billion="3129.4",
            ),
        ),
    ),
    date(2020, 5, 7): _ReleaseSpec(
        release_date=date(2020, 5, 7),
        headline_month=date(2020, 3, 1),
        timezone_abbreviation="EDT",
        headline_marker=(
            "Consumer credit increased at a seasonally adjusted annual rate of 1-3/4 percent "
            "during the first quarter. Revolving credit decreased at an annual rate of "
            "10-1/4 percent, while nonrevolving credit increased at an annual rate of 6 "
            "percent. In March, revolving credit decreased at an annual rate of 31 percent, "
            "while nonrevolving credit increased at an annual rate of 6-1/4 percent."
        ),
        table_header_marker="Janr Febr Marp",
        table_rows=(
            "Total percent change (annual rate)2 7.1 6.8 5.1 4.8 4.5 4.5 4.3 4.8 4.0 1.7 3.0 5.7 -3.4",
            "Revolving 5.5 6.8 5.6 3.1 3.8 2.5 4.6 4.3 3.5 -10.3 -3.7 3.6 -30.9",
            "Nonrevolving3 7.6 6.9 4.9 5.3 4.8 5.3 4.2 5.0 4.2 6.0 5.3 6.4 6.2",
            "Total flow (annual rate)2,4 233.7 233.1 185.6 182.0 180.4 182.5 174.8 197.1 167.4 72.8 123.8 239.0 -144.5",
            "Revolving 48.7 61.3 54.4 32.0 39.7 26.1 49.0 46.3 37.5 -113.0 -40.5 39.4 -338.1",
            "Nonrevolving3 185.0 171.8 131.2 150.0 140.7 156.4 125.8 150.8 129.9 185.8 164.3 199.6 193.6",
            "Total outstanding 3,410.3 3,643.4 3,829.0 4,010.7 4,191.1 4,056.3 4,100.0 4,149.3 4,191.1 4,209.3 4,201.4 4,221.4 4,209.3",
            "Revolving 907.2 968.5 1,022.9 1,054.6 1,094.3 1,061.2 1,073.4 1,085.0 1,094.3 1,066.1 1,091.0 1,094.3 1,066.1",
            "Nonrevolving3 2,503.1 2,674.9 2,806.1 2,956.1 3,096.8 2,995.1 3,026.6 3,064.3 3,096.8 3,143.2 3,110.5 3,127.1 3,143.2",
        ),
        facts=(
            _MonthFact(
                reference_month=date(2020, 1, 1),
                status_marker="r",
                total_change_percent="3.0",
                revolving_change_percent="-3.7",
                nonrevolving_change_percent="5.3",
                total_flow_billion="123.8",
                revolving_flow_billion="-40.5",
                nonrevolving_flow_billion="164.3",
                total_outstanding_billion="4201.4",
                revolving_outstanding_billion="1091.0",
                nonrevolving_outstanding_billion="3110.5",
                previous_revolving_change_percent="-2.7",
            ),
            _MonthFact(
                reference_month=date(2020, 2, 1),
                status_marker="r",
                total_change_percent="5.7",
                revolving_change_percent="3.6",
                nonrevolving_change_percent="6.4",
                total_flow_billion="239.0",
                revolving_flow_billion="39.4",
                nonrevolving_flow_billion="199.6",
                total_outstanding_billion="4221.4",
                revolving_outstanding_billion="1094.3",
                nonrevolving_outstanding_billion="3127.1",
                previous_revolving_change_percent="4.6",
            ),
            _MonthFact(
                reference_month=date(2020, 3, 1),
                status_marker="p",
                total_change_percent="-3.4",
                revolving_change_percent="-30.9",
                nonrevolving_change_percent="6.2",
                total_flow_billion="-144.5",
                revolving_flow_billion="-338.1",
                nonrevolving_flow_billion="193.6",
                total_outstanding_billion="4209.3",
                revolving_outstanding_billion="1066.1",
                nonrevolving_outstanding_billion="3143.2",
            ),
        ),
    ),
}


class FederalReserveG19ArchiveAdapter:
    """Retrieve one explicitly approved Federal Reserve G.19 release PDF."""

    availability_rule = (
        "Each selected Federal Reserve G.19 PDF states an exact 3 p.m. Eastern Time release "
        "date and time. FinReplay resolves Eastern Time through America/New_York for that "
        "calendar date and makes the complete PDF snapshot eligible at that instant. Current "
        "HTTP headers are retrieval metadata only and are not backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="federalreserve.g19.archived_consumer_credit",
        title="Federal Reserve archived G.19 consumer-credit releases",
        publisher="Board of Governors of the Federal Reserve System",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.federalreserve.gov/releases/g19/"
        ),
        allowed_hosts=("www.federalreserve.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved March-May 2020 PDFs sequentially; "
            "do not crawl or enumerate the G.19 archive."
        ),
        pagination_policy="Each selected release is one complete four-page PDF.",
        availability_rule=availability_rule,
        revision_behavior=(
            "Each PDF is retained as a versioned release snapshot. The April release revises "
            "January revolving-credit growth from -3.3 to -2.7 percent. The May release "
            "revises January to -3.7 percent and February from 4.6 to 3.6 percent. Every "
            "version remains separate; no later estimate overwrites an earlier decision view."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Full Federal Reserve PDFs remain in local content-addressed storage. The "
            "repository retains only minimal reported facts, URLs, hashes, attribution, and "
            "release-snapshot semantics; no redistribution right is inferred."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified Federal Reserve G.19 calendar")
        self.http = http
        self.release_date = release_date
        self.spec = _VERIFIED_RELEASES[release_date]
        self.endpoint = f"https://www.federalreserve.gov/releases/g19/{release_date:%Y%m%d}/g19.pdf"

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/pdf":
            raise SourceSchemaError(f"unexpected G.19 PDF content type: {content_type!r}")
        self._validate_spec_rows()
        self._parse_pdf(content)

        release_local = datetime.combine(self.release_date, time(15, 0), tzinfo=_NEW_YORK)
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError("G.19 release timezone does not match New York calendar")
        release_at = release_local.astimezone(UTC)
        if retrieved_at < release_at:
            raise SourceSchemaError("selected G.19 release is not yet knowable")
        digest = source_response_sha256(content)
        source_version = f"FED-G19:{self.spec.headline_month:%Y-%m}:pdf:{digest[:24]}"
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
        records = tuple(
            self._record(fact, source=source, release_at=release_at) for fact in self.spec.facts
        )
        warnings = (
            "The PDF states 3 p.m. Eastern Time; the EST/EDT offset is resolved against "
            "America/New_York for the stated release date. Current HTTP headers are not "
            "historical timing evidence.",
            "G.19 percent changes are simple annual rates calculated from unrounded data. "
            "FinReplay retains the one-decimal table values rather than replacing them with "
            "rounded fractional wording from the headline.",
            "The April and May snapshots revise earlier monthly values. Each version is "
            "stored separately and later revisions never overwrite a prior decision view.",
            "G.19 revolving credit includes most credit-card loans but also other revolving "
            "plans; this adapter does not equate the series with card spending or infer "
            "household, policy, or pandemic causality.",
            "The selected release notes describe a June 2020 presentation change for on-book "
            "and off-book balances; no cross-method continuity claim is inferred.",
            "Only the explicitly verified January, February, and March 2020 reference-month "
            "snapshots in the three selected PDFs are supported.",
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
            record_count=len(records),
            source_version=source_version,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            historical_replay_eligible=True,
            warnings=warnings,
        )
        artifact = RawArtifact(sha256=digest, content_type=content_type, content=content)
        return AdapterBatch(records=records, receipts=(receipt,), artifacts=(artifact,))

    def _record(
        self,
        fact: _MonthFact,
        *,
        source: SourceReference,
        release_at: datetime,
    ) -> BitemporalRecord:
        value_basis_points = _percent_basis_points(fact.revolving_change_percent)
        previous_basis_points = (
            None
            if fact.previous_revolving_change_percent is None
            else _percent_basis_points(fact.previous_revolving_change_percent)
        )
        revision_delta = (
            None if previous_basis_points is None else value_basis_points - previous_basis_points
        )
        return BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{fact.reference_month:%Y%m}:"
                "revolving_percent_change_annual_rate"
            ),
            entity_id="federal_reserve_g19:revolving_consumer_credit",
            source=source,
            interval=BitemporalInterval(
                valid_from=datetime.combine(fact.reference_month, time.min, tzinfo=UTC),
                published_at=release_at,
                available_at=release_at,
                revised_at=release_at if fact.status_marker == "r" else None,
                ingested_at=source.retrieved_at,
                availability_rule=self.availability_rule,
                availability_confidence=1.0,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload={
                "release_date": self.release_date.isoformat(),
                "release_reference_month": self.spec.headline_month.strftime("%Y-%m"),
                "reference_month": fact.reference_month.strftime("%Y-%m"),
                "release_series": "G.19 Consumer Credit",
                "metric": "revolving_consumer_credit_percent_change_annual_rate",
                "value_basis_points": value_basis_points,
                "reported_total_change_percent": fact.total_change_percent,
                "reported_revolving_change_percent": fact.revolving_change_percent,
                "reported_nonrevolving_change_percent": fact.nonrevolving_change_percent,
                "reported_total_flow_annual_rate_billion_dollars": fact.total_flow_billion,
                "reported_revolving_flow_annual_rate_billion_dollars": (
                    fact.revolving_flow_billion
                ),
                "reported_nonrevolving_flow_annual_rate_billion_dollars": (
                    fact.nonrevolving_flow_billion
                ),
                "reported_total_outstanding_billion_dollars": (fact.total_outstanding_billion),
                "reported_revolving_outstanding_billion_dollars": (
                    fact.revolving_outstanding_billion
                ),
                "reported_nonrevolving_outstanding_billion_dollars": (
                    fact.nonrevolving_outstanding_billion
                ),
                "total_flow_tenths_billion_dollars": _tenths_billion(fact.total_flow_billion),
                "revolving_flow_tenths_billion_dollars": _tenths_billion(
                    fact.revolving_flow_billion
                ),
                "nonrevolving_flow_tenths_billion_dollars": _tenths_billion(
                    fact.nonrevolving_flow_billion
                ),
                "total_outstanding_tenths_billion_dollars": _tenths_billion(
                    fact.total_outstanding_billion
                ),
                "revolving_outstanding_tenths_billion_dollars": _tenths_billion(
                    fact.revolving_outstanding_billion
                ),
                "nonrevolving_outstanding_tenths_billion_dollars": _tenths_billion(
                    fact.nonrevolving_outstanding_billion
                ),
                "estimate_status": fact.estimate_status,
                "status_marker": fact.status_marker,
                "previous_release_same_reference_revolving_change_basis_points": (
                    previous_basis_points
                ),
                "revision_delta_basis_points": revision_delta,
                "release_time_local": "15:00:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "official_release_at": release_at.isoformat(),
                "unit": "Basis Points",
                "snapshot_semantics": (
                    "monthly G.19 table value reported in this archived release"
                ),
                "simple_annual_rate_from_unrounded_data": True,
                "pdf_table_snapshot_verified": True,
                "release_pdf_url": str(source.url),
                "release_pdf_sha256": source.sha256,
                "release_pdf_pages": 4,
                "release_pdf_page_rotation_degrees": 90,
                "availability_method": "exact_local_time_and_date_stated_in_pdf",
            },
        )

    def _parse_pdf(self, content: bytes) -> None:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("G.19 statistical release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 4:
                raise SourceSchemaError("G.19 statistical release must contain exactly four pages")
            extracted_pages = []
            for page in reader.pages:
                dimensions = (float(page.mediabox.width), float(page.mediabox.height))
                if dimensions != (612.0, 792.0):
                    raise SourceSchemaError("G.19 statistical release page dimensions do not match")
                if page.rotation != 90:
                    raise SourceSchemaError("G.19 statistical release page rotation does not match")
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError("G.19 statistical release has a blank text layer")
                extracted_pages.append(_normalize_text(extracted))
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("G.19 statistical release PDF could not be parsed") from error

        first, second, third, fourth = extracted_pages
        time_marker = "For release at 3 p.m. (Eastern Time)"
        date_marker = f"{self.release_date:%B} {self.release_date.day}, {self.release_date:%Y}"
        identity_markers = ("G.19", "Consumer Credit", self.spec.headline_label)
        for page_name, page_text in (("cover", first), ("table", second)):
            compact = _compact_for_match(page_text)
            if compact.count(_compact_for_match(time_marker)) != 1:
                raise SourceSchemaError(f"G.19 {page_name} release-time identity does not match")
            if compact.count(_compact_for_match(date_marker)) != 1:
                raise SourceSchemaError(f"G.19 {page_name} release-date identity does not match")
            if any(_compact_for_match(marker) not in compact for marker in identity_markers):
                raise SourceSchemaError(f"G.19 {page_name} release identity does not match")
        if _compact_for_match(_NOTES_MARKER) not in _compact_for_match(first):
            raise SourceSchemaError("G.19 release notes do not match")
        second_compact = _compact_for_match(second)
        if _compact_for_match(self.spec.headline_marker) not in second_compact:
            raise SourceSchemaError("G.19 release headline does not match")
        table_markers = (self.spec.table_header_marker, *self.spec.table_rows)
        if any(_compact_for_match(marker) not in second_compact for marker in table_markers):
            raise SourceSchemaError("G.19 seasonally adjusted table values do not match")
        third_compact = _compact_for_match(third)
        if (
            _compact_for_match("Consumer Credit Outstanding (Levels)") not in third_compact
            or _compact_for_match("Non seasonally adjusted") not in third_compact
            or _compact_for_match(_SIMPLE_RATE_MARKER) not in third_compact
        ):
            raise SourceSchemaError("G.19 levels page identity or rate footnote does not match")
        fourth_compact = _compact_for_match(fourth)
        if (
            _compact_for_match("Consumer Credit Outstanding (Flows)") not in fourth_compact
            or _compact_for_match("Billions of dollars, annual rate") not in fourth_compact
            or _compact_for_match(_LEGEND_MARKER) not in fourth_compact
        ):
            raise SourceSchemaError("G.19 flows page identity or estimate legend does not match")

    def _validate_spec_rows(self) -> None:
        attributes = (
            "total_change_percent",
            "revolving_change_percent",
            "nonrevolving_change_percent",
            "total_flow_billion",
            "revolving_flow_billion",
            "nonrevolving_flow_billion",
            "total_outstanding_billion",
            "revolving_outstanding_billion",
            "nonrevolving_outstanding_billion",
        )
        for row, attribute in zip(self.spec.table_rows, attributes, strict=True):
            expected_tail = " ".join(getattr(fact, attribute) for fact in self.spec.facts)
            normalized_row = _normalize_numeric_row(row)
            if not normalized_row.endswith(expected_tail):
                raise SourceSchemaError("G.19 configured table facts do not match verified rows")
        expected_status = " ".join(
            f"{fact.reference_month:%b}{fact.status_marker}" for fact in self.spec.facts
        ).lower()
        compact_header = "".join(self.spec.table_header_marker.lower().split())
        if not compact_header.endswith("".join(expected_status.split())):
            raise SourceSchemaError("G.19 configured estimate statuses do not match header")

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        expected_path = f"/releases/g19/{self.release_date:%Y%m%d}/g19.pdf"
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("G.19 PDF response URL does not match request")


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


def _normalize_numeric_row(value: str) -> str:
    return " ".join(value.replace(",", "").split())


def _percent_basis_points(value: str) -> int:
    return _scaled_decimal(value, scale=100, label="G.19 percent change")


def _tenths_billion(value: str) -> int:
    return _scaled_decimal(value, scale=10, label="G.19 billion-dollar value")


def _scaled_decimal(value: str, *, scale: int, label: str) -> int:
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise SourceSchemaError(f"{label} is not decimal") from error
    scaled = decimal * scale
    if not decimal.is_finite() or scaled != scaled.to_integral_value():
        raise SourceSchemaError(f"{label} does not map to the required precision")
    result = int(scaled)
    if not -100_000_000 <= result <= 100_000_000:
        raise SourceSchemaError(f"{label} is outside the supported range")
    return result
