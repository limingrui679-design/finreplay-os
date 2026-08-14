"""Archived Census/HUD New Residential Sales release adapter."""

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
_PAGE_DIMENSIONS = ((612.0, 792.0),) * 5
_PAGE_TITLES = (
    None,
    "EXPLANATORY NOTES",
    "New Privately-Owned Houses Sold and For Sale",
    "New Privately-Owned Houses Sold, by Sales Price",
    (
        "New Houses Sold and For Sale by Stage of Construction and Median Number of "
        "Months on Sales Market"
    ),
)


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    reference_month: date
    release_number: str
    timezone_abbreviation: str
    sold_units: int
    monthly_change_percent: str
    monthly_margin_90_percent: str
    year_over_year_change_percent: str
    year_over_year_margin_90_percent: str
    year_over_year_comparison_units: int
    prior_month: date
    prior_month_revised_sold_units: int
    previous_release_same_reference_sold_units: int | None
    houses_for_sale_units: int
    months_supply: str
    median_sales_price_usd: int
    average_sales_price_usd: int
    average_rse_percent: int
    average_preliminary_revision_percent: str
    covid_publication_statement: bool = False

    @property
    def reference_label(self) -> str:
        return self.reference_month.strftime("%B %Y")

    @property
    def prior_label(self) -> str:
        return self.prior_month.strftime("%B")

    @property
    def sold_thousand_units(self) -> int:
        return self.sold_units // 1_000

    @property
    def prior_month_revised_sold_thousand_units(self) -> int:
        return self.prior_month_revised_sold_units // 1_000

    @property
    def monthly_ci_includes_zero(self) -> bool:
        return _interval_contains_zero(
            self.monthly_change_percent,
            self.monthly_margin_90_percent,
        )

    @property
    def year_over_year_ci_includes_zero(self) -> bool:
        return _interval_contains_zero(
            self.year_over_year_change_percent,
            self.year_over_year_margin_90_percent,
        )


_VERIFIED_RELEASES = {
    date(2020, 2, 26): _ReleaseSpec(
        release_date=date(2020, 2, 26),
        reference_month=date(2020, 1, 1),
        release_number="CB20-28",
        timezone_abbreviation="EST",
        sold_units=764_000,
        monthly_change_percent="7.9",
        monthly_margin_90_percent="17.8",
        year_over_year_change_percent="18.6",
        year_over_year_margin_90_percent="19.2",
        year_over_year_comparison_units=644_000,
        prior_month=date(2019, 12, 1),
        prior_month_revised_sold_units=708_000,
        previous_release_same_reference_sold_units=None,
        houses_for_sale_units=324_000,
        months_supply="5.1",
        median_sales_price_usd=348_200,
        average_sales_price_usd=402_300,
        average_rse_percent=9,
        average_preliminary_revision_percent="4.2",
    ),
    date(2020, 3, 24): _ReleaseSpec(
        release_date=date(2020, 3, 24),
        reference_month=date(2020, 2, 1),
        release_number="CB20-49",
        timezone_abbreviation="EDT",
        sold_units=765_000,
        monthly_change_percent="-4.4",
        monthly_margin_90_percent="14.8",
        year_over_year_change_percent="14.3",
        year_over_year_margin_90_percent="17.5",
        year_over_year_comparison_units=669_000,
        prior_month=date(2020, 1, 1),
        prior_month_revised_sold_units=800_000,
        previous_release_same_reference_sold_units=764_000,
        houses_for_sale_units=319_000,
        months_supply="5.0",
        median_sales_price_usd=345_900,
        average_sales_price_usd=403_800,
        average_rse_percent=8,
        average_preliminary_revision_percent="4.6",
    ),
    date(2020, 4, 23): _ReleaseSpec(
        release_date=date(2020, 4, 23),
        reference_month=date(2020, 3, 1),
        release_number="CB20-62",
        timezone_abbreviation="EDT",
        sold_units=627_000,
        monthly_change_percent="-15.4",
        monthly_margin_90_percent="14.8",
        year_over_year_change_percent="-9.5",
        year_over_year_margin_90_percent="14.6",
        year_over_year_comparison_units=693_000,
        prior_month=date(2020, 2, 1),
        prior_month_revised_sold_units=741_000,
        previous_release_same_reference_sold_units=765_000,
        houses_for_sale_units=333_000,
        months_supply="6.4",
        median_sales_price_usd=321_400,
        average_sales_price_usd=375_300,
        average_rse_percent=8,
        average_preliminary_revision_percent="4.6",
        covid_publication_statement=True,
    ),
}


class CensusHUDNRSArchiveAdapter:
    """Retrieve one explicitly approved Census/HUD NRS release PDF."""

    availability_rule = (
        "Each selected Census/HUD New Residential Sales PDF states an exact 10:00 a.m. "
        "EST/EDT release date and time. FinReplay validates the timezone abbreviation against "
        "America/New_York and makes the release snapshot eligible at that exact stated time. "
        "Current HTTP headers are retrieval metadata only and are not backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="census.hud.archived_new_residential_sales",
        title="Census/HUD archived New Residential Sales releases",
        publisher="U.S. Census Bureau and U.S. Department of Housing and Urban Development",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.census.gov/construction/nrs/data/releases.html"
        ),
        allowed_hosts=("www.census.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 PDFs sequentially; do not crawl "
            "or enumerate the historical release archive."
        ),
        pagination_policy="Each selected release is one complete five-page PDF.",
        availability_rule=availability_rule,
        revision_behavior=(
            "Each PDF is retained as a versioned release snapshot. The March 24 release's "
            "revision of January from 764,000 to 800,000 and the April 23 release's revision "
            "of February from 765,000 to 741,000 remain only in their later snapshots; "
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
            raise ValueError("release date is not in the verified Census/HUD NRS calendar")
        self.http = http
        self.release_date = release_date
        self.spec = _VERIFIED_RELEASES[release_date]
        self.endpoint = (
            "https://www.census.gov/construction/nrs/pdf/"
            f"newressales_{self.spec.reference_month:%Y%m}.pdf"
        )

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/pdf":
            raise SourceSchemaError(f"unexpected Census/HUD NRS content type: {content_type!r}")
        self._parse_pdf(content)
        release_local = datetime.combine(
            self.release_date,
            time(10, 0),
            tzinfo=_NEW_YORK,
        )
        if release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError("Census/HUD NRS timezone does not match New York calendar")
        release_at = release_local.astimezone(UTC)
        if retrieved_at < release_at:
            raise SourceSchemaError("selected Census/HUD NRS release is not yet knowable")
        digest = source_response_sha256(content)
        source_version = (
            f"CENSUS-HUD-NRS:{self.spec.reference_month:%Y-%m}:{self.spec.release_number}:"
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
        previous = self.spec.previous_release_same_reference_sold_units
        revision_delta = (
            None if previous is None else self.spec.prior_month_revised_sold_units - previous
        )
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.spec.reference_month:%Y%m}:"
                "new_single_family_houses_sold"
            ),
            entity_id="census_hud_nrs:new_single_family_houses_sold_us",
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
                "release_series": "Monthly New Residential Sales",
                "metric": "new_single_family_houses_sold_sa_annual_rate",
                "value_units": self.spec.sold_units,
                "value_thousand_units": self.spec.sold_thousand_units,
                "reported_monthly_change_percent": self.spec.monthly_change_percent,
                "reported_monthly_margin_90_percent": (self.spec.monthly_margin_90_percent),
                "reported_monthly_ci_includes_zero": self.spec.monthly_ci_includes_zero,
                "reported_monthly_change_significant_at_90_percent": (
                    not self.spec.monthly_ci_includes_zero
                ),
                "reported_year_over_year_change_percent": (self.spec.year_over_year_change_percent),
                "reported_year_over_year_margin_90_percent": (
                    self.spec.year_over_year_margin_90_percent
                ),
                "reported_year_over_year_ci_includes_zero": (
                    self.spec.year_over_year_ci_includes_zero
                ),
                "year_over_year_comparison_value_units": (
                    self.spec.year_over_year_comparison_units
                ),
                "prior_month": self.spec.prior_month.strftime("%Y-%m"),
                "prior_month_revised_value_units": (self.spec.prior_month_revised_sold_units),
                "prior_month_revised_value_thousand_units": (
                    self.spec.prior_month_revised_sold_thousand_units
                ),
                "prior_month_value_in_previous_release_units": previous,
                "prior_month_revision_delta_units": revision_delta,
                "new_houses_for_sale_units": self.spec.houses_for_sale_units,
                "reported_months_supply": self.spec.months_supply,
                "median_sales_price_usd": self.spec.median_sales_price_usd,
                "average_sales_price_usd": self.spec.average_sales_price_usd,
                "table1a_average_rse_percent": self.spec.average_rse_percent,
                "reported_average_preliminary_revision_percent": (
                    self.spec.average_preliminary_revision_percent
                ),
                "release_time_local": "10:00:00",
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "official_release_at": release_at.isoformat(),
                "covid_publication_standard_statement_present": (
                    self.spec.covid_publication_statement
                ),
                "unit": "Houses at Seasonally Adjusted Annual Rate",
                "sale_definition_boundary": (
                    "deposit taken or sales agreement signed; may precede permit issuance"
                ),
                "snapshot_semantics": "preliminary headline value in this archived release",
                "pdf_table_snapshot_verified": True,
                "release_pdf_url": response.request_url,
                "release_pdf_sha256": digest,
                "release_pdf_pages": 5,
                "availability_method": "exact_time_in_pdf",
            },
        )
        warnings = (
            "The exact 10:00 a.m. EST/EDT release time is stated in the PDF and validated "
            "against America/New_York; current HTTP headers are not historical timing evidence.",
            "Headline and Table 1a national sales values, changes, sampling margins, and "
            "revision bridges are cross-checked within each full five-page PDF.",
            "Census/HUD 90-percent sampling confidence intervals are official release metadata, "
            "not FinReplay forecast ranges or downstream probability statements.",
            "The January and February headline values are preliminary snapshots; later releases' "
            "revisions remain separate and never overwrite them.",
            "The releases warn that new-house sales need four months to establish a trend and "
            "are subject to sampling and nonsampling error.",
            "A reported sale means a deposit was taken or a sales agreement was signed and may "
            "precede permit issuance; it is not necessarily a closing.",
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
            raise SourceSchemaError("Census/HUD NRS release is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 5:
                raise SourceSchemaError("Census/HUD NRS release must contain exactly five pages")
            pages = []
            dimensions = []
            for page in reader.pages:
                dimensions.append(
                    (round(float(page.mediabox.width), 2), round(float(page.mediabox.height), 2))
                )
                extracted = page.extract_text()
                if not isinstance(extracted, str) or not extracted.strip():
                    raise SourceSchemaError("Census/HUD NRS release has a blank text layer")
                pages.append(_normalize(extracted))
            if tuple(dimensions) != _PAGE_DIMENSIONS:
                raise SourceSchemaError("Census/HUD NRS release page dimensions do not match")
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("Census/HUD NRS release PDF could not be parsed") from error
        for index, title in enumerate(_PAGE_TITLES):
            if title is not None and title not in pages[index]:
                raise SourceSchemaError("Census/HUD NRS page identity does not match")
        self._validate_first_page(pages[0])
        self._validate_explanatory_notes(pages[1])
        self._validate_table1a(pages[2])

    def _validate_first_page(self, first: str) -> None:
        time_marker = (
            f"FOR RELEASE AT 10:00 AM {self.spec.timezone_abbreviation}, "
            f"{calendar.day_name[self.release_date.weekday()].upper()}, "
            f"{self.release_date:%B %d, %Y}".upper()
        )
        monthly_direction = _direction(self.spec.monthly_change_percent)
        year_over_year_direction = _direction(self.spec.year_over_year_change_percent)
        monthly_star = "*" if self.spec.monthly_ci_includes_zero else ""
        year_over_year_star = "*" if self.spec.year_over_year_ci_includes_zero else ""
        identity_markers = (
            time_marker,
            f"MONTHLY NEW RESIDENTIAL SALES, {self.spec.reference_label.upper()}",
            f"Release Number: {self.spec.release_number}",
            f"New Houses Sold1: {self.spec.sold_units:,}",
            (
                f"Sales of new single-family houses in {self.spec.reference_month:%B} 2020 "
                "were at a seasonally adjusted annual rate of "
                f"{self.spec.sold_units:,}"
            ),
            (
                f"{abs(_percent_decimal(self.spec.monthly_change_percent)):.1f} percent "
                f"(±{self.spec.monthly_margin_90_percent} percent){monthly_star} "
                f"{monthly_direction} the revised {self.spec.prior_label} rate of "
                f"{self.spec.prior_month_revised_sold_units:,}"
            ),
            (
                f"{abs(_percent_decimal(self.spec.year_over_year_change_percent)):.1f} percent "
                f"(±{self.spec.year_over_year_margin_90_percent} percent){year_over_year_star} "
                f"{year_over_year_direction} the {self.spec.reference_month:%B} 2019 estimate "
                f"of {self.spec.year_over_year_comparison_units:,}"
            ),
            (
                "The median sales price of new houses sold in "
                f"{self.spec.reference_month:%B} 2020 was "
                f"${self.spec.median_sales_price_usd:,}"
            ),
            f"The average sales price was ${self.spec.average_sales_price_usd:,}",
            (
                "new houses for sale at the end of "
                f"{self.spec.reference_month:%B} was {self.spec.houses_for_sale_units:,}"
            ),
            f"supply of {self.spec.months_supply} months at the current sales rate",
        )
        if any(marker not in first for marker in identity_markers):
            raise SourceSchemaError("Census/HUD NRS headline or release identity does not match")
        number_marker = f"Release Number: {self.spec.release_number}"
        if first.count(time_marker) != 1 or first.count(number_marker) != 1:
            raise SourceSchemaError("Census/HUD NRS release identity is not unique")
        covid_marker = "determined estimates in this release meet publication standards"
        if (covid_marker in first) != self.spec.covid_publication_statement:
            raise SourceSchemaError("Census/HUD NRS COVID-19 statement does not match calendar")

    def _validate_explanatory_notes(self, notes: str) -> None:
        required = (
            "subject to sampling variability as well as nonsampling error",
            "All ranges given for percent changes are 90-percent confidence intervals",
            "account only for sampling variability",
            "It takes 4 months to establish a trend for new houses sold",
            (
                'Since a "sale" is defined as a deposit taken or sales agreement signed, this '
                "can occur prior to a permit being issued"
            ),
            (
                "On average, the preliminary seasonally adjusted estimate of total sales is "
                f"revised about {self.spec.average_preliminary_revision_percent} percent"
            ),
            (
                "The 90 percent confidence interval includes zero. In such cases, there is "
                "insufficient statistical evidence to conclude that the actual change is "
                "different from zero."
            ),
        )
        if any(marker not in notes for marker in required):
            raise SourceSchemaError("Census/HUD NRS explanatory notes do not match")

    def _validate_table1a(self, table: str) -> None:
        reference_row = re.compile(
            rf"{self.spec.reference_month:%B}\s*\(p\)(?:\s*\.)+\s*"
            rf"{self.spec.sold_thousand_units:,}\b"
        )
        prior_row = re.compile(
            rf"{self.spec.prior_month:%B}\s*\(r\)(?:\s*\.)+\s*"
            rf"{self.spec.prior_month_revised_sold_thousand_units:,}\b"
        )
        rse_row = re.compile(
            rf"Average RSE \(%\)\s*3(?:\s*\.)+\s*{self.spec.average_rse_percent}\b"
        )
        change_label = self.spec.reference_month.strftime("%b.")
        prior_label = self.spec.prior_month.strftime("%b.")
        change_row = re.compile(
            rf"{re.escape(change_label)} 2020 from {re.escape(prior_label)} "
            rf"(?:2019|2020)(?:\s*\.)+\s*"
            rf"{re.escape(self.spec.monthly_change_percent)}\s*%"
        )
        margin_row = re.compile(
            rf"90 percent confidence interval\s*5(?:\s*\.)+\s*±\s*"
            rf"{re.escape(self.spec.monthly_margin_90_percent)}\b"
        )
        if not all(
            pattern.search(table)
            for pattern in (reference_row, prior_row, rse_row, change_row, margin_row)
        ):
            raise SourceSchemaError("Census/HUD NRS Table 1a values do not cross-check")

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        expected_path = f"/construction/nrs/pdf/newressales_{self.spec.reference_month:%Y%m}.pdf"
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("Census/HUD NRS response URL does not match request")


def _normalize(value: str) -> str:
    return " ".join(
        value.replace("\u00a0", " ")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2212", "-")
        .replace("“", '"')
        .replace("”", '"')
        .split()
    )


def _direction(change_percent: str) -> str:
    return "above" if _percent_decimal(change_percent) >= 0 else "below"


def _interval_contains_zero(change_percent: str, margin_percent: str) -> bool:
    return abs(_percent_decimal(change_percent)) <= _percent_decimal(margin_percent)


def _percent_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise SourceSchemaError("Census/HUD NRS percentage is not numeric") from error
    if not parsed.is_finite():
        raise SourceSchemaError("Census/HUD NRS percentage must be finite")
    return parsed
