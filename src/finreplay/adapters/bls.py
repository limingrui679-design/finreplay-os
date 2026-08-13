"""BLS Public Data API adapter with revision-safe knowledge time."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import HttpUrl, TypeAdapter

from finreplay.adapters.base import (
    AdapterBatch,
    AdapterMetadata,
    AuthenticationMode,
    FetchReceipt,
    RawArtifact,
    SafeHttpClient,
    SourceSchemaError,
    require_json_object,
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
_YEAR_PATTERN = re.compile(r"^[0-9]{4}$")
_PERIOD_PATTERN = re.compile(r"^M(0[1-9]|1[0-3])$")
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class BLSCPIUAllItemsAdapter:
    """Retrieve the fixed CPI-U all-items monthly series from API version 1."""

    series_id = "CUUR0000SA0"
    endpoint = f"https://api.bls.gov/publicAPI/v1/timeseries/data/{series_id}"
    metadata = AdapterMetadata(
        adapter_id="bls.public_data.cpi_u_all_items",
        title="BLS CPI-U All Items, U.S. city average, not seasonally adjusted",
        publisher="U.S. Bureau of Labor Statistics",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.bls.gov/developers/api_signature_v1.htm"
        ),
        allowed_hosts=("api.bls.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Use the unregistered version-1 single-series GET contract: at most 25 daily "
            "queries, 25 series per query, 10 years per query, and 50 requests per 10 seconds. "
            "This adapter makes one sequential request."
        ),
        pagination_policy=(
            "The official single-series GET returns the latest three-year window in one bounded "
            "response; it has no pagination and this adapter never fabricates older pages."
        ),
        availability_rule=(
            "BLS documents an API lag and does not provide the publication or revision timestamp "
            "for each returned observation. The exact current value is therefore knowable only "
            "at the recorded retrieval time."
        ),
        revision_behavior=(
            "CPI observations can be corrected or revised. The version-1 response is a current "
            "window, so every response is content-addressed and no current value is backdated as "
            "a prior vintage."
        ),
        temporal_coverage=TemporalCoverage.LATEST_ONLY,
        license_class=LicenseClass.REDISTRIBUTABLE,
        redistribution_note=(
            "BLS-published material is public domain except identified third-party photographs "
            "or illustrations. Cite the Bureau of Labor Statistics and the API retrieval date; "
            "do not use the protected BLS emblem."
        ),
    )

    def __init__(self, http: SafeHttpClient) -> None:
        self.http = http

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"application/json", "text/json"}:
            raise SourceSchemaError(f"unexpected BLS content type: {content_type!r}")
        try:
            root = require_json_object(response.json(), "BLS Public Data response")
        except (TypeError, ValueError) as error:
            raise SourceSchemaError("BLS Public Data response is not valid JSON") from error
        rows = self._extract_rows(root)
        digest = source_response_sha256(content)
        parsed = [
            item
            for position, value in enumerate(rows)
            if (item := self._parse_row(value, position)) is not None
        ]
        if not parsed:
            raise SourceSchemaError("BLS response contains no monthly M01 through M12 observations")
        periods = [item[1] for item in parsed]
        latest_period = max(periods)
        source_version = (
            f"{self.series_id}:latest-through:{latest_period:%Y-%m}:sha256:{digest[:24]}"
        )
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.LATEST_ONLY,
            redistribution_note=self.metadata.redistribution_note,
        )
        records: list[BitemporalRecord] = []
        identities: set[str] = set()
        for row, valid_from in parsed:
            identity = f"{row['year']}-{row['period']}"
            if identity in identities:
                raise SourceSchemaError(f"duplicate BLS series period: {identity}")
            identities.add(identity)
            records.append(
                BitemporalRecord(
                    record_id=f"{self.metadata.adapter_id}:{identity}",
                    entity_id=f"bls_series:{self.series_id}",
                    source=source,
                    interval=BitemporalInterval(
                        valid_from=valid_from,
                        published_at=retrieved_at,
                        available_at=retrieved_at,
                        ingested_at=retrieved_at,
                        availability_rule=self.metadata.availability_rule,
                        availability_confidence=1.0,
                    ),
                    evidence_class=EvidenceClass.REPORTED,
                    payload_schema_version="1.0.0",
                    payload=row,
                )
            )
        warning = (
            "Latest-only BLS CPI-U window: observation dates are economic periods, not proof of "
            "when this exact value or correction first became public."
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
            temporal_coverage=TemporalCoverage.LATEST_ONLY,
            historical_replay_eligible=False,
            warnings=(warning,),
        )
        artifact = RawArtifact(sha256=digest, content_type=content_type, content=content)
        return AdapterBatch(records=tuple(records), receipts=(receipt,), artifacts=(artifact,))

    def _extract_rows(self, root: dict[str, Any]) -> list[Any]:
        if root.get("status") != "REQUEST_SUCCEEDED":
            raise SourceSchemaError("BLS request status is not REQUEST_SUCCEEDED")
        response_time = root.get("responseTime")
        if (
            not isinstance(response_time, int)
            or isinstance(response_time, bool)
            or response_time < 0
        ):
            raise SourceSchemaError("BLS responseTime must be a non-negative integer")
        messages = root.get("message")
        if not isinstance(messages, list) or not all(isinstance(item, str) for item in messages):
            raise SourceSchemaError("BLS message must be a list of strings")
        if messages:
            raise SourceSchemaError(f"BLS returned source messages: {messages!r}")
        results = require_json_object(root.get("Results"), "BLS Results")
        series = results.get("series")
        if not isinstance(series, list) or len(series) != 1:
            raise SourceSchemaError("BLS Results.series must contain exactly one series")
        selected = require_json_object(series[0], "BLS Results.series[0]")
        if selected.get("seriesID") != self.series_id:
            raise SourceSchemaError(
                f"BLS returned series {selected.get('seriesID')!r}, expected {self.series_id!r}"
            )
        data = selected.get("data")
        if not isinstance(data, list) or not data:
            raise SourceSchemaError("BLS selected series data must be a non-empty list")
        return data

    @staticmethod
    def _parse_row(value: Any, position: int) -> tuple[dict[str, Any], datetime] | None:
        row = require_json_object(value, f"BLS series row[{position}]")
        required = {"year", "period", "periodName", "value", "footnotes"}
        missing = required - set(row)
        if missing:
            raise SourceSchemaError(f"BLS series row is missing fields: {sorted(missing)}")
        year = row["year"]
        period = row["period"]
        if not isinstance(year, str) or _YEAR_PATTERN.fullmatch(year) is None:
            raise SourceSchemaError("BLS year must use YYYY")
        if not isinstance(period, str) or _PERIOD_PATTERN.fullmatch(period) is None:
            raise SourceSchemaError("BLS period must use monthly M01-M12 or annual M13")
        month = int(period[1:])
        expected_name = "Annual" if month == 13 else _MONTH_NAMES[month - 1]
        if row["periodName"] != expected_name:
            raise SourceSchemaError("BLS periodName does not match period")
        footnotes = row["footnotes"]
        if not isinstance(footnotes, list):
            raise SourceSchemaError("BLS footnotes must be a list")
        note_present = False
        for index, value in enumerate(footnotes):
            note = require_json_object(value, f"BLS footnotes[{index}]")
            for field in ("code", "text"):
                if field in note and not isinstance(note[field], str):
                    raise SourceSchemaError(f"BLS footnote {field} must be text")
            note_present = note_present or any(
                isinstance(note.get(field), str) and bool(note[field].strip())
                for field in ("code", "text")
            )
        raw_value = row["value"]
        if raw_value == "-":
            if not note_present:
                raise SourceSchemaError("unavailable BLS value requires an explanatory footnote")
        else:
            try:
                numeric = Decimal(str(raw_value))
            except (InvalidOperation, ValueError) as error:
                raise SourceSchemaError(
                    "BLS value must be numeric or '-' with a footnote"
                ) from error
            if not numeric.is_finite() or numeric < 0:
                raise SourceSchemaError("BLS CPI value must be finite and non-negative")
        if "latest" in row and row["latest"] not in {"true", "false"}:
            raise SourceSchemaError("BLS latest marker must be 'true' or 'false'")
        # BLS includes M13 annual averages in this response. This product is explicitly monthly,
        # and the official BLS API examples select M01-M12, so preserve M13 in the raw artifact
        # while excluding it from the normalized monthly fact stream.
        if month == 13:
            return None
        try:
            valid_from = datetime(int(year), month, 1, tzinfo=UTC)
        except ValueError as error:
            raise SourceSchemaError("BLS year and period are not a valid calendar month") from error
        return dict(row), valid_from
