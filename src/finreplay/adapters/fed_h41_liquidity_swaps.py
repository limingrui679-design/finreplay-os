"""Paired archived Federal Reserve H.4.1 liquidity-swap adapter."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
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
_INTEGER = re.compile(r"^[0-9]{1,3}(?:,[0-9]{3})*$|^[0-9]+$")
_SIGNED_INTEGER = re.compile(r"^(?:[+-]\s*)?(?:[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)$")
_TABLE_ROW_LABEL = re.compile(r"^Central bank liquidity swaps [0-9]+$")
_TXT_TABLE_ROW = re.compile(
    r"^\s*Central bank liquidity swaps \([0-9]+\)\s+"
    r"([0-9][0-9,]*)\s+([+-])?\s*([0-9][0-9,]*)\s+"
    r"([+-])?\s*([0-9][0-9,]*)\s+([0-9][0-9,]*)\s*$"
)
_SWAP_FOOTNOTE = (
    "Dollar value of foreign currency held under these agreements valued at the exchange rate "
    "to be used when the foreign currency is returned to the foreign central bank. This "
    "exchange rate equals the market exchange rate used when the foreign currency was acquired "
    "from the foreign central bank."
)


@dataclass(frozen=True, slots=True)
class _ReleaseSpec:
    release_date: date
    week_ending: date
    timezone_abbreviation: str
    exact_stated_time: bool
    weekly_average_millions: int
    weekly_average_change_millions: int
    weekly_average_year_change_millions: int
    wednesday_outstanding_millions: int


_VERIFIED_RELEASES = {
    date(2020, 3, 19): _ReleaseSpec(
        release_date=date(2020, 3, 19),
        week_ending=date(2020, 3, 18),
        timezone_abbreviation="EDT",
        exact_stated_time=True,
        weekly_average_millions=45,
        weekly_average_change_millions=-13,
        weekly_average_year_change_millions=-23,
        wednesday_outstanding_millions=45,
    ),
    date(2020, 3, 26): _ReleaseSpec(
        release_date=date(2020, 3, 26),
        week_ending=date(2020, 3, 25),
        timezone_abbreviation="EDT",
        exact_stated_time=True,
        weekly_average_millions=168_814,
        weekly_average_change_millions=168_769,
        weekly_average_year_change_millions=168_748,
        wednesday_outstanding_millions=206_051,
    ),
    date(2020, 4, 2): _ReleaseSpec(
        release_date=date(2020, 4, 2),
        week_ending=date(2020, 4, 1),
        timezone_abbreviation="EDT",
        exact_stated_time=False,
        weekly_average_millions=327_787,
        weekly_average_change_millions=158_973,
        weekly_average_year_change_millions=326_422,
        wednesday_outstanding_millions=348_544,
    ),
}


class _ParsedRelease(TypedDict):
    week_ending: date
    weekly_average_millions: int
    weekly_average_change_millions: int
    weekly_average_year_change_millions: int
    wednesday_outstanding_millions: int


class _ReleaseHTMLParser(HTMLParser):
    """Extract visible text and unnested table cells without executing content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._ignored_depth = 0
        self._table_depth = 0
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        if self._ignored_depth:
            return
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table = []
        elif self._table_depth == 1 and tag == "tr":
            self._row = []
        elif self._table_depth == 1 and tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
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
        if self._ignored_depth == 0 and data.strip():
            self.text_parts.append(data)
            if self._cell_parts is not None:
                self._cell_parts.append(data)


class FederalReserveH41LiquiditySwapsAdapter:
    """Retrieve one fixed H.4.1 central-bank-liquidity-swaps release pair."""

    availability_rule = (
        "For March 19 and 26, the archived H.4.1 HTML explicitly states 'For Release at 4:30 "
        "P.M. EDT'; FinReplay validates that official stated time against America/New_York. "
        "The April 2 HTML identifies only its Thursday release date, so FinReplay waits until "
        "the following New York midnight. Every Table 1 fact is cross-checked against the "
        "complete official ASCII release. Neither method is represented as an independently "
        "measured server-publication log, and current retrieval metadata is never backdated."
    )
    metadata = AdapterMetadata(
        adapter_id="federal_reserve.h41.central_bank_liquidity_swaps",
        title="Federal Reserve H.4.1 archived central bank liquidity swap balances",
        publisher="Board of Governors of the Federal Reserve System",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.federalreserve.gov/releases/h41/about.htm"
        ),
        allowed_hosts=("www.federalreserve.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 HTML/ASCII pairs sequentially; "
            "do not crawl or enumerate the release archive."
        ),
        pagination_policy=(
            "Each dated selection is one complete H.4.1 HTML page and matching ASCII release "
            "with no API pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each dated H.4.1 pair is a versioned release snapshot. Later releases remain "
            "separate facts and never overwrite an earlier Wednesday balance."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Keep full downloaded HTML and ASCII releases only in local content-addressed "
            "storage. Attribute the Board of Governors and preserve source links; repository "
            "scenarios retain only minimal reported facts and hashes."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified H.4.1 swap calendar")
        self.http = http
        self.release_date = release_date
        self.spec = _VERIFIED_RELEASES[release_date]
        root = f"https://www.federalreserve.gov/releases/h41/{release_date:%Y%m%d}"
        self.html_endpoint = f"{root}/h41.htm"
        self.txt_endpoint = f"{root}/H41.TXT"

    def fetch(self) -> AdapterBatch:
        html_response, html_content, html_retrieved_at = self.http.get(
            self.html_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        txt_response, txt_content, txt_retrieved_at = self.http.get(
            self.txt_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(html_response.request_url, kind="html")
        self._validate_response_url(txt_response.request_url, kind="txt")
        html_content_type = html_response.headers.get("Content-Type", "").split(";", 1)[0]
        if html_content_type not in {"text/html", "application/xhtml+xml"}:
            raise SourceSchemaError(f"unexpected H.4.1 HTML content type: {html_content_type!r}")
        txt_content_type = txt_response.headers.get("Content-Type", "").split(";", 1)[0]
        if txt_content_type not in {"text/plain", "text/txt"}:
            raise SourceSchemaError(f"unexpected H.4.1 ASCII content type: {txt_content_type!r}")

        html_parsed = self._parse_html(html_content)
        txt_parsed = self._parse_txt(txt_content)
        if html_parsed != txt_parsed:
            raise SourceSchemaError("H.4.1 HTML and ASCII Table 1 values do not agree")
        expected = _expected_parsed(self.spec)
        if html_parsed != expected:
            raise SourceSchemaError("H.4.1 liquidity-swap release differs from pinned facts")

        stated_release_local = datetime.combine(
            self.release_date,
            time(16, 30),
            tzinfo=_NEW_YORK,
        )
        if stated_release_local.tzname() != self.spec.timezone_abbreviation:
            raise SourceSchemaError("H.4.1 release timezone does not match New York calendar")
        if self.spec.exact_stated_time:
            available_at = stated_release_local.astimezone(UTC)
            availability_method = "exact_official_stated_time_crosschecked_html_ascii"
        else:
            available_at = datetime.combine(
                self.release_date + timedelta(days=1),
                time.min,
                tzinfo=_NEW_YORK,
            ).astimezone(UTC)
            availability_method = "release_date_following_new_york_midnight_html_ascii"
        if html_retrieved_at < available_at or txt_retrieved_at < available_at:
            raise SourceSchemaError("selected H.4.1 release is not yet knowable")
        retrieved_at = max(html_retrieved_at, txt_retrieved_at)

        html_digest = source_response_sha256(html_content)
        txt_digest = source_response_sha256(txt_content)
        release_semantics = {
            "schema_version": "1.0.0",
            "release_date": self.release_date.isoformat(),
            "parsed_table_1": {
                **html_parsed,
                "week_ending": html_parsed["week_ending"].isoformat(),
            },
            "availability_method": availability_method,
            "available_at": available_at.isoformat(),
            "timezone": "America/New_York",
            "measurement_footnote": _SWAP_FOOTNOTE,
            "html_ascii_crosscheck_verified": True,
        }
        release_semantic_digest = _hash(release_semantics)
        source_version = (
            f"H41-SWAPS:{self.release_date.isoformat()}:"
            f"semantic:{release_semantic_digest[:24]}"
        )
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(html_response.request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=release_semantic_digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            vintage_as_of=available_at,
            redistribution_note=self.metadata.redistribution_note,
        )
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.spec.week_ending:%Y%m%d}:"
                "wednesday_outstanding"
            ),
            entity_id="federal_reserve_facility:central_bank_liquidity_swaps",
            source=source,
            interval=BitemporalInterval(
                valid_from=datetime.combine(self.spec.week_ending, time.min, tzinfo=UTC),
                published_at=available_at,
                available_at=available_at,
                ingested_at=retrieved_at,
                availability_rule=self.availability_rule,
                availability_confidence=1.0,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload={
                "release_date": self.release_date.isoformat(),
                "week_ending": self.spec.week_ending.isoformat(),
                "release_series": "H.4.1 Factors Affecting Reserve Balances",
                "table": "H.4.1 Table 1",
                "program": "Central bank liquidity swaps",
                "metric": "wednesday_outstanding",
                "value_millions": self.spec.wednesday_outstanding_millions,
                "weekly_average_millions": self.spec.weekly_average_millions,
                "weekly_average_change_from_prior_week_millions": (
                    self.spec.weekly_average_change_millions
                ),
                "weekly_average_change_from_year_ago_millions": (
                    self.spec.weekly_average_year_change_millions
                ),
                "measurement_boundary": (
                    "Dollar value of foreign currency held under swap agreements, valued at "
                    "the exchange rate used when acquired and to be used when returned to the "
                    "foreign central bank."
                ),
                "html_ascii_crosscheck_verified": True,
                "release_html_url": html_response.request_url,
                "release_ascii_url": txt_response.request_url,
                "release_semantic_sha256": release_semantic_digest,
                "release_time_local": (
                    "16:30:00" if self.spec.exact_stated_time else None
                ),
                "release_timezone": "America/New_York",
                "release_timezone_abbreviation": self.spec.timezone_abbreviation,
                "official_stated_release_at": (
                    stated_release_local.astimezone(UTC).isoformat()
                    if self.spec.exact_stated_time
                    else None
                ),
                "conservative_available_at": available_at.isoformat(),
                "actual_server_publication_log_available": False,
                "availability_method": availability_method,
                "unit": "Millions of Dollars",
            },
        )
        warnings = (
            "March 19 and 26 use the archived HTML's official stated 4:30 p.m. EDT time; April "
            "2 uses the following New York midnight because its archived pair states only the "
            "release date. Neither is an independently measured server timestamp.",
            "The complete HTML and ASCII Table 1 values are cross-checked; current retrieval "
            "hashes do not prove release-time byte identity.",
            "The reported dollar value follows H.4.1's swap-exchange-rate convention and is "
            "not a mark-to-current-market exposure, loss, P&L, or counterparty-risk estimate.",
            "A weekly balance is not transaction-level usage, institution-level allocation, "
            "market impact, causality, forecast skill, adoption, or user impact.",
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
                request_url=_HTTP_URL_ADAPTER.validate_python(txt_response.request_url),
                retrieved_at=txt_retrieved_at,
                status_code=txt_response.status_code,
                content_type=txt_content_type,
                response_sha256=txt_digest,
                response_bytes=len(txt_content),
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
                    sha256=txt_digest,
                    content_type=txt_content_type,
                    content=txt_content,
                ),
            ),
        )

    def _parse_html(self, content: bytes) -> _ParsedRelease:
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("H.4.1 HTML is not valid UTF-8") from error
        parser = _ReleaseHTMLParser()
        try:
            parser.feed(decoded)
            parser.close()
        except (AssertionError, ValueError) as error:
            raise SourceSchemaError("H.4.1 HTML is not structurally valid") from error
        page_text = _normalize(" ".join(parser.text_parts))
        formatted_date = self.release_date.strftime("%B %d, %Y").replace(" 0", " ")
        if self.spec.exact_stated_time:
            stated_time_pattern = re.compile(
                rf"For Release at 4:30 P\.M\. E\s*D\s*T "
                rf"{re.escape(formatted_date[:-6])}\s*,?\s*{self.release_date:%Y}\b",
                re.IGNORECASE,
            )
            if stated_time_pattern.search(page_text) is None:
                raise SourceSchemaError(
                    "H.4.1 HTML official 4:30 p.m. EDT release date or time is missing"
                )
        else:
            release_pattern = re.compile(
                rf"Release Date:\s*(?:Thursday,\s*)?{re.escape(formatted_date)}\b",
                re.IGNORECASE,
            )
            if release_pattern.search(page_text) is None:
                raise SourceSchemaError("H.4.1 HTML release date does not match")
        if _SWAP_FOOTNOTE not in page_text:
            raise SourceSchemaError("H.4.1 HTML liquidity-swap measurement footnote is missing")
        candidates = []
        for table in parser.tables:
            header_text = _normalize(" ".join(cell for row in table[:3] for cell in row))
            if "Averages of daily figures" not in header_text:
                continue
            rows = [
                row
                for row in table
                if len(row) == 5 and _TABLE_ROW_LABEL.fullmatch(row[0]) is not None
            ]
            if rows:
                candidates.append((table, rows))
        if len(candidates) != 1 or len(candidates[0][1]) != 1:
            raise SourceSchemaError("H.4.1 HTML must contain one Table 1 liquidity-swap row")
        table, rows = candidates[0]
        header_text = _normalize(" ".join(cell for row in table[:3] for cell in row))
        wednesday_markers = re.findall(r"Wednesday ([A-Z][a-z]{2} \d{1,2}, \d{4})", header_text)
        week_markers = re.findall(r"Week ended ([A-Z][a-z]{2} \d{1,2}, \d{4})", header_text)
        if len(wednesday_markers) != 1 or len(week_markers) != 1:
            raise SourceSchemaError("H.4.1 HTML must identify one week-ending Wednesday")
        wednesday = _parse_display_date(wednesday_markers[0])
        week_ending = _parse_display_date(week_markers[0])
        if wednesday != week_ending or week_ending >= self.release_date:
            raise SourceSchemaError("H.4.1 HTML week-ending date is inconsistent")
        row = rows[0]
        return {
            "week_ending": week_ending,
            "weekly_average_millions": _positive_integer(row[1], "weekly average"),
            "weekly_average_change_millions": _signed_integer(row[2], "weekly change"),
            "weekly_average_year_change_millions": _signed_integer(row[3], "year change"),
            "wednesday_outstanding_millions": _positive_integer(
                row[4], "Wednesday outstanding"
            ),
        }

    def _parse_txt(self, content: bytes) -> _ParsedRelease:
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("H.4.1 ASCII release is not valid UTF-8") from error
        text = _normalize(decoded)
        formatted_date = self.release_date.strftime("%B %d, %Y").replace(" 0", " ")
        if "FEDERAL RESERVE statistical release" not in text or formatted_date not in text:
            raise SourceSchemaError("H.4.1 ASCII release identity does not match")
        if "Factors Affecting Reserve Balances of Depository Institutions" not in text:
            raise SourceSchemaError("H.4.1 ASCII title is missing")
        if _SWAP_FOOTNOTE not in text:
            raise SourceSchemaError("H.4.1 ASCII liquidity-swap measurement footnote is missing")
        matches = [
            match
            for line in decoded.splitlines()
            if (match := _TXT_TABLE_ROW.fullmatch(line)) is not None
        ]
        if len(matches) != 1:
            raise SourceSchemaError("H.4.1 ASCII must contain one Table 1 liquidity-swap row")
        match = matches[0]
        week_label = self.spec.week_ending.strftime("%b %d, %Y").replace(" 0", " ")
        if (
            f"Wednesday {week_label}" not in text
            or "Week ended Change from week ended" not in text
            or text.count(week_label) < 2
        ):
            raise SourceSchemaError("H.4.1 ASCII week-ending header does not match")
        weekly_sign = -1 if match.group(2) == "-" else 1
        year_sign = -1 if match.group(4) == "-" else 1
        return {
            "week_ending": self.spec.week_ending,
            "weekly_average_millions": _positive_integer(match.group(1), "weekly average"),
            "weekly_average_change_millions": weekly_sign
            * _positive_integer(match.group(3), "weekly change magnitude"),
            "weekly_average_year_change_millions": year_sign
            * _positive_integer(match.group(5), "year change magnitude"),
            "wednesday_outstanding_millions": _positive_integer(
                match.group(6), "Wednesday outstanding"
            ),
        }

    def _validate_response_url(self, response_url: str, *, kind: str) -> None:
        parsed = urlparse(response_url)
        filename = "h41.htm" if kind == "html" else "H41.TXT"
        expected_path = f"/releases/h41/{self.release_date:%Y%m%d}/{filename}"
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("H.4.1 response URL does not match the requested release")


def _expected_parsed(spec: _ReleaseSpec) -> _ParsedRelease:
    return {
        "week_ending": spec.week_ending,
        "weekly_average_millions": spec.weekly_average_millions,
        "weekly_average_change_millions": spec.weekly_average_change_millions,
        "weekly_average_year_change_millions": spec.weekly_average_year_change_millions,
        "wednesday_outstanding_millions": spec.wednesday_outstanding_millions,
    }


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


def _hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
