"""Archived Census/HUD New Residential Construction release adapter."""

from __future__ import annotations

import calendar
import re
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
_PAGE_DIMENSIONS = ((612.0, 792.0),) * 7
_PAGE_TITLES = (
    None,
    "EXPLANATORY NOTES",
    "New Privately-Owned Housing Units Authorized in Permit-Issuing Places",
    "New Privately-Owned Housing Units Authorized, but Not Started, at End of Period",
    "New Privately-Owned Housing Units Started",
    "New Privately-Owned Housing Units Under Construction at End of Period",
    "New Privately-Owned Housing Units Completed",
)


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    reference_month: date
    release_number: str
    timezone_abbreviation: str
    starts_units: int
    monthly_change_percent: str
    monthly_margin_90_percent: str
    monthly_ci_includes_zero: bool
    year_over_year_change_percent: str
    year_over_year_margin_90_percent: str
    prior_month: date
    prior_month_revised_starts_units: int
    previous_release_same_reference_starts_units: int | None
    single_family_starts_units: int
    single_family_monthly_change_percent: str
    single_family_monthly_margin_90_percent: str
    five_plus_starts_units: int
    average_rse_percent: int
    average_preliminary_revision_leq_percent: str
    covid_publication_statement: bool = False

    @property
    def reference_label(self) -> str:
        return self.reference_month.strftime("%B %Y")

    @property
    def prior_label(self) -> str:
        return self.prior_month.strftime("%B")

    @property
    def starts_thousand_units(self) -> int:
        return self.starts_units // 1_000

    @property
    def prior_month_revised_starts_thousand_units(self) -> int:
        return self.prior_month_revised_starts_units // 1_000


_VERIFIED_RELEASES = {
    date(2020, 2, 19): _ReleaseSpec(
        release_date=date(2020, 2, 19),
        reference_month=date(2020, 1, 1),
        release_number="CB20-26",
        timezone_abbreviation="EST",
        starts_units=1_567_000,
        monthly_change_percent="-3.6",
        monthly_margin_90_percent="13.3",
        monthly_ci_includes_zero=True,
        year_over_year_change_percent="21.4",
        year_over_year_margin_90_percent="12.2",
        prior_month=date(2019, 12, 1),
        prior_month_revised_starts_units=1_626_000,
        previous_release_same_reference_starts_units=None,
        single_family_starts_units=1_010_000,
        single_family_monthly_change_percent="-5.9",
        single_family_monthly_margin_90_percent="11.6",
        five_plus_starts_units=547_000,
        average_rse_percent=5,
        average_preliminary_revision_leq_percent="2.3",
    ),
    date(2020, 3, 18): _ReleaseSpec(
        release_date=date(2020, 3, 18),
        reference_month=date(2020, 2, 1),
        release_number="CB20-41",
        timezone_abbreviation="EDT",
        starts_units=1_599_000,
        monthly_change_percent="-1.5",
        monthly_margin_90_percent="12.4",
        monthly_ci_includes_zero=True,
        year_over_year_change_percent="39.2",
        year_over_year_margin_90_percent="17.7",
        prior_month=date(2020, 1, 1),
        prior_month_revised_starts_units=1_624_000,
        previous_release_same_reference_starts_units=1_567_000,
        single_family_starts_units=1_072_000,
        single_family_monthly_change_percent="6.7",
        single_family_monthly_margin_90_percent="13.9",
        five_plus_starts_units=508_000,
        average_rse_percent=5,
        average_preliminary_revision_leq_percent="2.1",
    ),
    date(2020, 4, 16): _ReleaseSpec(
        release_date=date(2020, 4, 16),
        reference_month=date(2020, 3, 1),
        release_number="CB20-61",
        timezone_abbreviation="EDT",
        starts_units=1_216_000,
        monthly_change_percent="-22.3",
        monthly_margin_90_percent="12.2",
        monthly_ci_includes_zero=False,
        year_over_year_change_percent="1.4",
        year_over_year_margin_90_percent="12.7",
        prior_month=date(2020, 2, 1),
        prior_month_revised_starts_units=1_564_000,
        previous_release_same_reference_starts_units=1_599_000,
        single_family_starts_units=856_000,
        single_family_monthly_change_percent="-17.5",
        single_family_monthly_margin_90_percent="13.1",
        five_plus_starts_units=347_000,
        average_rse_percent=6,
        average_preliminary_revision_leq_percent="2.1",
        covid_publication_statement=True,
    ),
}


class CensusHUDNRCArchiveAdapter:
    """Retrieve one explicitly approved Census/HUD NRC release PDF."""

    availability_rule = (
        "Each selected Census/HUD New Residential Construction PDF states an exact 8:30 a.m. "
        "EST/EDT release date and time. FinReplay validates the timezone abbreviation against "
        "America/New_York and makes the release snapshot eligible at that exact stated time. "
        "Current HTTP headers are retrieval metadata only and are not backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="census.hud.archived_new_residential_construction",
        title="Census/HUD archived New Residential Construction releases",
        publisher="U.S. Census Bureau and U.S. Department of Housing and Urban Development",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.census.gov/construction/nrc/data/releases.html"
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
            "Each PDF is retained as a versioned release snapshot. The March release's "
            "revision of January from 1,567,000 to 1,624,000 and the April release's revision "
            "of February from 1,599,000 to 1,564,000 remain only in their later snapshots; "
            "earlier preliminary values are never overwritten."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Full Census/HUD PDFs remain in local content-addressed storage. The repository "
            "retains only minimal reported facts, URLs, hashes, attribution, and release-"
            "snapshot semantics; no redistribution right is inferred."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified Census/HUD NRC calendar")
        self.http = http
        self.release_date = release_date
        self.spec = _VERIFIED_RELEASES[release_date]
        self.endpoint = (
            "https://www.census.gov/construction/nrc/pdf/"
            f"newresconst_{self.spec.reference_month:%Y%m}.pdf"
        )

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/pdf":
            raise SourceSchemaError(f"unexpected Census/HUD NRC content type: {content_type!r}")
        self._parse_pdf(content)
        release_local = datetime.combine(
            self.release_date,
            time(8, 30),
            tzinfo=_NEW_YORK,
        )
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError("Census/HUD NRC timezone does not match New York calendar")
        release_at = release_local.astimezone(UTC)
        if retrieved_at < release_at:
            raise SourceSchemaError("selected Census/HUD NRC release is not yet knowable")
        digest = source_response_sha256(content)
        source_version = (
            f"CENSUS-HUD-NRC:{self.spec.reference_month:%Y-%m}:{self.spec.release_number}:"
            f"pdf:{digest[:24]}"
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
        previous = self.spec.previous_release_same_reference_starts_units
        revision_delta = (
            None
            if previous is None
            else self.spec.prior_month_revised_starts_units - previous
        )
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.spec.reference_month:%Y%m}:"
                "total_housing_starts"
            ),
            entity_id="census_hud_nrc:privately_owned_housing_starts_total",
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
                "release_series": "Monthly New Residential Construction",
                "metric": "privately_owned_total_housing_starts_sa_annual_rate",
                "value_units": self.spec.starts_units,
                "value_thousand_units": self.spec.starts_thousand_units,
                "reported_monthly_change_percent": self.spec.monthly_change_percent,
                "reported_monthly_margin_90_percent": (
                    self.spec.monthly_margin_90_percent
                ),
                "reported_monthly_ci_includes_zero": self.spec.monthly_ci_includes_zero,
                "reported_monthly_change_significant_at_90_percent": (
                    not self.spec.monthly_ci_includes_zero
                ),
                "reported_year_over_year_change_percent": (
                    self.spec.year_over_year_change_percent
                ),
                "reported_year_over_year_margin_90_percent": (
                    self.spec.year_over_year_margin_90_percent
                ),
                "prior_month": self.spec.prior_month.strftime("%Y-%m"),
                "prior_month_revised_value_units": (
                    self.spec.prior_month_revised_starts_units
                ),
                "prior_month_revised_value_thousand_units": (
                    self.spec.prior_month_revised_starts_thousand_units
                ),
                "prior_month_value_in_previous_release_units": previous,
                "prior_month_revision_delta_units": revision_delta,
                "single_family_starts_units": self.spec.single_family_starts_units,
                "single_family_monthly_change_percent": (
                    self.spec.single_family_monthly_change_percent
                ),
                "single_family_monthly_margin_90_percent": (
                    self.spec.single_family_monthly_margin_90_percent
                ),
                "five_units_or_more_starts_units": self.spec.five_plus_starts_units,
                "table3_average_rse_percent": self.spec.average_rse_percent,
                "reported_average_preliminary_revision_leq_percent": (
                    self.spec.average_preliminary_revision_leq_percent
                ),
                "release_time_local": "08:30:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "official_release_at": release_at.isoformat(),
                "covid_publication_standard_statement_present": (
                    self.spec.covid_publication_statement
                ),
                "unit": "Housing Units at Seasonally Adjusted Annual Rate",
                "snapshot_semantics": "preliminary headline value in this archived release",
                "pdf_table_snapshot_verified": True,
                "release_pdf_url": response.request_url,
                "release_pdf_sha256": digest,
                "release_pdf_pages": 7,
                "availability_method": "exact_time_in_pdf",
            },
        )
        warnings = (
            "The exact 8:30 a.m. EST/EDT release time is stated in the PDF and validated "
            "against America/New_York; current HTTP headers are not historical timing evidence.",
            "Headline and Table 3a total housing-start values, changes, sampling margins, and "
            "revision bridges are cross-checked within each full seven-page PDF.",
            "Census/HUD 90-percent sampling confidence intervals are official release metadata, "
            "not FinReplay forecast ranges or downstream probability statements.",
            "The January and February headline values are preliminary snapshots; later releases' "
            "revisions remain separate and never overwrite them.",
            "The releases warn that total starts may require six months to establish a trend and "
            "are subject to sampling and nonsampling error.",
            "The April release's COVID-19 publication-standard statement is reported source text; "
            "no pandemic causality or unaffected-measurement claim is inferred.",
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
            raise SourceSchemaError("Census/HUD NRC release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 7:
                raise SourceSchemaError("Census/HUD NRC release must contain exactly seven pages")
            pages = []
            dimensions = []
            for page in reader.pages:
                dimensions.append(
                    (round(float(page.mediabox.width), 2), round(float(page.mediabox.height), 2))
                )
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError("Census/HUD NRC release has a blank text layer")
                pages.append(_normalize(extracted))
            if tuple(dimensions) != _PAGE_DIMENSIONS:
                raise SourceSchemaError("Census/HUD NRC release page dimensions do not match")
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("Census/HUD NRC release PDF could not be parsed") from error
        for index, title in enumerate(_PAGE_TITLES):
            if title is not None and title not in pages[index]:
                raise SourceSchemaError("Census/HUD NRC page identity does not match")
        self._validate_first_page(pages[0])
        self._validate_explanatory_notes(pages[1])
        self._validate_table3a(pages[4])

    def _validate_first_page(self, first: str) -> None:
        time_marker = (
            f"FOR RELEASE AT 8:30 AM {self.spec.timezone_abbreviation}, "
            f"{calendar.day_name[self.release_date.weekday()].upper()}, "
            f"{self.release_date:%B %d, %Y}".upper()
        )
        year_over_year_star = (
            "*"
            if _interval_contains_zero(
                self.spec.year_over_year_change_percent,
                self.spec.year_over_year_margin_90_percent,
            )
            else ""
        )
        identity_markers = (
            time_marker,
            f"MONTHLY NEW RESIDENTIAL CONSTRUCTION, {self.spec.reference_label.upper()}",
            f"Release Number: {self.spec.release_number}",
            f"Housing Starts: {self.spec.starts_units:,}",
            (
                f"Privately-owned housing starts in {self.spec.reference_month:%B} were at a "
                f"seasonally adjusted annual rate of {self.spec.starts_units:,}."
            ),
            (
                f"{_direction_phrase(self.spec.monthly_change_percent)}"
                f"{self.spec.monthly_margin_90_percent} percent)"
                f"{'*' if self.spec.monthly_ci_includes_zero else ''} "
                f"below the revised {self.spec.prior_label} estimate of "
                f"{self.spec.prior_month_revised_starts_units:,}"
            ),
            (
                f"{self.spec.year_over_year_change_percent} percent "
                f"(±{self.spec.year_over_year_margin_90_percent} percent)"
                f"{year_over_year_star} "
                f"above the {self.spec.reference_month:%B} 2019 rate"
            ),
            (
                f"Single-family housing starts in {self.spec.reference_month:%B} were at a rate "
                f"of {self.spec.single_family_starts_units:,}"
            ),
            f"buildings with five units or more was {self.spec.five_plus_starts_units:,}",
        )
        if any(marker not in first for marker in identity_markers):
            raise SourceSchemaError("Census/HUD NRC headline or release identity does not match")
        number_marker = f"Release Number: {self.spec.release_number}"
        if first.count(time_marker) != 1 or first.count(number_marker) != 1:
            raise SourceSchemaError("Census/HUD NRC release identity is not unique")
        covid_marker = "determined estimates in this release meet publication standards"
        if (covid_marker in first) != self.spec.covid_publication_statement:
            raise SourceSchemaError("Census/HUD NRC COVID-19 statement does not match calendar")

    def _validate_explanatory_notes(self, notes: str) -> None:
        required = (
            "six months for total starts",
            "subject to sampling variability as well as nonsampling error",
            "All ranges given for percentage changes are 90 percent confidence intervals",
            "account only for sampling variability",
            (
                "preliminary seasonally adjusted estimates of total building permits, housing "
                "starts and housing completions are revised "
                f"{self.spec.average_preliminary_revision_leq_percent} "
                "percent or less"
            ),
            (
                "The 90 percent confidence interval includes zero. In such cases, there is "
                "insufficient statistical evidence to conclude that the actual change is "
                "different from zero."
            ),
        )
        if any(marker not in notes for marker in required):
            raise SourceSchemaError("Census/HUD NRC explanatory notes do not match")

    def _validate_table3a(self, table: str) -> None:
        reference_row = re.compile(
            rf"{self.spec.reference_month:%B}\s*\(p\)(?:\s*\.)+\s*"
            rf"{self.spec.starts_thousand_units:,}\b"
        )
        prior_row = re.compile(
            rf"{self.spec.prior_month:%B}\s*\(r\)(?:\s*\.)+\s*"
            rf"{self.spec.prior_month_revised_starts_thousand_units:,}\b"
        )
        rse_row = re.compile(
            rf"Average RSE \(%\)\s*1(?:\s*\.)+\s*{self.spec.average_rse_percent}\b"
        )
        change_label = self.spec.reference_month.strftime("%b.")
        prior_label = self.spec.prior_month.strftime("%b.")
        change_row = re.compile(
            rf"{change_label} 2020 from {prior_label} "
            rf"(?:2019|2020)(?:\s*\.)+\s*{re.escape(self.spec.monthly_change_percent)}\s*%"
        )
        margin_row = re.compile(
            rf"90 percent confidence interval\s*3(?:\s*\.)+\s*±\s*"
            rf"{re.escape(self.spec.monthly_margin_90_percent)}\b"
        )
        if not all(
            pattern.search(table)
            for pattern in (reference_row, prior_row, rse_row, change_row, margin_row)
        ):
            raise SourceSchemaError("Census/HUD NRC Table 3a values do not cross-check")

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        expected_path = (
            "/construction/nrc/pdf/"
            f"newresconst_{self.spec.reference_month:%Y%m}.pdf"
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("Census/HUD NRC response URL does not match request")


def _normalize(value: str) -> str:
    return " ".join(
        value.replace("\u00a0", " ")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2212", "-")
        .split()
    )


def _direction_phrase(change_percent: str) -> str:
    value = _percent_decimal(change_percent)
    if value >= 0:
        raise SourceSchemaError(
            "Census/HUD NRC verified headline change must be negative for a below statement"
        )
    return f"This is {abs(value):.1f} percent (±"


def _interval_contains_zero(change_percent: str, margin_percent: str) -> bool:
    return abs(_percent_decimal(change_percent)) <= _percent_decimal(margin_percent)


def _percent_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise SourceSchemaError("Census/HUD NRC percentage is not numeric") from error
    if not parsed.is_finite():
        raise SourceSchemaError("Census/HUD NRC percentage must be finite")
    return parsed
