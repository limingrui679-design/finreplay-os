"""ALFRED native-vintage GDP adapter with conservative knowledge timing."""

from __future__ import annotations

import csv
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from io import StringIO
from urllib.parse import parse_qs, urlparse

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
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_GDP_VALUE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


class ALFREDGDPVintageAdapter:
    """Download one explicitly selected historical snapshot of U.S. nominal GDP."""

    series_id = "GDP"
    endpoint = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
    availability_lag = timedelta(days=2)
    availability_rule = (
        "ALFRED identifies only a calendar vintage date, not an intraday availability time. "
        "FinReplay therefore permits use only from 00:00 UTC two calendar days later. This is "
        "a deterministic conservative knowledge bound, not a claimed release timestamp."
    )
    metadata = AdapterMetadata(
        adapter_id="fred.alfred.vintage_gdp",
        title="ALFRED native-vintage U.S. Gross Domestic Product",
        publisher="Federal Reserve Bank of St. Louis",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://alfred.stlouisfed.org/help/downloaddata"
        ),
        allowed_hosts=("alfred.stlouisfed.org",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Make sequential, low-volume requests for explicitly named vintage dates. This "
            "scenario connector does not crawl, enumerate, or bulk-extract the ALFRED catalog."
        ),
        pagination_policy=(
            "One bounded CSV download covers the requested observation interval for one vintage; "
            "there is no pagination and no implicit request for additional vintages."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each request selects one native ALFRED vintage. Later vintages may revise earlier "
            "observations, so the vintage date and response hash are part of every fact identity."
        ),
        temporal_coverage=TemporalCoverage.VINTAGE_NATIVE,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Retain the downloaded response only in the local content-addressed store. Attribute "
            "ALFRED/FRED and the underlying U.S. Bureau of Economic Analysis series; do not "
            "redistribute the raw response or imply that repository code owns the source data."
        ),
    )

    def __init__(
        self,
        http: SafeHttpClient,
        *,
        vintage_date: date,
        observation_start: date,
        observation_end: date,
    ) -> None:
        if observation_end < observation_start:
            raise ValueError("observation_end must not precede observation_start")
        self.http = http
        self.vintage_date = vintage_date
        self.observation_start = observation_start
        self.observation_end = observation_end

    def fetch(self) -> AdapterBatch:
        params = {
            "id": self.series_id,
            "cosd": self.observation_start.isoformat(),
            "coed": self.observation_end.isoformat(),
            "vintage_date": self.vintage_date.isoformat(),
        }
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
            params=params,
        )
        self._validate_response_url(response.request_url, params)
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"application/csv", "text/csv"}:
            raise SourceSchemaError(f"unexpected ALFRED content type: {content_type!r}")
        rows = self._parse_csv(content)
        digest = source_response_sha256(content)
        vintage_as_of = datetime.combine(self.vintage_date, time.min, tzinfo=UTC)
        conservative_available_at = vintage_as_of + self.availability_lag
        if retrieved_at < conservative_available_at:
            raise SourceSchemaError("selected ALFRED vintage is not yet conservatively knowable")
        source_version = (
            f"{self.series_id}:vintage:{self.vintage_date.isoformat()}:"
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
            temporal_coverage=TemporalCoverage.VINTAGE_NATIVE,
            vintage_as_of=vintage_as_of,
            redistribution_note=self.metadata.redistribution_note,
        )
        records = tuple(
            BitemporalRecord(
                record_id=(
                    f"{self.metadata.adapter_id}:{self.series_id}:"
                    f"{self.vintage_date:%Y%m%d}:{observation_date.isoformat()}"
                ),
                entity_id=f"fred_series:{self.series_id}",
                source=source,
                interval=BitemporalInterval(
                    valid_from=datetime.combine(observation_date, time.min, tzinfo=UTC),
                    published_at=conservative_available_at,
                    available_at=conservative_available_at,
                    ingested_at=retrieved_at,
                    availability_rule=self.availability_rule,
                    availability_confidence=1.0,
                ),
                evidence_class=EvidenceClass.REPORTED,
                payload_schema_version="1.0.0",
                payload={
                    "series_id": self.series_id,
                    "series_title": "Gross Domestic Product",
                    "unit": "Billions of Dollars",
                    "frequency": "Quarterly",
                    "seasonal_adjustment": "Seasonally Adjusted Annual Rate",
                    "vintage_date": self.vintage_date.isoformat(),
                    "observation_date": observation_date.isoformat(),
                    "value": value,
                    "availability_method": "vintage_date_plus_two_calendar_days_utc",
                },
            )
            for observation_date, value in rows
        )
        warnings = (
            "ALFRED vintage dates are date-granular; the recorded knowledge time is a conservative "
            "two-day bound and is not an intraday publication timestamp.",
            "The raw ALFRED response is retained locally as download-only evidence and is not "
            "redistributed with the repository.",
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
            temporal_coverage=TemporalCoverage.VINTAGE_NATIVE,
            historical_replay_eligible=True,
            warnings=warnings,
        )
        artifact = RawArtifact(sha256=digest, content_type=content_type, content=content)
        return AdapterBatch(records=records, receipts=(receipt,), artifacts=(artifact,))

    def _parse_csv(self, content: bytes) -> tuple[tuple[date, str], ...]:
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("ALFRED CSV is not valid UTF-8") from error
        reader = csv.reader(StringIO(decoded, newline=""), strict=True)
        try:
            all_rows = list(reader)
        except csv.Error as error:
            raise SourceSchemaError("ALFRED response is not valid CSV") from error
        expected_header = ["observation_date", f"{self.series_id}_{self.vintage_date:%Y%m%d}"]
        if not all_rows or all_rows[0] != expected_header:
            raise SourceSchemaError(
                f"ALFRED header must exactly equal {expected_header!r}"
            )
        if len(all_rows) == 1:
            raise SourceSchemaError("ALFRED response contains no observations")
        parsed: list[tuple[date, str]] = []
        prior: date | None = None
        for position, row in enumerate(all_rows[1:], start=2):
            if len(row) != 2:
                raise SourceSchemaError(f"ALFRED CSV row {position} must contain two fields")
            raw_date, raw_value = row
            if _ISO_DATE.fullmatch(raw_date) is None:
                raise SourceSchemaError(
                    f"ALFRED row {position} observation_date must use YYYY-MM-DD"
                )
            try:
                observation_date = date.fromisoformat(raw_date)
            except ValueError as error:
                raise SourceSchemaError(
                    f"ALFRED row {position} observation_date is not a valid date"
                ) from error
            if not self.observation_start <= observation_date <= self.observation_end:
                raise SourceSchemaError(
                    f"ALFRED row {position} falls outside the requested observation interval"
                )
            if prior is not None and observation_date <= prior:
                raise SourceSchemaError("ALFRED observation dates must be unique and ascending")
            if _GDP_VALUE.fullmatch(raw_value) is None:
                raise SourceSchemaError(
                    f"ALFRED row {position} GDP value must be a positive decimal"
                )
            try:
                numeric = Decimal(raw_value)
            except InvalidOperation as error:
                raise SourceSchemaError(
                    f"ALFRED row {position} GDP value must be a positive decimal"
                ) from error
            if not numeric.is_finite() or numeric <= 0:
                raise SourceSchemaError(
                    f"ALFRED row {position} GDP value must be finite and positive"
                )
            parsed.append((observation_date, raw_value))
            prior = observation_date
        return tuple(parsed)

    def _validate_response_url(self, response_url: str, expected: dict[str, str]) -> None:
        parsed = urlparse(response_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != "/graph/alfredgraph.csv"
            or parsed.fragment
        ):
            raise SourceSchemaError("ALFRED response URL does not match the approved endpoint")
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        expected_query = {key: [value] for key, value in expected.items()}
        if query != expected_query:
            raise SourceSchemaError("ALFRED response URL does not match the requested vintage")
