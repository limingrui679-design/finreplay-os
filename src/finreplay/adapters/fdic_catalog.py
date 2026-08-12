"""Additional FDIC BankFind Suite data-product adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
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

_FIELD_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


@dataclass(frozen=True, slots=True)
class FDICDatasetSpec:
    slug: str
    title: str
    default_fields: tuple[str, ...]
    identity_fields: tuple[str, ...]
    valid_time_field: str | None
    default_sort: str
    description: str

    @property
    def adapter_id(self) -> str:
        return f"fdic.bankfind.{self.slug}"


FDIC_DATASET_SPECS: tuple[FDICDatasetSpec, ...] = (
    FDICDatasetSpec(
        slug="institutions",
        title="FDIC insured institutions",
        default_fields=(
            "CERT",
            "NAME",
            "ACTIVE",
            "INACTIVE",
            "ESTYMD",
            "ENDEFYMD",
            "DATEUPDT",
            "RUNDATE",
            "STALP",
            "FED_RSSD",
        ),
        identity_fields=("CERT",),
        valid_time_field="DATEUPDT",
        default_sort="CERT",
        description="Current institution identity, status, charter, location, and regulator data.",
    ),
    FDICDatasetSpec(
        slug="locations",
        title="FDIC institution locations and branches",
        default_fields=(
            "UNINUM",
            "CERT",
            "NAME",
            "OFFNAME",
            "OFFNUM",
            "ESTYMD",
            "ACQDATE",
            "RUNDATE",
            "LATITUDE",
            "LONGITUDE",
            "STALP",
            "MAINOFF",
        ),
        identity_fields=("UNINUM",),
        valid_time_field="RUNDATE",
        default_sort="UNINUM",
        description="Current main-office and branch locations for insured institutions.",
    ),
    FDICDatasetSpec(
        slug="history",
        title="FDIC structure change history",
        default_fields=(
            "ID",
            "CERT",
            "TRANSNUM",
            "PROCDATE",
            "EFFDATE",
            "CHANGECODE",
            "CHANGECODE_DESC",
            "INSTNAME",
            "OUT_UNINUM",
            "UNINUM",
        ),
        identity_fields=("ID",),
        valid_time_field="EFFDATE",
        default_sort="PROCDATE",
        description="Structure-change event records, including mergers and charter changes.",
    ),
    FDICDatasetSpec(
        slug="summary",
        title="FDIC historical aggregate summary",
        default_fields=("ID", "YEAR", "STALP", "ASSET", "DEP", "BANKS", "BRANCHES"),
        identity_fields=("ID",),
        valid_time_field="YEAR",
        default_sort="YEAR",
        description=(
            "Annual aggregate institution and financial measures from historical FDIC data."
        ),
    ),
    FDICDatasetSpec(
        slug="failures",
        title="FDIC failed bank list",
        default_fields=(
            "ID",
            "CERT",
            "NAME",
            "CITY",
            "PSTALP",
            "FAILDATE",
            "FIN",
            "COST",
            "QBFASSET",
            "QBFDEP",
            "UNINSDEP",
            "RESTYPE",
        ),
        identity_fields=("ID",),
        valid_time_field="FAILDATE",
        default_sort="FAILDATE",
        description="FDIC failed-institution resolution records.",
    ),
    FDICDatasetSpec(
        slug="sod",
        title="FDIC Summary of Deposits",
        default_fields=(
            "ID",
            "YEAR",
            "CERT",
            "UNINUMBR",
            "BRNUM",
            "NAMEFULL",
            "NAMEBR",
            "DEPSUMBR",
            "STALPBR",
        ),
        identity_fields=("ID",),
        valid_time_field="YEAR",
        default_sort="YEAR",
        description="Annual branch-level Summary of Deposits records.",
    ),
    FDICDatasetSpec(
        slug="demographics",
        title="FDIC institution demographics",
        default_fields=(
            "ID",
            "CERT",
            "REPDTE",
            "CALLYM",
            "OFFTOT",
            "OFFSOD",
            "OFFSTATE",
            "BRANCH",
            "METRO",
        ),
        identity_fields=("ID",),
        valid_time_field="REPDTE",
        default_sort="REPDTE",
        description="Quarterly institution demographic and office-distribution measures.",
    ),
)

FDIC_DATASET_BY_SLUG = {spec.slug: spec for spec in FDIC_DATASET_SPECS}


class FDICDatasetAdapter:
    """Strict parser parameterized by one documented BankFind data product."""

    def __init__(self, http: SafeHttpClient, spec: FDICDatasetSpec) -> None:
        self.http = http
        self.spec = spec
        self.endpoint = f"https://api.fdic.gov/banks/{spec.slug}"
        self.metadata = AdapterMetadata(
            adapter_id=spec.adapter_id,
            title=spec.title,
            publisher="Federal Deposit Insurance Corporation",
            documentation_url=_HTTP_URL_ADAPTER.validate_python(
                "https://api.fdic.gov/banks/docs/"
            ),
            allowed_hosts=("api.fdic.gov",),
            authentication=AuthenticationMode.NONE,
            rate_limit_policy=(
                "No undocumented numeric quota is assumed; requests are sequential and bounded."
            ),
            pagination_policy="Use documented limit/offset and reconcile every page to meta.total.",
            availability_rule=(
                "Current indexed snapshot only. Exact values become eligible at retrieval time; "
                "event/report dates are economic time, not assumed publication time."
            ),
            revision_behavior=(
                "The current BankFind index can incorporate corrections; this connector preserves "
                "each fetched index/content hash but does not invent missing historical vintages."
            ),
            temporal_coverage=TemporalCoverage.LATEST_ONLY,
            license_class=LicenseClass.DOWNLOAD_ONLY,
            redistribution_note=(
                "Preserve code, receipts, content hashes, and small derived fixtures; recheck FDIC "
                "terms before redistributing complete raw responses."
            ),
        )

    def fetch_page(
        self,
        *,
        fields: tuple[str, ...] | None = None,
        filters: str | None = None,
        limit: int = 1_000,
        offset: int = 0,
        sort_by: str | None = None,
        sort_order: str = "ASC",
    ) -> tuple[AdapterBatch, int]:
        if not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10,000")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        invalid_filters = filters is not None and (
            len(filters) > 2_000 or any(ord(char) < 32 for char in filters)
        )
        if invalid_filters:
            raise ValueError("filters contain control characters or exceed 2,000 characters")
        selected = self._validate_fields(fields or self.spec.default_fields)
        selected_sort = (sort_by or self.spec.default_sort).upper()
        if selected_sort not in selected:
            raise ValueError("sort_by must be included in selected fields")
        if sort_order not in {"ASC", "DESC"}:
            raise ValueError("sort_order must be ASC or DESC")
        params: dict[str, str | int] = {
            "fields": ",".join(selected),
            "sort_by": selected_sort,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
            "format": "json",
        }
        if filters:
            params["filters"] = filters
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
            params=params,
        )
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"application/json", "text/json"}:
            raise SourceSchemaError(f"unexpected FDIC content type: {content_type!r}")
        try:
            root = require_json_object(response.json(), f"FDIC {self.spec.slug} response")
        except (ValueError, TypeError) as error:
            raise SourceSchemaError(f"FDIC {self.spec.slug} response is not valid JSON") from error
        digest = source_response_sha256(content)
        records, total, source_version = self._parse(
            root,
            selected_fields=selected,
            retrieved_at=retrieved_at,
            response_url=response.request_url,
            response_sha256=digest,
        )
        warning = (
            f"Latest-only FDIC {self.spec.slug} snapshot: economic dates do not establish when "
            "this exact current-index value first became public."
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
        batch = AdapterBatch(records=tuple(records), receipts=(receipt,), artifacts=(artifact,))
        return batch, total

    def _parse(
        self,
        root: dict[str, Any],
        *,
        selected_fields: tuple[str, ...],
        retrieved_at: datetime,
        response_url: str,
        response_sha256: str,
    ) -> tuple[list[BitemporalRecord], int, str]:
        meta = require_json_object(root.get("meta"), f"FDIC {self.spec.slug} meta")
        index = require_json_object(meta.get("index"), f"FDIC {self.spec.slug} meta.index")
        index_name = index.get("name")
        index_created = index.get("createTimestamp")
        if not isinstance(index_name, str) or not index_name:
            raise SourceSchemaError("FDIC index name is missing")
        if not isinstance(index_created, str) or not index_created:
            raise SourceSchemaError("FDIC index createTimestamp is missing")
        total = meta.get("total")
        if not isinstance(total, int) or total < 0:
            raise SourceSchemaError("FDIC meta.total must be a non-negative integer")
        data = root.get("data")
        if not isinstance(data, list):
            raise SourceSchemaError("FDIC data must be a list")
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(response_url),
            retrieved_at=retrieved_at,
            source_version=f"{index_name}@{index_created}",
            sha256=response_sha256,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.LATEST_ONLY,
            redistribution_note=self.metadata.redistribution_note,
        )
        records: list[BitemporalRecord] = []
        for position, wrapper_value in enumerate(data):
            wrapper = require_json_object(
                wrapper_value, f"FDIC {self.spec.slug} data[{position}]"
            )
            row = require_json_object(
                wrapper.get("data"), f"FDIC {self.spec.slug} data[{position}].data"
            )
            required_fields = set(self.spec.identity_fields)
            if self.spec.valid_time_field is not None:
                required_fields.add(self.spec.valid_time_field)
            missing_required = required_fields - set(row)
            if missing_required:
                raise SourceSchemaError(
                    f"FDIC row is missing required fields: {sorted(missing_required)}"
                )
            identity = self._record_identity(row)
            valid_from = self._valid_time(row, retrieved_at)
            records.append(
                BitemporalRecord(
                    record_id=f"{self.metadata.adapter_id}:{identity}",
                    entity_id=self._entity_id(row),
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
                    # BankFind omits selected keys whose values are null. Preserve the requested
                    # schema explicitly while keeping identity/time fields fail-closed above.
                    payload={field: row.get(field) for field in selected_fields},
                )
            )
        return records, total, f"{index_name}@{index_created}"

    def _record_identity(self, row: dict[str, Any]) -> str:
        parts: list[str] = []
        for field in self.spec.identity_fields:
            value = row.get(field)
            if value is None or str(value).strip() == "":
                raise SourceSchemaError(f"FDIC identity field {field} is empty")
            parts.append(str(value).strip())
        return ":".join(parts)

    @staticmethod
    def _entity_id(row: dict[str, Any]) -> str | None:
        cert = row.get("CERT")
        return f"fdic_cert:{cert}" if cert not in (None, "") else None

    def _valid_time(self, row: dict[str, Any], fallback: datetime) -> datetime:
        field = self.spec.valid_time_field
        if field is None:
            return fallback
        raw = row.get(field)
        if raw in (None, ""):
            raise SourceSchemaError(f"FDIC valid-time field {field} is empty")
        text = str(raw).strip()
        date_format: str | None = None
        if re.fullmatch(r"[0-9]{4}", text):
            date_format = "%Y"
        elif re.fullmatch(r"[0-9]{6}", text):
            date_format = "%Y%m"
        elif re.fullmatch(r"[0-9]{8}", text):
            date_format = "%Y%m%d"
        elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
            date_format = "%Y-%m-%d"
        elif re.fullmatch(r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}", text):
            date_format = "%m/%d/%Y"
        try:
            if date_format is not None:
                parsed = datetime.strptime(text, date_format).replace(tzinfo=UTC)
            else:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                else:
                    parsed = parsed.astimezone(UTC)
        except ValueError as error:
            raise SourceSchemaError(f"invalid FDIC date for {field}: {text!r}") from error
        if date_format == "%Y":
            parsed = parsed.replace(month=12, day=31)
        return parsed

    @staticmethod
    def _validate_fields(fields: tuple[str, ...]) -> tuple[str, ...]:
        selected = tuple(dict.fromkeys(field.strip().upper() for field in fields))
        if not selected or any(not _FIELD_PATTERN.fullmatch(field) for field in selected):
            raise ValueError("FDIC fields must be non-empty documented uppercase identifiers")
        return selected
