"""FDIC BankFind Suite financials adapter."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import HttpUrl, TypeAdapter, ValidationError

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

_FIELD_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
_DATE_PATTERN = re.compile(r"^[0-9]{8}$")
_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


class FDICFinancialsAdapter:
    """Retrieve current FDIC BankFind financial snapshots without inventing vintages."""

    endpoint = "https://api.fdic.gov/banks/financials"
    metadata = AdapterMetadata(
        adapter_id="fdic.bankfind.financials",
        title="FDIC BankFind Suite financials",
        publisher="Federal Deposit Insurance Corporation",
        documentation_url=_HTTP_URL_ADAPTER.validate_python("https://api.fdic.gov/banks/docs/"),
        allowed_hosts=("api.fdic.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "No public numeric quota is assumed. Requests are sequential, bounded, and paginated; "
            "HTTP throttling fails visibly rather than retrying without limit."
        ),
        pagination_policy="Elastic-style limit and offset; total is read from response metadata.",
        availability_rule=(
            "The API does not expose the publication timestamp of the exact current value. "
            "Therefore the exact payload is considered knowable only at this retrieval time."
        ),
        revision_behavior=(
            "Current indexed snapshot; historical pre-revision values are not guaranteed by this "
            "endpoint and must not be reconstructed from report dates."
        ),
        temporal_coverage=TemporalCoverage.LATEST_ONLY,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Store content hashes, receipts, schemas, and derived fixtures in the repository. "
            "Recheck FDIC terms before redistributing full raw responses."
        ),
    )

    def __init__(self, http: SafeHttpClient) -> None:
        self.http = http

    def fetch_page(
        self,
        *,
        cert: int,
        fields: tuple[str, ...],
        limit: int = 1_000,
        offset: int = 0,
        sort_by: str = "REPDTE",
        sort_order: str = "ASC",
    ) -> tuple[AdapterBatch, int]:
        if cert <= 0:
            raise ValueError("cert must be a positive FDIC certificate number")
        if not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10,000")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        normalized_fields = self._validate_fields(fields)
        if sort_by not in normalized_fields:
            raise ValueError("sort_by must be included in fields")
        if sort_order not in {"ASC", "DESC"}:
            raise ValueError("sort_order must be ASC or DESC")
        params: dict[str, str | int] = {
            "filters": f"CERT:{cert}",
            "fields": ",".join(normalized_fields),
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
            "format": "json",
        }
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
            params=params,
        )
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"application/json", "text/json"}:
            raise SourceSchemaError(f"unexpected FDIC content type: {content_type!r}")
        try:
            root = require_json_object(response.json(), "FDIC response")
        except (ValueError, ValidationError) as error:
            raise SourceSchemaError("FDIC response is not valid JSON") from error
        records, total, source_version = self._parse(
            root,
            cert=cert,
            requested_fields=normalized_fields,
            retrieved_at=retrieved_at,
            response_url=response.request_url,
            response_sha256=source_response_sha256(content),
        )
        digest = source_response_sha256(content)
        warning = (
            "Latest-only FDIC snapshot: exact values are eligible from retrieval time, not from "
            "their quarter-end report date; historical replay requires an archived vintage."
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
            temporal_coverage=self.metadata.temporal_coverage,
            historical_replay_eligible=False,
            warnings=(warning,),
        )
        artifact = RawArtifact(sha256=digest, content_type=content_type, content=content)
        batch = AdapterBatch(records=tuple(records), receipts=(receipt,), artifacts=(artifact,))
        return batch, total

    def fetch_all(
        self,
        *,
        cert: int,
        fields: tuple[str, ...],
        page_size: int = 1_000,
        max_pages: int = 100,
    ) -> AdapterBatch:
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        records: list[BitemporalRecord] = []
        receipts: list[FetchReceipt] = []
        artifacts: list[RawArtifact] = []
        offset = 0
        total: int | None = None
        for _ in range(max_pages):
            page, page_total = self.fetch_page(
                cert=cert,
                fields=fields,
                limit=page_size,
                offset=offset,
            )
            if total is None:
                total = page_total
            elif page_total != total:
                raise SourceSchemaError("FDIC total changed during pagination")
            records.extend(page.records)
            receipts.extend(page.receipts)
            artifacts.extend(page.artifacts)
            offset += len(page.records)
            if offset >= total:
                break
            if not page.records:
                raise SourceSchemaError("FDIC returned an empty page before advertised total")
        if total is None or len(records) != total:
            raise SourceSchemaError(
                f"pagination incomplete: parsed {len(records)} of {total} records"
            )
        if len({record.record_id for record in records}) != len(records):
            raise SourceSchemaError("FDIC pagination produced duplicate logical record IDs")
        return AdapterBatch(
            records=tuple(records),
            receipts=tuple(receipts),
            artifacts=tuple(artifacts),
        )

    @staticmethod
    def _validate_fields(fields: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(field.strip().upper() for field in fields))
        if not normalized:
            raise ValueError("at least one field is required")
        if not {"CERT", "REPDTE"}.issubset(normalized):
            raise ValueError("fields must include CERT and REPDTE")
        if any(not _FIELD_PATTERN.fullmatch(field) for field in normalized):
            raise ValueError("FDIC fields must use documented uppercase identifiers")
        return normalized

    def _parse(
        self,
        root: dict[str, Any],
        *,
        cert: int,
        requested_fields: tuple[str, ...],
        retrieved_at: datetime,
        response_url: str,
        response_sha256: str,
    ) -> tuple[list[BitemporalRecord], int, str]:
        meta = require_json_object(root.get("meta"), "FDIC meta")
        index = require_json_object(meta.get("index"), "FDIC meta.index")
        index_name = index.get("name")
        index_created = index.get("createTimestamp")
        if not isinstance(index_name, str) or not index_name:
            raise SourceSchemaError("FDIC meta.index.name is missing")
        if not isinstance(index_created, str) or not index_created:
            raise SourceSchemaError("FDIC meta.index.createTimestamp is missing")
        source_version = f"{index_name}@{index_created}"
        raw_total = meta.get("total")
        if not isinstance(raw_total, int) or raw_total < 0:
            raise SourceSchemaError("FDIC meta.total must be a non-negative integer")
        raw_data = root.get("data")
        if not isinstance(raw_data, list):
            raise SourceSchemaError("FDIC data must be a list")
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(response_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=response_sha256,
            license_class=self.metadata.license_class,
            temporal_coverage=self.metadata.temporal_coverage,
            redistribution_note=self.metadata.redistribution_note,
        )
        records: list[BitemporalRecord] = []
        for position, wrapper_value in enumerate(raw_data):
            wrapper = require_json_object(wrapper_value, f"FDIC data[{position}]")
            row = require_json_object(wrapper.get("data"), f"FDIC data[{position}].data")
            missing = set(requested_fields) - set(row)
            if missing:
                raise SourceSchemaError(f"FDIC row is missing requested fields: {sorted(missing)}")
            if row.get("CERT") != cert:
                raise SourceSchemaError(f"FDIC row CERT {row.get('CERT')!r} does not match {cert}")
            report_date_raw = row.get("REPDTE")
            if not isinstance(report_date_raw, str) or not _DATE_PATTERN.fullmatch(report_date_raw):
                raise SourceSchemaError(f"invalid FDIC REPDTE: {report_date_raw!r}")
            report_date = datetime.strptime(report_date_raw, "%Y%m%d").replace(tzinfo=UTC)
            record_id = f"fdic.financials:{cert}:{report_date_raw}"
            payload = {field: row[field] for field in requested_fields}
            payload["ID"] = row.get("ID", f"{cert}_{report_date_raw}")
            records.append(
                BitemporalRecord(
                    record_id=record_id,
                    entity_id=f"fdic_cert:{cert}",
                    source=source,
                    interval=BitemporalInterval(
                        valid_from=report_date,
                        published_at=retrieved_at,
                        available_at=retrieved_at,
                        ingested_at=retrieved_at,
                        availability_rule=self.metadata.availability_rule,
                        availability_confidence=1.0,
                    ),
                    evidence_class=EvidenceClass.REPORTED,
                    payload_schema_version="1.0.0",
                    payload=TypeAdapter(dict[str, Any]).validate_python(payload),
                )
            )
        return records, raw_total, source_version
