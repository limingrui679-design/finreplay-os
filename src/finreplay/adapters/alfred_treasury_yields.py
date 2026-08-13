"""ALFRED native-vintage Treasury-yield adapter with conservative timing."""

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
_YIELD_VALUE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$")
_SERIES = {
    "DGS2": (
        "Market Yield on U.S. Treasury Securities at 2-Year Constant Maturity",
        2,
    ),
    "DGS10": (
        "Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity",
        10,
    ),
}


class ALFREDTreasuryYieldVintageAdapter:
    """Download one selected DGS2 or DGS10 observation from one ALFRED vintage."""

    endpoint = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
    availability_lag = timedelta(days=2)
    availability_rule = (
        "ALFRED identifies only a calendar vintage date, not an intraday availability time. "
        "FinReplay therefore permits use only from 00:00 UTC two calendar days later. This is "
        "a deterministic conservative knowledge bound, not a claimed H.15 release timestamp."
    )
    metadata = AdapterMetadata(
        adapter_id="fred.alfred.vintage_treasury_yield",
        title="ALFRED native-vintage 2-year and 10-year Treasury constant-maturity yields",
        publisher="Federal Reserve Bank of St. Louis",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://alfred.stlouisfed.org/help"
        ),
        allowed_hosts=("alfred.stlouisfed.org",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Make sequential, low-volume requests only for the six explicitly named series, "
            "observation, and vintage combinations used by the scenario."
        ),
        pagination_policy=(
            "Each bounded CSV request contains exactly one selected observation and has no "
            "pagination or implicit request for other vintages."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Every request selects one native ALFRED vintage. The series ID, vintage date, "
            "observation date, and response hash remain part of each fact identity."
        ),
        temporal_coverage=TemporalCoverage.VINTAGE_NATIVE,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Retain raw ALFRED CSV only in local content-addressed storage. Attribute FRED/ALFRED "
            "and the underlying Board of Governors H.15 series; do not redistribute the raw "
            "response or imply ownership of source data."
        ),
    )

    def __init__(
        self,
        http: SafeHttpClient,
        *,
        series_id: str,
        vintage_date: date,
        observation_date: date,
    ) -> None:
        if series_id not in _SERIES:
            raise ValueError("series_id must be DGS2 or DGS10")
        if vintage_date < observation_date:
            raise ValueError("vintage_date cannot precede observation_date")
        self.http = http
        self.series_id = series_id
        self.vintage_date = vintage_date
        self.observation_date = observation_date

    def fetch(self) -> AdapterBatch:
        params = {
            "id": self.series_id,
            "cosd": self.observation_date.isoformat(),
            "coed": self.observation_date.isoformat(),
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
            raise SourceSchemaError(
                f"unexpected ALFRED Treasury-yield content type: {content_type!r}"
            )
        value_text, value_basis_points = self._parse_csv(content)
        digest = source_response_sha256(content)
        vintage_as_of = datetime.combine(self.vintage_date, time.min, tzinfo=UTC)
        conservative_available_at = vintage_as_of + self.availability_lag
        if retrieved_at < conservative_available_at:
            raise SourceSchemaError(
                "selected ALFRED Treasury-yield vintage is not yet conservatively knowable"
            )
        source_version = (
            f"{self.series_id}:vintage:{self.vintage_date.isoformat()}:"
            f"observation:{self.observation_date.isoformat()}:sha256:{digest[:24]}"
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
        title, maturity_years = _SERIES[self.series_id]
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.series_id}:"
                f"{self.vintage_date:%Y%m%d}:{self.observation_date.isoformat()}"
            ),
            entity_id=f"fred_series:{self.series_id}",
            source=source,
            interval=BitemporalInterval(
                valid_from=datetime.combine(self.observation_date, time.min, tzinfo=UTC),
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
                "series_title": title,
                "underlying_release": "H.15 Selected Interest Rates",
                "maturity_years": maturity_years,
                "unit": "Basis Points",
                "frequency": "Daily",
                "seasonal_adjustment": "Not Seasonally Adjusted",
                "vintage_date": self.vintage_date.isoformat(),
                "observation_date": self.observation_date.isoformat(),
                "reported_value_percent": value_text,
                "value_basis_points": value_basis_points,
                "availability_method": "vintage_date_plus_two_calendar_days_utc",
            },
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
            temporal_coverage=TemporalCoverage.VINTAGE_NATIVE,
            historical_replay_eligible=True,
            warnings=(
                "ALFRED vintage dates are date-granular; knowledge time is conservatively set two "
                "calendar days later and is not an intraday H.15 publication timestamp.",
                "The yield-curve spread is derived later from separately reported DGS10 and DGS2 "
                "facts; it is not an upstream reported series in this receipt.",
                "Raw ALFRED CSV remains download-only local evidence and is not redistributed.",
            ),
        )
        return AdapterBatch(
            records=(record,),
            receipts=(receipt,),
            artifacts=(
                RawArtifact(sha256=digest, content_type=content_type, content=content),
            ),
        )

    def _parse_csv(self, content: bytes) -> tuple[str, int]:
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceSchemaError("ALFRED Treasury-yield CSV is not valid UTF-8") from error
        reader = csv.reader(StringIO(decoded, newline=""), strict=True)
        try:
            rows = list(reader)
        except csv.Error as error:
            raise SourceSchemaError("ALFRED Treasury-yield response is not valid CSV") from error
        expected_header = [
            "observation_date",
            f"{self.series_id}_{self.vintage_date:%Y%m%d}",
        ]
        if not rows or rows[0] != expected_header:
            raise SourceSchemaError(
                f"ALFRED Treasury-yield header must exactly equal {expected_header!r}"
            )
        if len(rows) != 2 or len(rows[1]) != 2:
            raise SourceSchemaError(
                "ALFRED Treasury-yield response must contain exactly one two-field observation"
            )
        raw_date, raw_value = rows[1]
        if raw_date != self.observation_date.isoformat():
            raise SourceSchemaError(
                "ALFRED Treasury-yield observation date differs from the requested date"
            )
        if _YIELD_VALUE.fullmatch(raw_value) is None:
            raise SourceSchemaError(
                "ALFRED Treasury-yield value must be a decimal with at most two places"
            )
        try:
            numeric = Decimal(raw_value)
        except InvalidOperation as error:
            raise SourceSchemaError(
                "ALFRED Treasury-yield value must be a finite decimal"
            ) from error
        scaled = numeric * 100
        if not numeric.is_finite() or scaled != scaled.to_integral_value():
            raise SourceSchemaError(
                "ALFRED Treasury-yield value must resolve to whole basis points"
            )
        basis_points = int(scaled)
        if not -1_000 <= basis_points <= 10_000:
            raise SourceSchemaError("ALFRED Treasury-yield value is outside the supported range")
        return raw_value, basis_points

    def _validate_response_url(self, response_url: str, expected: dict[str, str]) -> None:
        parsed = urlparse(response_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != "/graph/alfredgraph.csv"
            or parsed.fragment
        ):
            raise SourceSchemaError(
                "ALFRED Treasury-yield response URL does not match the approved endpoint"
            )
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        expected_query = {key: [value] for key, value in expected.items()}
        if query != expected_query:
            raise SourceSchemaError(
                "ALFRED Treasury-yield response URL does not match the requested vintage"
            )
