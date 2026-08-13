"""Federal Reserve H.4.1 archived BTFP release adapter."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from html.parser import HTMLParser
from typing import TypedDict
from urllib.parse import urlparse

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
_INTEGER = re.compile(r"^[0-9]{1,3}(?:,[0-9]{3})*$|^[0-9]+$")
_SIGNED_INTEGER = re.compile(r"^(?:[+-]\s*)?(?:[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)$")


class _ParsedH41(TypedDict):
    week_ending: date
    weekly_average: int
    weekly_change: int
    year_change: int
    wednesday_outstanding: int


class _ReleaseHTMLParser(HTMLParser):
    """Extract text and non-nested table cells without executing page content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table = []
        elif self._table_depth == 1 and tag == "tr":
            self._row = []
        elif self._table_depth == 1 and tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self._table_depth == 1 and tag in {"td", "th"} and self._cell_parts is not None:
            assert self._row is not None
            self._row.append(_normalize(" ".join(self._cell_parts)))
            self._cell_parts = None
        elif self._table_depth == 1 and tag == "tr" and self._row is not None:
            if self._row:
                assert self._table is not None
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth > 0:
            if self._table_depth == 1:
                assert self._table is not None
                self.tables.append(self._table)
                self._table = None
            self._table_depth -= 1

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text_parts.append(data)
            if self._cell_parts is not None:
                self._cell_parts.append(data)


class FederalReserveH41BTFPAdapter:
    """Retrieve the BTFP row from one dated archived H.4.1 release."""

    availability_lag = timedelta(days=2)
    availability_rule = (
        "The archived H.4.1 page identifies a calendar release date but the adapter does not "
        "rely on an intraday timestamp. FinReplay permits use only from 00:00 UTC two calendar "
        "days after that release date, a deterministic conservative knowledge bound."
    )
    metadata = AdapterMetadata(
        adapter_id="federal_reserve.h41.btfp_historical_release",
        title="Federal Reserve H.4.1 archived Bank Term Funding Program balances",
        publisher="Board of Governors of the Federal Reserve System",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.federalreserve.gov/releases/h41/about.htm"
        ),
        allowed_hosts=("www.federalreserve.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only explicitly named historical release dates sequentially; do not crawl "
            "or enumerate the release archive."
        ),
        pagination_policy=(
            "Each dated H.4.1 HTML page is one complete archived release with no pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each date-stamped H.4.1 page is treated as a versioned release snapshot. Later "
            "releases are separate source versions and never overwrite an earlier release fact."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Keep full downloaded HTML only in local content-addressed storage. Attribute the "
            "Board of Governors and preserve source links; repository scenarios retain only "
            "minimal reported facts and hashes, not the full source page."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        self.http = http
        self.release_date = release_date
        self.endpoint = (
            f"https://www.federalreserve.gov/releases/h41/{release_date:%Y%m%d}/"
        )

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise SourceSchemaError(f"unexpected H.4.1 content type: {content_type!r}")
        parsed = self._parse_release(content)
        digest = source_response_sha256(content)
        vintage_as_of = datetime.combine(self.release_date, time.min, tzinfo=UTC)
        available_at = vintage_as_of + self.availability_lag
        if retrieved_at < available_at:
            raise SourceSchemaError("selected H.4.1 release is not yet conservatively knowable")
        source_version = f"H41:{self.release_date.isoformat()}:sha256:{digest[:24]}"
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            vintage_as_of=vintage_as_of,
            redistribution_note=self.metadata.redistribution_note,
        )
        common_payload = {
            "release_date": self.release_date.isoformat(),
            "week_ending": parsed["week_ending"].isoformat(),
            "program": "Bank Term Funding Program",
            "unit": "Millions of Dollars",
            "table": "H.4.1 Table 1",
            "weekly_average_change_from_prior_millions": parsed["weekly_change"],
            "weekly_average_change_from_year_ago_millions": parsed["year_change"],
            "availability_method": "release_date_plus_two_calendar_days_utc",
        }
        values = (
            ("weekly_average", parsed["weekly_average"]),
            ("wednesday_outstanding", parsed["wednesday_outstanding"]),
        )
        records = tuple(
            BitemporalRecord(
                record_id=(
                    f"{self.metadata.adapter_id}:{self.release_date:%Y%m%d}:"
                    f"{metric}"
                ),
                entity_id="federal_reserve_facility:btfp",
                source=source,
                interval=BitemporalInterval(
                    valid_from=datetime.combine(parsed["week_ending"], time.min, tzinfo=UTC),
                    published_at=available_at,
                    available_at=available_at,
                    ingested_at=retrieved_at,
                    availability_rule=self.availability_rule,
                    availability_confidence=1.0,
                ),
                evidence_class=EvidenceClass.REPORTED,
                payload_schema_version="1.0.0",
                payload={**common_payload, "metric": metric, "value_millions": value},
            )
            for metric, value in values
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
                "H.4.1 release timing is recorded as a conservative date-plus-two-day bound, "
                "not an exact intraday publication timestamp.",
                "Full archived HTML is retained locally as download-only evidence and is not "
                "redistributed with the repository.",
            ),
        )
        return AdapterBatch(
            records=records,
            receipts=(receipt,),
            artifacts=(RawArtifact(sha256=digest, content_type=content_type, content=content),),
        )

    def _parse_release(self, content: bytes) -> _ParsedH41:
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("H.4.1 HTML is not valid UTF-8") from error
        parser = _ReleaseHTMLParser()
        try:
            parser.feed(decoded)
            parser.close()
        except (AssertionError, ValueError) as error:
            raise SourceSchemaError("H.4.1 response is not structurally valid HTML") from error
        page_text = _normalize(" ".join(parser.text_parts))
        formatted_date = self.release_date.strftime("%B %d, %Y").replace(" 0", " ")
        release_pattern = re.compile(
            rf"Release Date:\s*(?:Thursday,\s*)?{re.escape(formatted_date)}\b",
            re.IGNORECASE,
        )
        if release_pattern.search(page_text) is None:
            raise SourceSchemaError("H.4.1 page release date does not match requested date")
        matches = []
        for table in parser.tables:
            target_rows = [
                row
                for row in table
                if len(row) == 5 and row[0] == "Bank Term Funding Program"
            ]
            if target_rows:
                matches.append((table, target_rows))
        if len(matches) != 1 or len(matches[0][1]) != 1:
            raise SourceSchemaError("H.4.1 must contain exactly one five-cell BTFP Table 1 row")
        table, target_rows = matches[0]
        if len(table) < 3 or "Averages of daily figures" not in table[0]:
            raise SourceSchemaError("H.4.1 BTFP table header is missing daily-average semantics")
        wednesday_cells = [cell for cell in table[0] if cell.startswith("Wednesday ")]
        week_cells = [cell for cell in table[1] if cell.startswith("Week ended ")]
        if len(wednesday_cells) != 1 or len(week_cells) != 1:
            raise SourceSchemaError("H.4.1 BTFP table must identify one week-ending Wednesday")
        wednesday = _parse_display_date(wednesday_cells[0].removeprefix("Wednesday "))
        week_ending = _parse_display_date(week_cells[0].removeprefix("Week ended "))
        if wednesday != week_ending or week_ending >= self.release_date:
            raise SourceSchemaError("H.4.1 week-ending date is inconsistent with release date")
        row = target_rows[0]
        weekly_average = _positive_integer(row[1], "weekly average")
        weekly_change = _signed_integer(row[2], "weekly change")
        year_change = _signed_integer(row[3], "year-over-year change")
        wednesday_outstanding = _positive_integer(row[4], "Wednesday outstanding")
        return {
            "week_ending": week_ending,
            "weekly_average": weekly_average,
            "weekly_change": weekly_change,
            "year_change": year_change,
            "wednesday_outstanding": wednesday_outstanding,
        }

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        expected_path = f"/releases/h41/{self.release_date:%Y%m%d}/"
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("H.4.1 response URL does not match the requested release")


def _parse_display_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%b %d, %Y").replace(tzinfo=UTC).date()
    except ValueError as error:
        raise SourceSchemaError("H.4.1 header date must use Mon D, YYYY") from error


def _positive_integer(value: str, context: str) -> int:
    if _INTEGER.fullmatch(value) is None:
        raise SourceSchemaError(f"H.4.1 {context} must be a positive integer")
    parsed = int(value.replace(",", ""))
    if parsed <= 0:
        raise SourceSchemaError(f"H.4.1 {context} must be a positive integer")
    return parsed


def _signed_integer(value: str, context: str) -> int:
    if _SIGNED_INTEGER.fullmatch(value) is None:
        raise SourceSchemaError(f"H.4.1 {context} must be a signed integer")
    return int(value.replace(",", "").replace(" ", ""))


def _normalize(value: str) -> str:
    return " ".join(value.split())
