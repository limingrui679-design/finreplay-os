"""CFTC Commitments of Traders product adapters."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
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
_REPORT_DATE_PATTERN = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})T00:00:00(?:\.000)?$"
)
_INTEGER_PATTERN = re.compile(r"^-?[0-9]+$")
_COMMON_FIELDS = (
    "id",
    "market_and_exchange_names",
    "report_date_as_yyyy_mm_dd",
    "yyyy_report_week_ww",
    "contract_market_name",
    "cftc_contract_market_code",
    "commodity_name",
    "open_interest_all",
    "tot_rept_positions_long_all",
    "tot_rept_positions_short",
    "nonrept_positions_long_all",
    "nonrept_positions_short_all",
)


class CFTCReportKind(StrEnum):
    """The four independently defined CFTC COT classification products."""

    LEGACY = "legacy"
    DISAGGREGATED = "disaggregated"
    TFF = "traders_in_financial_futures"
    SUPPLEMENTAL_CIT = "supplemental_commodity_index_traders"


@dataclass(frozen=True, slots=True)
class CFTCCOTSpec:
    """One official COT view and its source-specific balance equation."""

    slug: str
    title: str
    view_id: str
    upstream_dataset_id: str
    report_kind: CFTCReportKind
    long_components: tuple[str, ...]
    short_components: tuple[str, ...]
    balance_tolerance: int
    expected_mode: str | None

    @property
    def adapter_id(self) -> str:
        return f"cftc.cot.{self.slug}"

    @property
    def required_fields(self) -> tuple[str, ...]:
        fields = (*_COMMON_FIELDS, *self.long_components, *self.short_components)
        if self.expected_mode is not None:
            fields = (*fields, "futonly_or_combined")
        return tuple(dict.fromkeys(fields))


CFTC_COT_SPECS: tuple[CFTCCOTSpec, ...] = (
    CFTCCOTSpec(
        slug="legacy_futures_only",
        title="CFTC Legacy Commitments of Traders — Futures Only",
        view_id="6dca-aqww",
        upstream_dataset_id="srt6-5q2f",
        report_kind=CFTCReportKind.LEGACY,
        long_components=(
            "noncomm_positions_long_all",
            "noncomm_postions_spread_all",
            "comm_positions_long_all",
        ),
        short_components=(
            "noncomm_positions_short_all",
            "noncomm_postions_spread_all",
            "comm_positions_short_all",
        ),
        balance_tolerance=1,
        expected_mode="FutOnly",
    ),
    CFTCCOTSpec(
        slug="disaggregated_futures_only",
        title="CFTC Disaggregated Commitments of Traders — Futures Only",
        view_id="72hh-3qpy",
        upstream_dataset_id="rxbv-e226",
        report_kind=CFTCReportKind.DISAGGREGATED,
        long_components=(
            "prod_merc_positions_long",
            "swap_positions_long_all",
            "swap__positions_spread_all",
            "m_money_positions_long_all",
            "m_money_positions_spread",
            "other_rept_positions_long",
            "other_rept_positions_spread",
        ),
        short_components=(
            "prod_merc_positions_short",
            "swap__positions_short_all",
            "swap__positions_spread_all",
            "m_money_positions_short_all",
            "m_money_positions_spread",
            "other_rept_positions_short",
            "other_rept_positions_spread",
        ),
        balance_tolerance=1,
        expected_mode="FutOnly",
    ),
    CFTCCOTSpec(
        slug="tff_futures_only",
        title="CFTC Traders in Financial Futures — Futures Only",
        view_id="gpe5-46if",
        upstream_dataset_id="udgc-27he",
        report_kind=CFTCReportKind.TFF,
        long_components=(
            "dealer_positions_long_all",
            "dealer_positions_spread_all",
            "asset_mgr_positions_long",
            "asset_mgr_positions_spread",
            "lev_money_positions_long",
            "lev_money_positions_spread",
            "other_rept_positions_long",
            "other_rept_positions_spread",
        ),
        short_components=(
            "dealer_positions_short_all",
            "dealer_positions_spread_all",
            "asset_mgr_positions_short",
            "asset_mgr_positions_spread",
            "lev_money_positions_short",
            "lev_money_positions_spread",
            "other_rept_positions_short",
            "other_rept_positions_spread",
        ),
        balance_tolerance=2,
        expected_mode="FutOnly",
    ),
    CFTCCOTSpec(
        slug="supplemental_cit",
        title="CFTC Supplemental Commodity Index Traders Report",
        view_id="4zgm-a668",
        upstream_dataset_id="j83k-qyrd",
        report_kind=CFTCReportKind.SUPPLEMENTAL_CIT,
        long_components=(
            "ncomm_postions_long_all_nocit",
            "ncomm_postions_spread_all_nocit",
            "comm_positions_long_all_nocit",
            "cit_positions_long_all",
        ),
        short_components=(
            "ncomm_postions_short_all_nocit",
            "ncomm_postions_spread_all_nocit",
            "comm_positions_short_all_nocit",
            "cit_positions_short_all",
        ),
        balance_tolerance=2,
        expected_mode=None,
    ),
)

CFTC_COT_BY_SLUG = {spec.slug: spec for spec in CFTC_COT_SPECS}


class CFTCCOTAdapter:
    """Retrieve and reconcile one independently defined CFTC COT product."""

    endpoint_root = "https://publicreporting.cftc.gov/resource"

    def __init__(self, http: SafeHttpClient, spec: CFTCCOTSpec) -> None:
        self.http = http
        self.spec = spec
        self.endpoint = f"{self.endpoint_root}/{spec.view_id}.json"
        self.metadata = AdapterMetadata(
            adapter_id=spec.adapter_id,
            title=spec.title,
            publisher="U.S. Commodity Futures Trading Commission",
            documentation_url=_HTTP_URL_ADAPTER.validate_python(
                "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"
            ),
            allowed_hosts=("publicreporting.cftc.gov",),
            authentication=AuthenticationMode.NONE,
            rate_limit_policy=(
                "CFTC currently issues no API tokens and permits unauthenticated use when the "
                "API is not overused. Requests are sequential, bounded, and fail visibly on "
                "throttling."
            ),
            pagination_policy=(
                "Use Socrata $limit/$offset with a fixed report-date, contract-code, and source-ID "
                "order. CFTC states that the dataset has no primary key; the supplied ID is only "
                "a response-local identity and duplicate IDs fail closed."
            ),
            availability_rule=(
                "The API report date is economic time, not release time. Although COT reports are "
                "normally released on a weekly schedule, holidays and exceptional delays occur; "
                "without a row-specific release record, the exact row is knowable only at this "
                "retrieval time."
            ),
            revision_behavior=(
                "CFTC states that historical COT data are not updated once published. Each API "
                "page is nevertheless content-addressed, and corrections or release-time claims "
                "are never inferred from the report date."
            ),
            temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
            license_class=LicenseClass.REDISTRIBUTABLE,
            redistribution_note=(
                "CFTC government information is public domain and may be copied or distributed "
                "with appropriate CFTC acknowledgement. Do not imply CFTC endorsement and recheck "
                "the current web policy for any identified third-party material."
            ),
        )

    def fetch_page(self, *, limit: int = 1_000, offset: int = 0) -> AdapterBatch:
        if not 1 <= limit <= 50_000:
            raise ValueError("limit must be between 1 and 50,000")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
            params={
                "$limit": limit,
                "$offset": offset,
                "$order": ("report_date_as_yyyy_mm_dd DESC,cftc_contract_market_code ASC,id ASC"),
            },
        )
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"application/json", "text/json"}:
            raise SourceSchemaError(f"unexpected CFTC content type: {content_type!r}")
        try:
            raw_rows = response.json()
        except (TypeError, ValueError) as error:
            raise SourceSchemaError("CFTC response is not valid JSON") from error
        if not isinstance(raw_rows, list):
            raise SourceSchemaError("CFTC response must be a JSON list")
        rows = [self._normalize_row(value, position) for position, value in enumerate(raw_rows)]
        digest = source_response_sha256(content)
        dates = [self._report_date(row["report_date_as_yyyy_mm_dd"]) for row in rows]
        through = max(dates).date().isoformat() if dates else "empty"
        source_version = (
            f"view:{self.spec.view_id}:upstream:{self.spec.upstream_dataset_id}:"
            f"through:{through}:sha256:{digest[:24]}"
        )
        source: SourceReference | None = None
        if rows:
            source = SourceReference(
                source_id=self.metadata.adapter_id,
                publisher=self.metadata.publisher,
                url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
                retrieved_at=retrieved_at,
                source_version=source_version,
                sha256=digest,
                license_class=self.metadata.license_class,
                temporal_coverage=TemporalCoverage.IMMUTABLE_EVENT,
                vintage_as_of=retrieved_at,
                redistribution_note=self.metadata.redistribution_note,
            )
        records: list[BitemporalRecord] = []
        identities: set[str] = set()
        for position, (row, valid_from) in enumerate(zip(rows, dates, strict=True)):
            self._validate_semantics(row, position)
            identity = self._text(row, "id", position)
            if identity in identities:
                raise SourceSchemaError(f"duplicate CFTC response-local ID: {identity}")
            identities.add(identity)
            record_id = f"{self.metadata.adapter_id}:{identity}"
            if len(record_id) > 300:
                record_id = (
                    f"{self.metadata.adapter_id}:sha256:"
                    f"{hashlib.sha256(identity.encode()).hexdigest()}"
                )
            contract_code = self._text(row, "cftc_contract_market_code", position)
            if source is None:  # pragma: no cover - guarded by zip(rows, dates)
                raise AssertionError("non-empty CFTC page must have a source reference")
            records.append(
                BitemporalRecord(
                    record_id=record_id,
                    entity_id=f"cftc_contract:{contract_code}",
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
            f"CFTC {self.spec.slug} rows are immutable published observations, but this generic "
            "API page lacks row-specific release timestamps; historical decision-time use "
            "requires separate release evidence."
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
            historical_replay_eligible=False,
            warnings=(warning,),
        )
        artifact = RawArtifact(sha256=digest, content_type=content_type, content=content)
        return AdapterBatch(records=tuple(records), receipts=(receipt,), artifacts=(artifact,))

    def fetch_all(self, *, page_size: int = 10_000, max_pages: int = 100) -> AdapterBatch:
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        records: list[BitemporalRecord] = []
        receipts: list[FetchReceipt] = []
        artifacts: list[RawArtifact] = []
        identities: set[str] = set()
        complete = False
        for page_number in range(max_pages):
            page = self.fetch_page(limit=page_size, offset=page_number * page_size)
            duplicate = identities.intersection(record.record_id for record in page.records)
            if duplicate:
                raise SourceSchemaError(
                    f"CFTC pagination produced duplicate identity: {min(duplicate)}"
                )
            identities.update(record.record_id for record in page.records)
            records.extend(page.records)
            receipts.extend(page.receipts)
            artifacts.extend(page.artifacts)
            if len(page.records) < page_size:
                complete = True
                break
        if not complete:
            raise SourceSchemaError(
                f"CFTC pagination reached max_pages={max_pages} before a short terminal page"
            )
        return AdapterBatch(
            records=tuple(records), receipts=tuple(receipts), artifacts=tuple(artifacts)
        )

    def _normalize_row(self, value: Any, position: int) -> dict[str, Any]:
        original = require_json_object(value, f"CFTC {self.spec.slug} row[{position}]")
        normalized: dict[str, Any] = {}
        for key, item in original.items():
            lowered = key.lower()
            if lowered in normalized:
                raise SourceSchemaError(f"CFTC row contains case-colliding field names: {key!r}")
            normalized[lowered] = item
        missing = set(self.spec.required_fields) - set(normalized)
        if missing:
            raise SourceSchemaError(f"CFTC row is missing fields: {sorted(missing)}")
        return normalized

    def _validate_semantics(self, row: dict[str, Any], position: int) -> None:
        for field in (
            "id",
            "market_and_exchange_names",
            "yyyy_report_week_ww",
            "contract_market_name",
            "cftc_contract_market_code",
            "commodity_name",
        ):
            self._text(row, field, position)
        if self.spec.expected_mode is not None:
            mode = self._text(row, "futonly_or_combined", position)
            if mode != self.spec.expected_mode:
                raise SourceSchemaError(
                    f"CFTC {self.spec.slug} row[{position}] mode is {mode!r}, "
                    f"expected {self.spec.expected_mode!r}"
                )
        open_interest = self._position(row, "open_interest_all", position)
        reportable_long = self._position(row, "tot_rept_positions_long_all", position)
        reportable_short = self._position(row, "tot_rept_positions_short", position)
        nonreportable_long = self._position(row, "nonrept_positions_long_all", position)
        nonreportable_short = self._position(row, "nonrept_positions_short_all", position)
        component_long = sum(
            self._position(row, field, position) for field in self.spec.long_components
        )
        component_short = sum(
            self._position(row, field, position) for field in self.spec.short_components
        )
        tolerance = self.spec.balance_tolerance
        equations = (
            ("long open-interest", open_interest, reportable_long + nonreportable_long),
            ("short open-interest", open_interest, reportable_short + nonreportable_short),
            ("long classification", reportable_long, component_long),
            ("short classification", reportable_short, component_short),
        )
        for label, expected, actual in equations:
            if abs(expected - actual) > tolerance:
                raise SourceSchemaError(
                    f"CFTC {self.spec.slug} row[{position}] {label} balance differs by "
                    f"{actual - expected} contracts, beyond tolerance {tolerance}"
                )

    @staticmethod
    def _report_date(value: Any) -> datetime:
        if not isinstance(value, str):
            raise SourceSchemaError("CFTC report date must be text")
        match = _REPORT_DATE_PATTERN.fullmatch(value)
        if match is None:
            raise SourceSchemaError("CFTC report date must be midnight ISO calendar time")
        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=UTC,
            )
        except ValueError as error:
            raise SourceSchemaError("CFTC report date is not a valid calendar date") from error

    @staticmethod
    def _text(row: dict[str, Any], field: str, position: int) -> str:
        value = row[field]
        if not isinstance(value, str) or not value.strip():
            raise SourceSchemaError(f"CFTC row[{position}] field {field} must be non-empty text")
        return value.strip()

    @staticmethod
    def _position(row: dict[str, Any], field: str, position: int) -> int:
        value = row[field]
        if isinstance(value, bool):
            raise SourceSchemaError(f"CFTC row[{position}] field {field} must be an integer")
        text = str(value)
        if _INTEGER_PATTERN.fullmatch(text) is None:
            raise SourceSchemaError(f"CFTC row[{position}] field {field} must be an integer")
        parsed = int(text)
        if parsed < 0:
            raise SourceSchemaError(f"CFTC row[{position}] field {field} must be non-negative")
        return parsed
