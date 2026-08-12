"""U.S. Treasury Fiscal Data adapters with conservative temporal eligibility."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from urllib.parse import quote

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
_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_CUSIP_PATTERN = re.compile(r"^[0-9A-Z*@#]{9}$")


class FiscalDataSemanticKind(StrEnum):
    """Source-specific invariant family for one Fiscal Data table."""

    DEBT_TOTAL = "debt_total"
    AVERAGE_RATE = "average_rate"
    CASH_BALANCE = "cash_balance"
    AUCTION = "auction"
    DEBT_SUMMARY = "debt_summary"


@dataclass(frozen=True, slots=True)
class FiscalDataSpec:
    """One independently counted Treasury data table and parser contract."""

    slug: str
    title: str
    api_path: str
    documentation_url: str
    required_fields: tuple[str, ...]
    identity_fields: tuple[str, ...]
    valid_time_field: str
    semantic_kind: FiscalDataSemanticKind

    @property
    def adapter_id(self) -> str:
        return f"treasury.fiscaldata.{self.slug}"


FISCAL_DATA_SPECS: tuple[FiscalDataSpec, ...] = (
    FiscalDataSpec(
        slug="debt_to_penny",
        title="U.S. Treasury Debt to the Penny",
        api_path="v2/accounting/od/debt_to_penny",
        documentation_url="https://fiscaldata.treasury.gov/datasets/debt-to-the-penny/",
        required_fields=(
            "record_date",
            "debt_held_public_amt",
            "intragov_hold_amt",
            "tot_pub_debt_out_amt",
            "src_line_nbr",
        ),
        identity_fields=("record_date", "src_line_nbr"),
        valid_time_field="record_date",
        semantic_kind=FiscalDataSemanticKind.DEBT_TOTAL,
    ),
    FiscalDataSpec(
        slug="average_interest_rates",
        title="U.S. Treasury Average Interest Rates on Debt",
        api_path="v2/accounting/od/avg_interest_rates",
        documentation_url=(
            "https://fiscaldata.treasury.gov/datasets/average-interest-rates-treasury-"
            "securities/"
        ),
        required_fields=(
            "record_date",
            "security_type_desc",
            "security_desc",
            "avg_interest_rate_amt",
            "src_line_nbr",
        ),
        identity_fields=(
            "record_date",
            "security_type_desc",
            "security_desc",
            "src_line_nbr",
        ),
        valid_time_field="record_date",
        semantic_kind=FiscalDataSemanticKind.AVERAGE_RATE,
    ),
    FiscalDataSpec(
        slug="operating_cash_balance",
        title="U.S. Treasury Daily Operating Cash Balance",
        api_path="v1/accounting/dts/operating_cash_balance",
        documentation_url=(
            "https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/"
            "operating-cash-balance/"
        ),
        required_fields=(
            "record_date",
            "account_type",
            "close_today_bal",
            "open_today_bal",
            "open_month_bal",
            "open_fiscal_year_bal",
            "table_nbr",
            "sub_table_name",
            "src_line_nbr",
        ),
        identity_fields=(
            "record_date",
            "table_nbr",
            "sub_table_name",
            "src_line_nbr",
        ),
        valid_time_field="record_date",
        semantic_kind=FiscalDataSemanticKind.CASH_BALANCE,
    ),
    FiscalDataSpec(
        slug="treasury_auctions",
        title="U.S. Treasury Securities Auctions",
        api_path="v1/accounting/od/auctions_query",
        documentation_url=(
            "https://fiscaldata.treasury.gov/datasets/treasury-securities-auctions-data/"
        ),
        required_fields=(
            "record_date",
            "cusip",
            "security_type",
            "security_term",
            "announcemt_date",
            "auction_date",
            "issue_date",
            "maturity_date",
        ),
        identity_fields=("record_date", "cusip", "auction_date"),
        # Announced future issues are already knowable. Their economic event begins at the
        # announcement, while knowledge eligibility remains the retrieval timestamp below.
        valid_time_field="announcemt_date",
        semantic_kind=FiscalDataSemanticKind.AUCTION,
    ),
    FiscalDataSpec(
        slug="mspd_summary",
        title="U.S. Treasury Monthly Statement of the Public Debt Summary",
        api_path="v1/debt/mspd/mspd_table_1",
        documentation_url=(
            "https://fiscaldata.treasury.gov/datasets/monthly-statement-public-debt/"
        ),
        required_fields=(
            "record_date",
            "security_type_desc",
            "security_class_desc",
            "debt_held_public_mil_amt",
            "intragov_hold_mil_amt",
            "total_mil_amt",
            "src_line_nbr",
        ),
        identity_fields=(
            "record_date",
            "security_type_desc",
            "security_class_desc",
            "src_line_nbr",
        ),
        valid_time_field="record_date",
        semantic_kind=FiscalDataSemanticKind.DEBT_SUMMARY,
    ),
)

FISCAL_DATA_BY_SLUG = {spec.slug: spec for spec in FISCAL_DATA_SPECS}


class FiscalDataAdapter:
    """Retrieve and validate one current Fiscal Data table without inventing vintages."""

    endpoint_root = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"

    def __init__(self, http: SafeHttpClient, spec: FiscalDataSpec) -> None:
        self.http = http
        self.spec = spec
        self.endpoint = f"{self.endpoint_root}/{spec.api_path}"
        self.metadata = AdapterMetadata(
            adapter_id=spec.adapter_id,
            title=spec.title,
            publisher="U.S. Department of the Treasury, Bureau of the Fiscal Service",
            documentation_url=_HTTP_URL_ADAPTER.validate_python(spec.documentation_url),
            allowed_hosts=("api.fiscaldata.treasury.gov",),
            authentication=AuthenticationMode.NONE,
            rate_limit_policy=(
                "No undocumented numeric quota is assumed; requests are sequential, bounded, "
                "and throttling fails visibly."
            ),
            pagination_policy=(
                "Use page[number]/page[size], reconcile meta count, total-count and total-pages, "
                "and reject a changing total or duplicate identity across pages."
            ),
            availability_rule=(
                "The current API response can contain prior-dated and future-effective rows but "
                "does not expose the full revision vintage of each value. Exact fetched values "
                "therefore become knowledge-eligible only at retrieval time."
            ),
            revision_behavior=(
                "Current table snapshot may incorporate corrections. Content-address every "
                "response and never backdate the current value to record_date."
            ),
            temporal_coverage=TemporalCoverage.LATEST_ONLY,
            license_class=LicenseClass.DOWNLOAD_ONLY,
            redistribution_note=(
                "Commit connector code, metadata, hashes and small derived fixtures; recheck "
                "Fiscal Data terms before redistributing complete raw responses."
            ),
        )

    def fetch_page(
        self,
        *,
        page_number: int = 1,
        page_size: int = 100,
        sort: str = "-record_date",
        filters: str | None = None,
    ) -> tuple[AdapterBatch, int, int]:
        if page_number <= 0:
            raise ValueError("page_number must be positive")
        if not 1 <= page_size <= 10_000:
            raise ValueError("page_size must be between 1 and 10,000")
        if not sort or len(sort) > 200 or any(ord(char) < 32 for char in sort):
            raise ValueError("sort must be non-empty, bounded text without control characters")
        invalid_filter = filters is not None and (
            len(filters) > 2_000 or any(ord(char) < 32 for char in filters)
        )
        if invalid_filter:
            raise ValueError("filters contain control characters or exceed 2,000 characters")
        params: dict[str, str | int] = {
            "page[number]": page_number,
            "page[size]": page_size,
            "sort": sort,
        }
        if filters:
            params["filter"] = filters
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
            params=params,
        )
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"application/json", "text/json"}:
            raise SourceSchemaError(f"unexpected Fiscal Data content type: {content_type!r}")
        try:
            root = require_json_object(response.json(), f"Fiscal Data {self.spec.slug} response")
        except (ValueError, TypeError) as error:
            raise SourceSchemaError(
                f"Fiscal Data {self.spec.slug} response is not valid JSON"
            ) from error
        digest = source_response_sha256(content)
        records, total_count, total_pages = self._parse(
            root,
            page_size=page_size,
            retrieved_at=retrieved_at,
            response_url=response.request_url,
            response_sha256=digest,
        )
        warning = (
            f"Latest-only Treasury {self.spec.slug} snapshot: record/effective dates are "
            "economic time and cannot establish when this exact current value first became public."
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
            source_version=f"sha256:{digest[:24]}",
            temporal_coverage=TemporalCoverage.LATEST_ONLY,
            historical_replay_eligible=False,
            warnings=(warning,),
        )
        artifact = RawArtifact(sha256=digest, content_type=content_type, content=content)
        return (
            AdapterBatch(records=tuple(records), receipts=(receipt,), artifacts=(artifact,)),
            total_count,
            total_pages,
        )

    def fetch_all(
        self,
        *,
        page_size: int = 1_000,
        max_pages: int = 100,
        sort: str = "record_date",
        filters: str | None = None,
    ) -> AdapterBatch:
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        records: list[BitemporalRecord] = []
        receipts: list[FetchReceipt] = []
        artifacts: list[RawArtifact] = []
        expected_total: int | None = None
        expected_pages: int | None = None
        seen_ids: set[str] = set()
        for page_number in range(1, max_pages + 1):
            page, total, total_pages = self.fetch_page(
                page_number=page_number,
                page_size=page_size,
                sort=sort,
                filters=filters,
            )
            if expected_total is None:
                expected_total = total
                expected_pages = total_pages
            elif total != expected_total or total_pages != expected_pages:
                raise SourceSchemaError("Fiscal Data pagination totals changed during retrieval")
            if not page.records and len(records) < total:
                raise SourceSchemaError(
                    "Fiscal Data returned an empty page before advertised total"
                )
            duplicate = seen_ids.intersection(record.record_id for record in page.records)
            if duplicate:
                raise SourceSchemaError(
                    f"Fiscal Data pagination produced duplicate identity: {min(duplicate)}"
                )
            records.extend(page.records)
            seen_ids.update(record.record_id for record in page.records)
            receipts.extend(page.receipts)
            artifacts.extend(page.artifacts)
            if len(records) >= total:
                break
        if expected_total is None or len(records) != expected_total:
            raise SourceSchemaError(
                f"Fiscal Data pagination incomplete: parsed {len(records)} of "
                f"{expected_total} records"
            )
        return AdapterBatch(
            records=tuple(records), receipts=tuple(receipts), artifacts=tuple(artifacts)
        )

    def _parse(
        self,
        root: dict[str, Any],
        *,
        page_size: int,
        retrieved_at: datetime,
        response_url: str,
        response_sha256: str,
    ) -> tuple[list[BitemporalRecord], int, int]:
        data = root.get("data")
        if not isinstance(data, list):
            raise SourceSchemaError("Fiscal Data data must be a list")
        meta = require_json_object(root.get("meta"), "Fiscal Data meta")
        links = require_json_object(root.get("links"), "Fiscal Data links")
        count = meta.get("count")
        total_count = meta.get("total-count")
        total_pages = meta.get("total-pages")
        if not isinstance(count, int) or count != len(data):
            raise SourceSchemaError("Fiscal Data meta.count must equal page row count")
        if not isinstance(total_count, int) or total_count < count:
            raise SourceSchemaError("Fiscal Data meta.total-count must cover the page count")
        expected_pages = math.ceil(total_count / page_size) if total_count else 0
        if not isinstance(total_pages, int) or total_pages != expected_pages:
            raise SourceSchemaError("Fiscal Data meta.total-pages is inconsistent with page size")
        for name in ("labels", "dataTypes", "dataFormats"):
            schema = require_json_object(meta.get(name), f"Fiscal Data meta.{name}")
            missing_schema = set(self.spec.required_fields) - set(schema)
            if missing_schema:
                raise SourceSchemaError(
                    f"Fiscal Data meta.{name} is missing fields: {sorted(missing_schema)}"
                )
        if not isinstance(links.get("self"), str):
            raise SourceSchemaError("Fiscal Data links.self must be text")
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(response_url),
            retrieved_at=retrieved_at,
            source_version=f"sha256:{response_sha256[:24]}",
            sha256=response_sha256,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.LATEST_ONLY,
            redistribution_note=self.metadata.redistribution_note,
        )
        records: list[BitemporalRecord] = []
        page_ids: set[str] = set()
        for position, value in enumerate(data):
            row = require_json_object(value, f"Fiscal Data data[{position}]")
            missing = set(self.spec.required_fields) - set(row)
            if missing:
                raise SourceSchemaError(f"Fiscal Data row is missing fields: {sorted(missing)}")
            for field in self.spec.identity_fields:
                if row[field] in (None, "", "null"):
                    raise SourceSchemaError(f"Fiscal Data identity field {field} is empty")
            valid_from = _date_at_utc(row[self.spec.valid_time_field], self.spec.valid_time_field)
            self._validate_semantics(row)
            identity = ":".join(
                quote(str(row[field]), safe="") for field in self.spec.identity_fields
            )
            record_id = f"{self.metadata.adapter_id}:{identity}"
            if len(record_id) > 300:
                identity_digest = hashlib.sha256(
                    json.dumps(
                        {field: row[field] for field in self.spec.identity_fields},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                record_id = f"{self.metadata.adapter_id}:sha256:{identity_digest}"
            if record_id in page_ids:
                raise SourceSchemaError(f"duplicate Fiscal Data row identity: {record_id}")
            page_ids.add(record_id)
            records.append(
                BitemporalRecord(
                    record_id=record_id,
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
        return records, total_count, total_pages

    def _validate_semantics(self, row: dict[str, Any]) -> None:
        kind = self.spec.semantic_kind
        if kind is FiscalDataSemanticKind.DEBT_TOTAL:
            public = _decimal(row["debt_held_public_amt"], "debt_held_public_amt", nonnegative=True)
            intra = _decimal(row["intragov_hold_amt"], "intragov_hold_amt", nonnegative=True)
            total = _decimal(row["tot_pub_debt_out_amt"], "tot_pub_debt_out_amt", nonnegative=True)
            if public + intra != total:
                raise SourceSchemaError("Treasury debt components do not equal total public debt")
        elif kind is FiscalDataSemanticKind.AVERAGE_RATE:
            rate = _decimal(row["avg_interest_rate_amt"], "avg_interest_rate_amt")
            if not Decimal("0") <= rate <= Decimal("100"):
                raise SourceSchemaError("Treasury average interest rate is outside [0, 100]")
        elif kind is FiscalDataSemanticKind.CASH_BALANCE:
            fields = (
                "close_today_bal",
                "open_today_bal",
                "open_month_bal",
                "open_fiscal_year_bal",
            )
            present = [field for field in fields if row[field] not in (None, "", "null")]
            if not present:
                raise SourceSchemaError("Treasury cash row has no reported balance")
            for field in present:
                _decimal(row[field], field)
        elif kind is FiscalDataSemanticKind.AUCTION:
            cusip = row["cusip"]
            if not isinstance(cusip, str) or not _CUSIP_PATTERN.fullmatch(cusip):
                raise SourceSchemaError("Treasury auction CUSIP must be nine valid characters")
            announced = _date_at_utc(row["announcemt_date"], "announcemt_date")
            auction = _date_at_utc(row["auction_date"], "auction_date")
            issue = _date_at_utc(row["issue_date"], "issue_date")
            maturity = _date_at_utc(row["maturity_date"], "maturity_date")
            if not announced <= auction <= issue <= maturity:
                raise SourceSchemaError("Treasury auction dates are not chronologically ordered")
        elif kind is FiscalDataSemanticKind.DEBT_SUMMARY:
            public = _decimal(
                row["debt_held_public_mil_amt"], "debt_held_public_mil_amt", nonnegative=True
            )
            intra = _decimal(
                row["intragov_hold_mil_amt"], "intragov_hold_mil_amt", nonnegative=True
            )
            total = _decimal(row["total_mil_amt"], "total_mil_amt", nonnegative=True)
            if public + intra != total:
                raise SourceSchemaError("Treasury MSPD components do not equal row total")


def _date_at_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise SourceSchemaError(f"Fiscal Data {field} must use YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as error:
        raise SourceSchemaError(f"Fiscal Data {field} is not a calendar date") from error


def _decimal(value: Any, field: str, *, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or value in (None, "", "null"):
        raise SourceSchemaError(f"Fiscal Data {field} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise SourceSchemaError(f"Fiscal Data {field} must be numeric") from error
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise SourceSchemaError(f"Fiscal Data {field} has an invalid numeric value")
    return parsed
