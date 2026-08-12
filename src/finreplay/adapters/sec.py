"""SEC EDGAR submissions adapters with filing-acceptance knowledge time."""

from __future__ import annotations

import re
from datetime import UTC, datetime
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
_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_HISTORICAL_FILE_PATTERN = re.compile(r"^CIK([0-9]{10})-submissions-([0-9]{3})\.json$")
_RECENT_REQUIRED_COLUMNS = (
    "accessionNumber",
    "filingDate",
    "reportDate",
    "acceptanceDateTime",
    "act",
    "form",
    "fileNumber",
    "filmNumber",
    "items",
    "size",
    "isXBRL",
    "isInlineXBRL",
    "primaryDocument",
    "primaryDocDescription",
)


class SECSubmissionsAdapter:
    """Retrieve a company's current EDGAR submissions index and immutable filing events."""

    metadata = AdapterMetadata(
        adapter_id="sec.edgar.submissions",
        title="SEC EDGAR company submissions",
        publisher="U.S. Securities and Exchange Commission",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"
        ),
        allowed_hosts=("data.sec.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Use an accountable configured User-Agent and remain below SEC fair-access limits; "
            "the adapter is sequential and never retries throttling without a bound."
        ),
        pagination_policy=(
            "The main response contains recent filings plus named historical JSON shards. "
            "Historical shards are retrieved only by the dedicated validated adapter."
        ),
        availability_rule=(
            "EDGAR acceptanceDateTime is the knowledge time for each filing event. Missing or "
            "invalid acceptance timestamps fail closed."
        ),
        revision_behavior=(
            "Filings are immutable accessioned events; amendments are separate accessions rather "
            "than silent replacements. The current index snapshot is content-addressed."
        ),
        temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
        license_class=LicenseClass.REDISTRIBUTABLE,
        redistribution_note=(
            "SEC public filing metadata may be reproduced with attribution and fair-access "
            "compliance; this classification is not legal advice."
        ),
    )

    def __init__(self, http: SafeHttpClient) -> None:
        self.http = http

    def fetch(self, cik: int) -> tuple[AdapterBatch, tuple[str, ...]]:
        normalized_cik = _normalize_cik(cik)
        url = f"https://data.sec.gov/submissions/CIK{normalized_cik}.json"
        response, content, retrieved_at = self.http.get(
            url,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        content_type = _require_json_content_type(response.headers.get("Content-Type", ""))
        try:
            root = require_json_object(response.json(), "SEC submissions response")
        except (ValueError, TypeError) as error:
            raise SourceSchemaError("SEC submissions response is not valid JSON") from error
        returned_cik = str(root.get("cik", "")).zfill(10)
        if returned_cik != normalized_cik:
            raise SourceSchemaError(
                f"SEC returned CIK {returned_cik!r}, expected {normalized_cik!r}"
            )
        filings = require_json_object(root.get("filings"), "SEC submissions filings")
        recent = require_json_object(filings.get("recent"), "SEC submissions filings.recent")
        digest = source_response_sha256(content)
        source_version = f"CIK{normalized_cik}:{digest[:20]}"
        source = _sec_source(
            metadata=self.metadata,
            url=response.request_url,
            retrieved_at=retrieved_at,
            source_version=source_version,
            digest=digest,
        )
        records = _parse_submission_columns(
            recent,
            cik=normalized_cik,
            source=source,
            retrieved_at=retrieved_at,
        )
        historical_files = _parse_historical_files(filings.get("files"), normalized_cik)
        warning = (
            "Acceptance time proves when filing metadata entered EDGAR, not the truth, causal "
            "meaning, or investment value of the filer-provided disclosure."
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
            temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
            historical_replay_eligible=True,
            warnings=(warning,),
        )
        raw = RawArtifact(sha256=digest, content_type=content_type, content=content)
        batch = AdapterBatch(records=tuple(records), receipts=(receipt,), artifacts=(raw,))
        return batch, historical_files


class SECHistoricalSubmissionsAdapter:
    """Retrieve a named historical submissions shard disclosed by the main SEC response."""

    metadata = AdapterMetadata(
        adapter_id="sec.edgar.submissions_historical",
        title="SEC EDGAR historical submissions shards",
        publisher="U.S. Securities and Exchange Commission",
        documentation_url=SECSubmissionsAdapter.metadata.documentation_url,
        allowed_hosts=("data.sec.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=SECSubmissionsAdapter.metadata.rate_limit_policy,
        pagination_policy="One SEC-declared immutable JSON shard per request.",
        availability_rule=SECSubmissionsAdapter.metadata.availability_rule,
        revision_behavior=SECSubmissionsAdapter.metadata.revision_behavior,
        temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
        license_class=LicenseClass.REDISTRIBUTABLE,
        redistribution_note=SECSubmissionsAdapter.metadata.redistribution_note,
    )

    def __init__(self, http: SafeHttpClient) -> None:
        self.http = http

    def fetch(self, *, cik: int, file_name: str) -> AdapterBatch:
        normalized_cik = _normalize_cik(cik)
        match = _HISTORICAL_FILE_PATTERN.fullmatch(file_name)
        if match is None or match.group(1) != normalized_cik:
            raise ValueError("historical file name must match the requested ten-digit CIK")
        url = f"https://data.sec.gov/submissions/{file_name}"
        response, content, retrieved_at = self.http.get(
            url,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        content_type = _require_json_content_type(response.headers.get("Content-Type", ""))
        try:
            columns = require_json_object(response.json(), "SEC historical submissions response")
        except (ValueError, TypeError) as error:
            raise SourceSchemaError("SEC historical submissions shard is not valid JSON") from error
        digest = source_response_sha256(content)
        source = _sec_source(
            metadata=self.metadata,
            url=response.request_url,
            retrieved_at=retrieved_at,
            source_version=f"{file_name}:{digest[:20]}",
            digest=digest,
        )
        records = _parse_submission_columns(
            columns,
            cik=normalized_cik,
            source=source,
            retrieved_at=retrieved_at,
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
            source_version=f"{file_name}:{digest[:20]}",
            temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
            historical_replay_eligible=True,
            warnings=(
                "Historical filing metadata is an accession event, not proof that disclosure "
                "content is accurate or sufficient for an investment decision.",
            ),
        )
        raw = RawArtifact(sha256=digest, content_type=content_type, content=content)
        return AdapterBatch(records=tuple(records), receipts=(receipt,), artifacts=(raw,))


def _parse_submission_columns(
    columns: dict[str, Any],
    *,
    cik: str,
    source: SourceReference,
    retrieved_at: datetime,
) -> list[BitemporalRecord]:
    parsed_columns: dict[str, list[Any]] = {}
    for name in _RECENT_REQUIRED_COLUMNS:
        value = columns.get(name)
        if not isinstance(value, list):
            raise SourceSchemaError(f"SEC submissions column {name} must be a list")
        parsed_columns[name] = value
    lengths = {len(value) for value in parsed_columns.values()}
    if len(lengths) != 1:
        raise SourceSchemaError("SEC submissions columns have unequal lengths")
    count = lengths.pop()
    records: list[BitemporalRecord] = []
    seen_accessions: set[str] = set()
    for position in range(count):
        row = {name: values[position] for name, values in parsed_columns.items()}
        accession = row["accessionNumber"]
        if not isinstance(accession, str) or _ACCESSION_PATTERN.fullmatch(accession) is None:
            raise SourceSchemaError(f"invalid SEC accession at row {position}: {accession!r}")
        if accession in seen_accessions:
            raise SourceSchemaError(f"duplicate SEC accession in response: {accession}")
        seen_accessions.add(accession)
        accepted_at = _parse_sec_datetime(row["acceptanceDateTime"], "acceptanceDateTime")
        filing_date = _parse_sec_date(row["filingDate"], "filingDate")
        report_value = row["reportDate"]
        valid_from = filing_date
        if isinstance(report_value, str) and report_value:
            valid_from = _parse_sec_date(report_value, "reportDate")
        records.append(
            BitemporalRecord(
                record_id=f"sec.edgar.submissions:{cik}:{accession}",
                entity_id=f"sec_cik:{cik}",
                source=source,
                interval=BitemporalInterval(
                    valid_from=valid_from,
                    published_at=accepted_at,
                    available_at=accepted_at,
                    ingested_at=retrieved_at,
                    availability_rule=(
                        "Official EDGAR acceptanceDateTime; amendments remain separate accessions."
                    ),
                    availability_confidence=1.0,
                ),
                evidence_class=EvidenceClass.REPORTED,
                payload_schema_version="1.0.0",
                payload=row,
            )
        )
    return records


def _parse_historical_files(value: Any, cik: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SourceSchemaError("SEC submissions filings.files must be a list")
    names: list[str] = []
    for position, item_value in enumerate(value):
        item = require_json_object(item_value, f"SEC filings.files[{position}]")
        name = item.get("name")
        if not isinstance(name, str):
            raise SourceSchemaError("SEC historical submissions file name is missing")
        match = _HISTORICAL_FILE_PATTERN.fullmatch(name)
        if match is None or match.group(1) != cik:
            raise SourceSchemaError(f"invalid SEC historical submissions file name: {name!r}")
        for integer_field in ("filingCount",):
            if not isinstance(item.get(integer_field), int) or item[integer_field] < 0:
                raise SourceSchemaError(f"SEC historical file {integer_field} is invalid")
        for date_field in ("filingFrom", "filingTo"):
            _parse_sec_date(item.get(date_field), date_field)
        names.append(name)
    if len(set(names)) != len(names):
        raise SourceSchemaError("duplicate SEC historical submissions file names")
    return tuple(names)


def _sec_source(
    *,
    metadata: AdapterMetadata,
    url: str,
    retrieved_at: datetime,
    source_version: str,
    digest: str,
) -> SourceReference:
    return SourceReference(
        source_id=metadata.adapter_id,
        publisher=metadata.publisher,
        url=_HTTP_URL_ADAPTER.validate_python(url),
        retrieved_at=retrieved_at,
        source_version=source_version,
        sha256=digest,
        license_class=metadata.license_class,
        temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
        vintage_as_of=retrieved_at,
        redistribution_note=metadata.redistribution_note,
    )


def _normalize_cik(cik: int) -> str:
    if not 1 <= cik <= 9_999_999_999:
        raise ValueError("CIK must be a positive integer with at most ten digits")
    return f"{cik:010d}"


def _parse_sec_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SourceSchemaError(f"SEC {field} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SourceSchemaError(f"SEC {field} is not an ISO timestamp: {value!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceSchemaError(f"SEC {field} must include a timezone")
    return parsed.astimezone(UTC)


def _parse_sec_date(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SourceSchemaError(f"SEC {field} must be a non-empty date")
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as error:
        raise SourceSchemaError(f"SEC {field} is not YYYY-MM-DD: {value!r}") from error


def _require_json_content_type(value: str) -> str:
    content_type = value.split(";", maxsplit=1)[0].lower()
    if content_type not in {"application/json", "text/json"}:
        raise SourceSchemaError(f"unexpected SEC content type: {content_type!r}")
    return content_type
