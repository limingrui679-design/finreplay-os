"""BLS archived Consumer Price Index release adapter."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import TypedDict
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import HttpUrl, TypeAdapter

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
_EMBARGO = re.compile(
    r"Transmission of material in this release is embargoed until "
    r"8:30 a\.m\. \(ET\) (?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday), "
    r"(?P<release_date>[A-Z][a-z]+ [0-9]{1,2}, [0-9]{4}) "
    r"(?P<release_number>USDL-[0-9]{2}-[0-9]{4})\b"
)
_REPORT_TITLE = re.compile(
    r"CONSUMER PRICE INDEX - (?P<month>[A-Z]+) (?P<year>[0-9]{4})\b"
)
_HEADLINE = re.compile(
    r"The Consumer Price Index for All Urban Consumers \(CPI-U\) "
    r"(?P<direction>declined|rose) (?P<monthly>[0-9]+\.[0-9]) percent in "
    r"(?P<month>[A-Z][a-z]+) on a seasonally adjusted basis, after "
    r"(?P<prior_direction>increasing|declining) (?P<prior>[0-9]+\.[0-9]) percent in "
    r"(?P<prior_month>[A-Z][a-z]+), the U\.S\. Bureau of Labor Statistics reported "
    r"today\. Over the last 12 months, the all items index increased "
    r"(?P<year_over_year>[0-9]+\.[0-9]) percent before seasonal adjustment\."
)
_ONE_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]$")


class _ParsedCPIRelease(TypedDict):
    release_number: str
    release_at: datetime
    report_month: date
    monthly_change_tenths_percent: int
    year_over_year_tenths_percent: int
    monthly_direction: str
    prior_month: str
    prior_month_change_tenths_percent: int
    text_encoding: str


class _TextParser(HTMLParser):
    """Extract visible text without executing or interpreting page content."""

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


class BLSCPIArchiveAdapter:
    """Retrieve two headline CPI-U facts from one explicitly dated archived release."""

    availability_rule = (
        "The archived CPI release explicitly states that transmission is embargoed until 8:30 "
        "a.m. Eastern Time on its named release date. FinReplay parses that timestamp in "
        "America/New_York and converts it to UTC without backdating."
    )
    metadata = AdapterMetadata(
        adapter_id="bls.cpi.archived_release",
        title="BLS archived CPI-U all-items headline release facts",
        publisher="U.S. Bureau of Labor Statistics",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.bls.gov/cpi/news.htm"
        ),
        allowed_hosts=("www.bls.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only explicitly named historical archive pages sequentially; do not crawl "
            "or enumerate the archive. Use a descriptive contact-style user agent."
        ),
        pagination_policy=(
            "Each date-stamped archive page is one complete release with no pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each archived release is preserved as a versioned snapshot of its headline CPI-U "
            "facts. Annual seasonal-factor revisions or weight updates in later releases never "
            "overwrite an earlier page's reported values."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.REDISTRIBUTABLE,
        redistribution_note=(
            "BLS-published material is public domain except identified third-party material. "
            "Attribute the U.S. Bureau of Labor Statistics, retain the archive URL and release "
            "date, and do not use the protected BLS emblem."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        self.http = http
        self.release_date = release_date
        self.endpoint = (
            "https://www.bls.gov/news.release/archives/"
            f"cpi_{release_date:%m%d%Y}.htm"
        )

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise SourceSchemaError(f"unexpected BLS CPI release content type: {content_type!r}")
        parsed = self._parse_release(content)
        if retrieved_at < parsed["release_at"]:
            raise SourceSchemaError("selected BLS CPI release is not yet knowable")
        digest = source_response_sha256(content)
        source_version = (
            f"CPIRelease:{self.release_date.isoformat()}:{parsed['release_number']}:"
            f"sha256:{digest[:24]}"
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
            vintage_as_of=parsed["release_at"],
            redistribution_note=self.metadata.redistribution_note,
        )
        common_payload = {
            "release_date": self.release_date.isoformat(),
            "release_number": parsed["release_number"],
            "report_period": parsed["report_month"].strftime("%Y-%m"),
            "series": "Consumer Price Index for All Urban Consumers, All Items",
            "release_time_eastern": "08:30:00",
            "availability_method": "explicit_bls_embargo_end_america_new_york",
            "snapshot_semantics": "headline value reported in this archived release",
            "source_text_encoding": parsed["text_encoding"],
        }
        values = (
            (
                "all_items_monthly_change_seasonally_adjusted",
                parsed["monthly_change_tenths_percent"],
                {
                    "adjustment": "Seasonally Adjusted",
                    "direction_word": parsed["monthly_direction"],
                    "prior_month": parsed["prior_month"],
                    "prior_month_change_tenths_percent": parsed[
                        "prior_month_change_tenths_percent"
                    ],
                },
            ),
            (
                "all_items_12_month_change_not_seasonally_adjusted",
                parsed["year_over_year_tenths_percent"],
                {"adjustment": "Not Seasonally Adjusted"},
            ),
        )
        records = tuple(
            BitemporalRecord(
                record_id=(
                    f"{self.metadata.adapter_id}:{self.release_date:%Y%m%d}:{metric}"
                ),
                entity_id="bls_cpi_u_all_items:united_states",
                source=source,
                interval=BitemporalInterval(
                    valid_from=datetime(
                        parsed["report_month"].year,
                        parsed["report_month"].month,
                        1,
                        tzinfo=UTC,
                    ),
                    published_at=parsed["release_at"],
                    available_at=parsed["release_at"],
                    ingested_at=retrieved_at,
                    availability_rule=self.availability_rule,
                    availability_confidence=1.0,
                ),
                evidence_class=EvidenceClass.REPORTED,
                payload_schema_version="1.0.0",
                payload={
                    **common_payload,
                    "metric": metric,
                    "value_tenths_percent": value,
                    "unit": "Tenths of a Percent",
                    **metric_payload,
                },
            )
            for metric, value, metric_payload in values
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
            warnings=(
                "Archived CPI headlines are release-snapshot facts and may differ from later "
                "seasonally adjusted values after annual recalculation.",
                "The January 2023 CPI release documents new annual weights and revisions to the "
                "previous five years of seasonally adjusted indexes; adjacent snapshots are not "
                "treated as a calibrated stationary forecasting sample.",
            ),
        )
        return AdapterBatch(
            records=records,
            receipts=(receipt,),
            artifacts=(RawArtifact(sha256=digest, content_type=content_type, content=content),),
        )

    def _parse_release(self, content: bytes) -> _ParsedCPIRelease:
        decoded, text_encoding = _decode_html(content)
        parser = _TextParser()
        try:
            parser.feed(decoded)
            parser.close()
        except (AssertionError, ValueError) as error:
            raise SourceSchemaError("BLS CPI response is not structurally valid HTML") from error
        text = _normalize(" ".join(parser.parts))
        embargo_matches = list(_EMBARGO.finditer(text))
        title_matches = list(_REPORT_TITLE.finditer(text))
        headline_matches = list(_HEADLINE.finditer(text))
        if len(embargo_matches) != 1:
            raise SourceSchemaError("BLS CPI release must contain exactly one embargo statement")
        if len(title_matches) != 1:
            raise SourceSchemaError("BLS CPI release must contain exactly one report-period title")
        if len(headline_matches) != 1:
            raise SourceSchemaError("BLS CPI release must contain exactly one headline fact block")
        embargo = embargo_matches[0]
        title = title_matches[0]
        headline = headline_matches[0]
        release_date = _display_date(embargo.group("release_date"))
        if release_date != self.release_date:
            raise SourceSchemaError("BLS CPI page release date does not match requested date")
        if embargo.group("weekday") != calendar.day_name[release_date.weekday()]:
            raise SourceSchemaError("BLS CPI embargo weekday does not match release date")
        title_month = _month_number(title.group("month"), "report title")
        headline_month = _month_number(headline.group("month").upper(), "headline")
        title_year = int(title.group("year"))
        if title_month != headline_month:
            raise SourceSchemaError("BLS CPI headline month does not match report-period title")
        report_month = date(title_year, title_month, 1)
        if report_month >= self.release_date.replace(day=1):
            raise SourceSchemaError("BLS CPI report period must precede the release month")
        prior_month = _month_number(headline.group("prior_month").upper(), "prior headline")
        expected_prior_month = 12 if title_month == 1 else title_month - 1
        if prior_month != expected_prior_month:
            raise SourceSchemaError("BLS CPI prior month is not the preceding calendar month")
        monthly = _tenths_percent(headline.group("monthly"), "monthly change")
        if headline.group("direction") == "declined":
            monthly = -monthly
        prior = _tenths_percent(headline.group("prior"), "prior-month change")
        if headline.group("prior_direction") == "declining":
            prior = -prior
        year_over_year = _tenths_percent(
            headline.group("year_over_year"),
            "12-month change",
        )
        release_at = datetime(
            release_date.year,
            release_date.month,
            release_date.day,
            8,
            30,
            tzinfo=_NEW_YORK,
        ).astimezone(UTC)
        return {
            "release_number": embargo.group("release_number"),
            "release_at": release_at,
            "report_month": report_month,
            "monthly_change_tenths_percent": monthly,
            "year_over_year_tenths_percent": year_over_year,
            "monthly_direction": headline.group("direction"),
            "prior_month": headline.group("prior_month"),
            "prior_month_change_tenths_percent": prior,
            "text_encoding": text_encoding,
        }

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        expected_path = f"/news.release/archives/cpi_{self.release_date:%m%d%Y}.htm"
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("BLS CPI response URL does not match the requested release")


def _display_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%B %d, %Y").replace(tzinfo=UTC).date()
    except ValueError as error:
        raise SourceSchemaError("BLS CPI release date must use Month D, YYYY") from error


def _month_number(value: str, context: str) -> int:
    try:
        return list(calendar.month_name).index(value.title())
    except ValueError as error:
        raise SourceSchemaError(f"BLS CPI {context} month is invalid") from error


def _tenths_percent(value: str, context: str) -> int:
    if _ONE_DECIMAL.fullmatch(value) is None:
        raise SourceSchemaError(f"BLS CPI {context} must have one decimal place")
    whole, tenths = value.split(".")
    parsed = int(whole) * 10 + int(tenths)
    if parsed > 1_000:
        raise SourceSchemaError(f"BLS CPI {context} is outside the supported range")
    return parsed


def _decode_html(content: bytes) -> tuple[str, str]:
    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try:
            return content.decode("windows-1252"), "windows-1252"
        except UnicodeDecodeError as error:
            raise SourceSchemaError(
                "BLS CPI HTML is neither valid UTF-8 nor Windows-1252"
            ) from error


def _normalize(value: str) -> str:
    return " ".join(value.split())
