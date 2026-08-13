"""Federal Reserve archived FOMC statement adapter."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from fractions import Fraction
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
_HEADER = re.compile(
    r"(?P<release_date>[A-Z][a-z]+ [0-9]{2}, [0-9]{4}) "
    r"Federal Reserve issues FOMC statement For release at 2:00 p\.m\. "
    r"(?P<timezone>EST|EDT)\b"
)
_TARGET_RANGE = re.compile(
    r"[Tt]he Committee decided to raise the target range for the federal funds rate to "
    r"(?P<lower>[0-9]{1,2}(?:-(?:1/4|1/2|3/4))?) to "
    r"(?P<upper>[0-9]{1,2}(?:-(?:1/4|1/2|3/4))?) percent\."
)
_LAST_UPDATE = re.compile(
    r"Last Update: (?P<release_date>[A-Z][a-z]+ [0-9]{2}, [0-9]{4})\b"
)
_RATE_TOKEN = re.compile(r"^(?P<whole>[0-9]{1,2})(?:-(?P<fraction>1/4|1/2|3/4))?$")


class _ParsedFOMCStatement(TypedDict):
    release_at: datetime
    timezone_abbreviation: str
    lower_basis_points: int
    upper_basis_points: int
    lower_display: str
    upper_display: str


class _TextParser(HTMLParser):
    """Extract visible page text without executing scripts or styles."""

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


class FederalReserveFOMCStatementAdapter:
    """Retrieve the target-rate range from one date-stamped archived FOMC statement."""

    availability_rule = (
        "The archived FOMC statement explicitly says 'For release at 2:00 p.m.' with an "
        "EST or EDT abbreviation. FinReplay validates that abbreviation against "
        "America/New_York on the named release date and converts the timestamp to UTC."
    )
    metadata = AdapterMetadata(
        adapter_id="federal_reserve.fomc.archived_statement",
        title="Federal Reserve archived FOMC statement target range",
        publisher="Board of Governors of the Federal Reserve System",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
        ),
        allowed_hosts=("www.federalreserve.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only explicitly named historical statement dates sequentially; do not "
            "crawl or enumerate press-release archives."
        ),
        pagination_policy=(
            "Each date-stamped FOMC statement page is a complete release without pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each date-stamped statement is a versioned policy-release snapshot. Later statements "
            "are separate source versions and never overwrite the earlier target range."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Keep downloaded HTML in local content-addressed storage. Repository scenarios retain "
            "only minimal target-range facts, source links, hashes, and attribution to the Board "
            "of Governors; do not imply endorsement or redistribute third-party page material."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        self.http = http
        self.release_date = release_date
        self.endpoint = (
            "https://www.federalreserve.gov/newsevents/pressreleases/"
            f"monetary{release_date:%Y%m%d}a.htm"
        )

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise SourceSchemaError(f"unexpected FOMC statement content type: {content_type!r}")
        parsed = self._parse_statement(content)
        if retrieved_at < parsed["release_at"]:
            raise SourceSchemaError("selected FOMC statement is not yet knowable")
        digest = source_response_sha256(content)
        source_version = (
            f"FOMCStatement:{self.release_date.isoformat()}:sha256:{digest[:24]}"
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
        width = parsed["upper_basis_points"] - parsed["lower_basis_points"]
        common_payload = {
            "release_date": self.release_date.isoformat(),
            "policy": "Federal funds target range",
            "unit": "Basis Points",
            "range_width_basis_points": width,
            "release_time_eastern": "14:00:00",
            "release_timezone_abbreviation": parsed["timezone_abbreviation"],
            "availability_method": "explicit_fomc_release_time_america_new_york",
        }
        values = (
            ("target_range_lower", parsed["lower_basis_points"], parsed["lower_display"]),
            ("target_range_upper", parsed["upper_basis_points"], parsed["upper_display"]),
        )
        records = tuple(
            BitemporalRecord(
                record_id=(
                    f"{self.metadata.adapter_id}:{self.release_date:%Y%m%d}:{metric}"
                ),
                entity_id="fomc_policy:federal_funds_target_range",
                source=source,
                interval=BitemporalInterval(
                    valid_from=parsed["release_at"],
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
                    "value_basis_points": value,
                    "source_display_value_percent": display,
                },
            )
            for metric, value, display in values
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
                "The target range is an official policy-release fact, not a market forecast or "
                "evidence of the policy's causal effects.",
                "Full archived HTML is retained locally as download-only evidence and is not "
                "redistributed with the repository.",
            ),
        )
        return AdapterBatch(
            records=records,
            receipts=(receipt,),
            artifacts=(RawArtifact(sha256=digest, content_type=content_type, content=content),),
        )

    def _parse_statement(self, content: bytes) -> _ParsedFOMCStatement:
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("FOMC statement HTML is not valid UTF-8") from error
        parser = _TextParser()
        try:
            parser.feed(decoded)
            parser.close()
        except (AssertionError, ValueError) as error:
            raise SourceSchemaError("FOMC response is not structurally valid HTML") from error
        text = _normalize(" ".join(parser.parts))
        headers = list(_HEADER.finditer(text))
        ranges = list(_TARGET_RANGE.finditer(text))
        updates = list(_LAST_UPDATE.finditer(text))
        if len(headers) != 1:
            raise SourceSchemaError("FOMC page must contain exactly one dated release-time header")
        if len(ranges) != 1:
            raise SourceSchemaError("FOMC page must contain exactly one target-range decision")
        if len(updates) != 1:
            raise SourceSchemaError("FOMC page must contain exactly one Last Update date")
        header = headers[0]
        release_date = _display_date(header.group("release_date"))
        update_date = _display_date(updates[0].group("release_date"))
        if release_date != self.release_date or update_date != self.release_date:
            raise SourceSchemaError("FOMC page dates do not match the requested release")
        release_local = datetime(
            release_date.year,
            release_date.month,
            release_date.day,
            14,
            0,
            tzinfo=_NEW_YORK,
        )
        timezone_abbreviation = release_local.tzname()
        if timezone_abbreviation is None:
            raise SourceSchemaError("FOMC release timezone could not be resolved")
        if timezone_abbreviation != header.group("timezone"):
            raise SourceSchemaError("FOMC release timezone abbreviation is inconsistent")
        target_range = ranges[0]
        lower = _rate_basis_points(target_range.group("lower"))
        upper = _rate_basis_points(target_range.group("upper"))
        if lower >= upper:
            raise SourceSchemaError("FOMC target range must have increasing endpoints")
        if upper - lower not in {25, 50, 75, 100}:
            raise SourceSchemaError("FOMC target range width is outside supported increments")
        return {
            "release_at": release_local.astimezone(UTC),
            "timezone_abbreviation": timezone_abbreviation,
            "lower_basis_points": lower,
            "upper_basis_points": upper,
            "lower_display": target_range.group("lower"),
            "upper_display": target_range.group("upper"),
        }

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        expected_path = (
            f"/newsevents/pressreleases/monetary{self.release_date:%Y%m%d}a.htm"
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("FOMC response URL does not match the requested release")


def _display_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%B %d, %Y").replace(tzinfo=UTC).date()
    except ValueError as error:
        raise SourceSchemaError("FOMC release date must use Month DD, YYYY") from error


def _rate_basis_points(value: str) -> int:
    match = _RATE_TOKEN.fullmatch(value)
    if match is None:
        raise SourceSchemaError("FOMC target endpoint has an unsupported percent format")
    fraction = Fraction(match.group("fraction") or "0")
    percent = Fraction(int(match.group("whole")), 1) + fraction
    basis_points = percent * 100
    if basis_points.denominator != 1:
        raise SourceSchemaError("FOMC target endpoint does not resolve to whole basis points")
    return basis_points.numerator


def _normalize(value: str) -> str:
    return " ".join(value.split())
