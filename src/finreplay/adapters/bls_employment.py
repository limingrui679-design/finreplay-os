"""BLS archived Employment Situation release adapter."""

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
_INTEGER = re.compile(r"^[1-9][0-9]{0,2}(?:,[0-9]{3})*$|^[1-9][0-9]*$")
_RATE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9])$")
_EMBARGO = re.compile(
    r"Transmission of material in this news release is embargoed until\s+"
    r"(?P<release_number>USDL-[0-9]{2}-[0-9]{4})\s+"
    r"8:30 a\.m\. \(ET\) (?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday), "
    r"(?P<release_date>[A-Z][a-z]+ [0-9]{1,2}, [0-9]{4})\b"
)
_REPORT_TITLE = re.compile(
    r"THE EMPLOYMENT SITUATION -- (?P<month>[A-Z]+) (?P<year>[0-9]{4})\b"
)
_HEADLINE = re.compile(
    r"Total nonfarm payroll employment (?P<verb>increased|rose) by "
    r"(?P<payroll>[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+) in "
    r"(?P<month>[A-Z][a-z]+), and the unemployment rate\s+"
    r"(?P<rate_phrase>edged down to|changed little at|edged up to|was unchanged at) "
    r"(?P<rate>[0-9]+\.[0-9]) percent, the U\.S\. Bureau of Labor Statistics "
    r"reported today\."
)


class _ParsedEmploymentRelease(TypedDict):
    release_number: str
    release_at: datetime
    report_month: date
    payroll_change_thousands: int
    unemployment_rate_percent: float
    payroll_verb: str
    unemployment_rate_phrase: str
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


class BLSEmploymentSituationArchiveAdapter:
    """Retrieve two headline facts from one explicitly dated BLS archived release."""

    availability_rule = (
        "The archived Employment Situation page explicitly states that transmission is "
        "embargoed until 8:30 a.m. Eastern Time on its named release date. FinReplay parses "
        "that timestamp in America/New_York and converts it to UTC without backdating."
    )
    metadata = AdapterMetadata(
        adapter_id="bls.employment_situation.archived_release",
        title="BLS archived Employment Situation headline release facts",
        publisher="U.S. Bureau of Labor Statistics",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.bls.gov/bls/news-release/empsit.htm"
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
            "Each archived release is preserved as a versioned snapshot of what BLS reported at "
            "that release. Later benchmark revisions or monthly revisions are separate releases "
            "and never overwrite the archived headline facts."
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
            f"empsit_{release_date:%m%d%Y}.htm"
        )

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise SourceSchemaError(
                f"unexpected BLS Employment Situation content type: {content_type!r}"
            )
        parsed = self._parse_release(content)
        if retrieved_at < parsed["release_at"]:
            raise SourceSchemaError("selected BLS release is not yet knowable")
        digest = source_response_sha256(content)
        source_version = (
            f"EmploymentSituation:{self.release_date.isoformat()}:"
            f"{parsed['release_number']}:sha256:{digest[:24]}"
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
            "release_time_eastern": "08:30:00",
            "availability_method": "explicit_bls_embargo_end_america_new_york",
            "snapshot_semantics": "headline value reported in this archived release",
            "source_text_encoding": parsed["text_encoding"],
        }
        values = (
            (
                "nonfarm_payroll_change",
                {
                    "value_thousands": parsed["payroll_change_thousands"],
                    "unit": "Thousands of Persons",
                    "wording": parsed["payroll_verb"],
                },
            ),
            (
                "unemployment_rate",
                {
                    "value_percent": parsed["unemployment_rate_percent"],
                    "unit": "Percent",
                    "wording": parsed["unemployment_rate_phrase"],
                },
            ),
        )
        records = tuple(
            BitemporalRecord(
                record_id=(
                    f"{self.metadata.adapter_id}:{self.release_date:%Y%m%d}:{metric}"
                ),
                entity_id="bls_employment_situation:united_states",
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
                payload={**common_payload, "metric": metric, **metric_payload},
            )
            for metric, metric_payload in values
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
                "Archived headline values are release-snapshot facts and may differ from later "
                "revised series values.",
                "The January 2023 release documents annual establishment-survey benchmarking "
                "and updated seasonal adjustment factors; adjacent releases are not treated as "
                "a calibrated stationary forecasting sample.",
            ),
        )
        return AdapterBatch(
            records=records,
            receipts=(receipt,),
            artifacts=(RawArtifact(sha256=digest, content_type=content_type, content=content),),
        )

    def _parse_release(self, content: bytes) -> _ParsedEmploymentRelease:
        decoded, text_encoding = _decode_html(content)
        parser = _TextParser()
        try:
            parser.feed(decoded)
            parser.close()
        except (AssertionError, ValueError) as error:
            raise SourceSchemaError(
                "BLS Employment Situation response is not structurally valid HTML"
            ) from error
        text = _normalize(" ".join(parser.parts))
        embargo_matches = list(_EMBARGO.finditer(text))
        title_matches = list(_REPORT_TITLE.finditer(text))
        headline_matches = list(_HEADLINE.finditer(text))
        if len(embargo_matches) != 1:
            raise SourceSchemaError("BLS release must contain exactly one explicit embargo time")
        if len(title_matches) != 1:
            raise SourceSchemaError("BLS release must contain exactly one report-period title")
        if len(headline_matches) != 1:
            raise SourceSchemaError("BLS release must contain exactly one headline fact sentence")
        embargo = embargo_matches[0]
        title = title_matches[0]
        headline = headline_matches[0]
        release_date = _display_date(embargo.group("release_date"), "release date")
        if release_date != self.release_date:
            raise SourceSchemaError("BLS page release date does not match requested date")
        if embargo.group("weekday") != calendar.day_name[release_date.weekday()]:
            raise SourceSchemaError("BLS embargo weekday does not match release date")
        title_month = _month_number(title.group("month"), "report title")
        headline_month = _month_number(headline.group("month").upper(), "headline")
        title_year = int(title.group("year"))
        if title_month != headline_month:
            raise SourceSchemaError("BLS headline month does not match report-period title")
        report_month = date(title_year, title_month, 1)
        if report_month >= self.release_date.replace(day=1):
            raise SourceSchemaError("BLS report period must precede the release month")
        payroll_text = headline.group("payroll")
        if _INTEGER.fullmatch(payroll_text) is None:
            raise SourceSchemaError("BLS payroll change must be a positive integer")
        payroll_persons = int(payroll_text.replace(",", ""))
        if payroll_persons % 1_000:
            raise SourceSchemaError(
                "BLS headline payroll change must be an exact multiple of 1,000 persons"
            )
        payroll = payroll_persons // 1_000
        rate_text = headline.group("rate")
        if _RATE.fullmatch(rate_text) is None:
            raise SourceSchemaError("BLS unemployment rate must have one decimal place")
        rate = float(rate_text)
        if not 0.0 <= rate <= 100.0:
            raise SourceSchemaError("BLS unemployment rate must be between zero and 100")
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
            "payroll_change_thousands": payroll,
            "unemployment_rate_percent": rate,
            "payroll_verb": headline.group("verb"),
            "unemployment_rate_phrase": headline.group("rate_phrase"),
            "text_encoding": text_encoding,
        }

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        expected_path = f"/news.release/archives/empsit_{self.release_date:%m%d%Y}.htm"
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("BLS response URL does not match the requested release")


def _display_date(value: str, context: str) -> date:
    try:
        return datetime.strptime(value, "%B %d, %Y").replace(tzinfo=UTC).date()
    except ValueError as error:
        raise SourceSchemaError(f"BLS {context} must use Month D, YYYY") from error


def _month_number(value: str, context: str) -> int:
    try:
        return list(calendar.month_name).index(value.title())
    except ValueError as error:
        raise SourceSchemaError(f"BLS {context} month is invalid") from error


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _decode_html(content: bytes) -> tuple[str, str]:
    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try:
            return content.decode("windows-1252"), "windows-1252"
        except UnicodeDecodeError as error:
            raise SourceSchemaError(
                "BLS Employment Situation HTML is neither valid UTF-8 nor Windows-1252"
            ) from error
