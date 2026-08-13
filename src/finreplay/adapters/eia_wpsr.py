"""Archived EIA Weekly Petroleum Status Report commercial-crude adapter."""

from __future__ import annotations

import csv
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from io import BytesIO, StringIO
from typing import TypedDict
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
_MILLION_BARRELS = re.compile(r"^-?[0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{3}$")
_PDF_MILLION_BARRELS = re.compile(r"^-?[0-9]{1,3}(?:,[0-9]{3})*\.[0-9]$")
_VERIFIED_RELEASES = {
    date(2020, 4, 8): (date(2020, 4, 3), date(2020, 3, 27)),
    date(2020, 4, 15): (date(2020, 4, 10), date(2020, 4, 3)),
    date(2020, 4, 22): (date(2020, 4, 17), date(2020, 4, 10)),
}


class _ParsedStocks(TypedDict):
    value_thousand_barrels: int
    prior_value_thousand_barrels: int
    reported_difference_thousand_barrels: int
    value_million_barrels: str
    prior_value_million_barrels: str
    reported_difference_million_barrels: str


class EIAWPSRCommercialCrudeStocksAdapter:
    """Retrieve one fixed EIA WPSR Table 4 CSV plus its archived release PDF."""

    availability_rule = (
        "The date-stamped archived WPSR PDF identifies its release date and states that CSV and "
        "XLS tables are posted after 10:30 a.m. Eastern on Wednesdays. Because that wording does "
        "not establish an exact publication instant, FinReplay permits the paired archived CSV "
        "and PDF bytes only from 00:00 America/New_York on the next local calendar day. Both "
        "official Last-Modified headers must fall on the stated release date and before that "
        "conservative knowledge boundary."
    )
    metadata = AdapterMetadata(
        adapter_id="eia.wpsr.archived_commercial_crude_stocks",
        title="EIA archived WPSR U.S. commercial crude stocks excluding SPR",
        publisher="U.S. Energy Information Administration",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.eia.gov/petroleum/supply/weekly/schedule.php"
        ),
        allowed_hosts=("www.eia.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved 2020 archive dates sequentially; do "
            "not crawl or enumerate the Weekly Petroleum Status Report archive."
        ),
        pagination_policy=(
            "Each release uses one complete Table 4 CSV and one complete WPSR PDF without "
            "pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each date-stamped CSV/PDF pair is content-addressed as one archived release "
            "snapshot. A later WPSR or current history series never overwrites an earlier pair."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "EIA states that its government publications and data are reusable with "
            "acknowledgment, but full archived PDFs and CSVs remain in local content-addressed "
            "storage because reports may contain protected third-party items and the EIA logo. "
            "Repository scenarios retain only the minimal Table 4 fact, attribution, URLs, and "
            "hashes."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, release_date: date) -> None:
        if release_date not in _VERIFIED_RELEASES:
            raise ValueError("release date is not in the verified EIA WPSR calendar")
        self.http = http
        self.release_date = release_date
        self.week_ending, self.prior_week_ending = _VERIFIED_RELEASES[release_date]
        base = (
            "https://www.eia.gov/petroleum/supply/weekly/archive/"
            f"{release_date:%Y}/{release_date:%Y_%m_%d}"
        )
        self.csv_endpoint = f"{base}/csv/table4.csv"
        self.pdf_endpoint = f"{base}/pdf/wpsrall.pdf"

    def fetch(self) -> AdapterBatch:
        csv_response, csv_content, csv_retrieved_at = self.http.get(
            self.csv_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        pdf_response, pdf_content, pdf_retrieved_at = self.http.get(
            self.pdf_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(csv_response.request_url, kind="csv")
        self._validate_response_url(pdf_response.request_url, kind="pdf")
        csv_content_type = csv_response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if csv_content_type not in {"application/octet-stream", "text/csv"}:
            raise SourceSchemaError(
                f"unexpected EIA WPSR Table 4 content type: {csv_content_type!r}"
            )
        pdf_content_type = pdf_response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if pdf_content_type != "application/pdf":
            raise SourceSchemaError(
                f"unexpected EIA WPSR report content type: {pdf_content_type!r}"
            )
        available_at = datetime.combine(
            self.release_date + timedelta(days=1),
            time.min,
            tzinfo=_NEW_YORK,
        ).astimezone(UTC)
        csv_last_modified = self._last_modified(
            csv_response.headers.get("Last-Modified"),
            kind="CSV",
            available_at=available_at,
        )
        pdf_last_modified = self._last_modified(
            pdf_response.headers.get("Last-Modified"),
            kind="PDF",
            available_at=available_at,
        )
        parsed = self._parse_csv(csv_content)
        self._validate_pdf(pdf_content, parsed)
        retrieved_at = max(csv_retrieved_at, pdf_retrieved_at)
        if retrieved_at < available_at:
            raise SourceSchemaError("selected EIA WPSR release is not yet conservatively knowable")
        csv_digest = source_response_sha256(csv_content)
        pdf_digest = source_response_sha256(pdf_content)
        vintage_as_of = max(csv_last_modified, pdf_last_modified)
        source_version = (
            f"EIA-WPSR:{self.release_date.isoformat()}:{self.week_ending.isoformat()}:"
            f"csv:{csv_digest[:20]}:pdf:{pdf_digest[:20]}"
        )
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(csv_response.request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=csv_digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            vintage_as_of=vintage_as_of,
            redistribution_note=self.metadata.redistribution_note,
        )
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.release_date:%Y%m%d}:"
                "commercial_crude_excluding_spr"
            ),
            entity_id="eia_series:weekly_us_commercial_crude_stocks_excluding_spr",
            source=source,
            interval=BitemporalInterval(
                valid_from=datetime.combine(self.week_ending, time.min, tzinfo=UTC),
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
                "week_ending": self.week_ending.isoformat(),
                "prior_week_ending": self.prior_week_ending.isoformat(),
                "metric": "commercial_crude_stocks_excluding_spr",
                "value_thousand_barrels": parsed["value_thousand_barrels"],
                "prior_value_thousand_barrels": parsed["prior_value_thousand_barrels"],
                "reported_difference_thousand_barrels": parsed[
                    "reported_difference_thousand_barrels"
                ],
                "reported_value_million_barrels": parsed["value_million_barrels"],
                "reported_prior_value_million_barrels": parsed[
                    "prior_value_million_barrels"
                ],
                "reported_difference_million_barrels": parsed[
                    "reported_difference_million_barrels"
                ],
                "unit": "Thousand Barrels",
                "table": "Weekly Petroleum Status Report Table 4",
                "arithmetic_verified": True,
                "release_pdf_url": pdf_response.request_url,
                "release_pdf_sha256": pdf_digest,
                "csv_last_modified_at": csv_last_modified.isoformat(),
                "pdf_last_modified_at": pdf_last_modified.isoformat(),
                "availability_method": "official_release_date_next_local_midnight",
            },
        )
        warnings = (
            "The next-local-midnight timestamp is a conservative knowledge bound, not an exact "
            "publication instant.",
            "The exact thousand-barrel value comes from archived Table 4 CSV; the paired PDF "
            "corroborates release identity, schedule language, and rounded Table 4 values.",
            "Only the explicitly verified three-date April 2020 calendar is supported.",
            "Full archived CSV and PDF bytes remain local download evidence.",
        )
        receipts = (
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(csv_response.request_url),
                retrieved_at=csv_retrieved_at,
                status_code=csv_response.status_code,
                content_type=csv_content_type,
                response_sha256=csv_digest,
                response_bytes=len(csv_content),
                record_count=1,
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
                record_count=0,
                source_version=source_version,
                temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
        )
        artifacts = (
            RawArtifact(
                sha256=csv_digest,
                content_type=csv_content_type,
                content=csv_content,
            ),
            RawArtifact(
                sha256=pdf_digest,
                content_type=pdf_content_type,
                content=pdf_content,
            ),
        )
        return AdapterBatch(records=(record,), receipts=receipts, artifacts=artifacts)

    def _parse_csv(self, content: bytes) -> _ParsedStocks:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("EIA WPSR Table 4 CSV is not valid UTF-8") from error
        if "\x00" in text:
            raise SourceSchemaError("EIA WPSR Table 4 CSV contains a NUL byte")
        try:
            rows = list(csv.reader(StringIO(text, newline=""), strict=True))
        except csv.Error as error:
            raise SourceSchemaError("EIA WPSR Table 4 CSV could not be parsed") from error
        if not rows:
            raise SourceSchemaError("EIA WPSR Table 4 CSV is empty")
        expected_header = [
            "STUB_1",
            _display_csv_date(self.week_ending),
            _display_csv_date(self.prior_week_ending),
            "Difference",
            _display_csv_date(self.week_ending - timedelta(days=364)),
            "Percent Change",
            _display_csv_date(self.week_ending - timedelta(days=728)),
            "Percent Change",
        ]
        if rows[0] != expected_header:
            raise SourceSchemaError("EIA WPSR Table 4 header or comparison dates do not match")
        required_labels = {
            "Crude Oil",
            "Commercial (Excluding SPR)",
            "SPR",
            "Total Stocks (Excluding SPR)",
        }
        labels = [row[0] for row in rows[1:] if row]
        if not required_labels.issubset(labels):
            raise SourceSchemaError("EIA WPSR Table 4 required stock rows are missing")
        matches = [row for row in rows[1:] if row and row[0] == "Commercial (Excluding SPR)"]
        if len(matches) != 1 or len(matches[0]) != 8:
            raise SourceSchemaError(
                "EIA WPSR Table 4 must contain one eight-column commercial-crude row"
            )
        current_raw, prior_raw, difference_raw = matches[0][1:4]
        current = _thousand_barrels(current_raw, "current commercial-crude stock")
        prior = _thousand_barrels(prior_raw, "prior commercial-crude stock")
        difference = _thousand_barrels(difference_raw, "commercial-crude stock difference")
        if current <= 0 or prior <= 0 or max(current, prior, abs(difference)) > 10_000_000:
            raise SourceSchemaError("EIA WPSR commercial-crude value is outside supported range")
        if current - prior != difference:
            raise SourceSchemaError("EIA WPSR commercial-crude values do not reconcile")
        return {
            "value_thousand_barrels": current,
            "prior_value_thousand_barrels": prior,
            "reported_difference_thousand_barrels": difference,
            "value_million_barrels": current_raw,
            "prior_value_million_barrels": prior_raw,
            "reported_difference_million_barrels": difference_raw,
        }

    def _validate_pdf(self, content: bytes, parsed: _ParsedStocks) -> None:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("EIA WPSR report is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 62:
                raise SourceSchemaError("EIA WPSR archive report must contain exactly 62 pages")
            release_text = reader.pages[1].extract_text()
            table_text = reader.pages[8].extract_text()
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("EIA WPSR archive PDF could not be parsed") from error
        if not isinstance(release_text, str) or not release_text.strip():
            raise SourceSchemaError("EIA WPSR release page has no extractable text")
        if not isinstance(table_text, str) or not table_text.strip():
            raise SourceSchemaError("EIA WPSR Table 4 page has no extractable text")
        release = " ".join(release_text.split())
        display_release_date = (
            f"{self.release_date:%B} {self.release_date.day}, {self.release_date:%Y}"
        )
        release_markers = (
            "EIA DATA ARE",
            "ELECTRONIC FORM",
            "The tables in the Weekly Petroleum Status Report (WPSR) are posted to the web site "
            "after 10:30 a.m. Eastern Standard Time (EST) on Wednesdays in CSV and XLS formats.",
            "For some weeks that include holidays, posting is delayed by one day.",
            f"Release Date: {display_release_date}",
        )
        if any(marker not in release for marker in release_markers):
            raise SourceSchemaError("EIA WPSR PDF release identity or schedule text does not match")
        table = " ".join(table_text.split())
        table_markers = (
            "Table 4. Stocks of Crude Oil by PAD District",
            "Stocks of Petroleum Products",
            "U.S. Totals",
            "(Million Barrels)",
            "Current Week",
            _display_csv_date(self.week_ending),
        )
        if any(marker not in table for marker in table_markers):
            raise SourceSchemaError("EIA WPSR PDF Table 4 identity or current week does not match")
        row_pattern = re.compile(
            r"Commercial \(Excluding SPR\)\d+\s+\.+\s+"
            r"(-?[0-9][0-9,]*\.[0-9])\s+"
            r"(-?[0-9][0-9,]*\.[0-9])\s+"
            r"(-?[0-9][0-9,]*\.[0-9])"
        )
        matches = row_pattern.findall(table)
        if len(matches) != 1 or any(
            _PDF_MILLION_BARRELS.fullmatch(value) is None for value in matches[0]
        ):
            raise SourceSchemaError(
                "EIA WPSR PDF Table 4 must contain one valid commercial-crude row"
            )
        expected = (
            _rounded_tenth(parsed["value_million_barrels"]),
            _rounded_tenth(parsed["prior_value_million_barrels"]),
            _rounded_tenth(parsed["reported_difference_million_barrels"]),
        )
        actual = tuple(Decimal(value.replace(",", "")) for value in matches[0])
        if actual != expected:
            raise SourceSchemaError(
                "EIA WPSR PDF rounded commercial-crude values do not match archived CSV"
            )

    def _last_modified(
        self,
        raw_value: str | None,
        *,
        kind: str,
        available_at: datetime,
    ) -> datetime:
        if raw_value is None:
            raise SourceSchemaError(f"EIA WPSR {kind} response lacks Last-Modified")
        try:
            parsed = parsedate_to_datetime(raw_value)
        except (TypeError, ValueError) as error:
            raise SourceSchemaError(f"EIA WPSR {kind} Last-Modified is invalid") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise SourceSchemaError(f"EIA WPSR {kind} Last-Modified lacks a timezone")
        normalized = parsed.astimezone(UTC)
        if normalized.date() != self.release_date or normalized >= available_at:
            raise SourceSchemaError(
                f"EIA WPSR {kind} Last-Modified falls outside the verified release date"
            )
        return normalized

    def _validate_response_url(self, response_url: str, *, kind: str) -> None:
        parsed = urlparse(response_url)
        suffix = "csv/table4.csv" if kind == "csv" else "pdf/wpsrall.pdf"
        expected_path = (
            "/petroleum/supply/weekly/archive/"
            f"{self.release_date:%Y}/{self.release_date:%Y_%m_%d}/{suffix}"
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError(f"EIA WPSR {kind.upper()} response URL does not match request")


def _thousand_barrels(raw_value: str, label: str) -> int:
    if _MILLION_BARRELS.fullmatch(raw_value) is None:
        raise SourceSchemaError(f"EIA WPSR {label} must have three decimal places")
    try:
        value = Decimal(raw_value.replace(",", "")) * 1_000
    except InvalidOperation as error:
        raise SourceSchemaError(f"EIA WPSR {label} is not decimal") from error
    if not value.is_finite() or value != value.to_integral_value():
        raise SourceSchemaError(f"EIA WPSR {label} does not convert to whole thousand barrels")
    return int(value)


def _rounded_tenth(raw_value: str) -> Decimal:
    return Decimal(raw_value.replace(",", "")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _display_csv_date(value: date) -> str:
    return f"{value.month}/{value.day}/{value:%y}"
