"""Federal Reserve Bank of New York Markets API product adapters."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
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
_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_CUSIP_PATTERN = re.compile(r"^[0-9A-Z*@#]{9}$")
_SERIES_BREAK_PATTERN = re.compile(r"^[A-Z0-9]{3,30}$")
_REFERENCE_RATE_TYPES = frozenset({"EFFR", "OBFR", "TGCR", "BGCR", "SOFR", "SOFRAI"})


class NYFedSemanticKind(StrEnum):
    """Independent source-specific invariant family for a Markets API product."""

    AMBS_OPERATION = "ambs_operation"
    FX_SWAP = "fx_swap"
    GUIDE_SHEET = "guide_sheet"
    PRIMARY_DEALER_ASOF = "primary_dealer_asof"
    REFERENCE_RATE = "reference_rate"
    REPO_OPERATION = "repo_operation"
    SECURITIES_LENDING = "securities_lending"
    SOMA_SUMMARY = "soma_summary"
    TREASURY_OPERATION = "treasury_operation"


@dataclass(frozen=True, slots=True)
class NYFedDatasetSpec:
    """One independently counted Markets API data product and parser contract."""

    slug: str
    title: str
    endpoint_path: str
    row_path: tuple[str, ...]
    required_fields: tuple[str, ...]
    identity_fields: tuple[str, ...]
    valid_time_field: str
    semantic_kind: NYFedSemanticKind
    object_row: bool = False

    @property
    def adapter_id(self) -> str:
        return f"nyfed.markets.{self.slug}"


NYFED_DATASET_SPECS: tuple[NYFedDatasetSpec, ...] = (
    NYFedDatasetSpec(
        slug="ambs_operations",
        title="New York Fed agency MBS operations",
        endpoint_path="api/ambs/all/results/summary/last/1.json",
        row_path=("ambs", "auctions"),
        required_fields=(
            "operationId",
            "operationDate",
            "operationType",
            "settlementDate",
            "lastUpdated",
        ),
        identity_fields=("operationId",),
        valid_time_field="operationDate",
        semantic_kind=NYFedSemanticKind.AMBS_OPERATION,
    ),
    NYFedDatasetSpec(
        slug="central_bank_liquidity_swaps",
        title="New York Fed central-bank liquidity swap operations",
        endpoint_path="api/fxs/usdollar/last/1.json",
        row_path=("fxSwaps", "operations"),
        required_fields=(
            "operationType",
            "counterparty",
            "currency",
            "tradeDate",
            "settlementDate",
            "maturityDate",
            "termInDays",
            "amount",
            "interestRate",
            "lastUpdated",
        ),
        identity_fields=(
            "operationType",
            "counterparty",
            "currency",
            "tradeDate",
            "maturityDate",
        ),
        valid_time_field="tradeDate",
        semantic_kind=NYFedSemanticKind.FX_SWAP,
    ),
    NYFedDatasetSpec(
        slug="primary_dealer_guidesheet",
        title="New York Fed FR 2004SI primary-dealer guide sheet",
        endpoint_path="api/guidesheets/si/latest.json",
        row_path=("guidesheet", "si"),
        required_fields=(
            "title",
            "reportWeeksFromDate",
            "reportWeeksToDate",
            "nextDistributionDate",
            "details",
        ),
        identity_fields=("reportWeeksFromDate", "reportWeeksToDate"),
        valid_time_field="reportWeeksToDate",
        semantic_kind=NYFedSemanticKind.GUIDE_SHEET,
        object_row=True,
    ),
    NYFedDatasetSpec(
        slug="primary_dealer_asof_dates",
        title="New York Fed primary-dealer statistics as-of dates",
        endpoint_path="api/pd/list/asof.json",
        row_path=("pd", "asofdates"),
        required_fields=("asof", "seriesbreak"),
        identity_fields=("asof", "seriesbreak"),
        valid_time_field="asof",
        semantic_kind=NYFedSemanticKind.PRIMARY_DEALER_ASOF,
    ),
    NYFedDatasetSpec(
        slug="reference_rates",
        title="New York Fed secured and unsecured reference rates",
        endpoint_path="api/rates/all/latest.json",
        row_path=("refRates",),
        required_fields=("effectiveDate", "type", "revisionIndicator"),
        identity_fields=("effectiveDate", "type"),
        valid_time_field="effectiveDate",
        semantic_kind=NYFedSemanticKind.REFERENCE_RATE,
    ),
    NYFedDatasetSpec(
        slug="repo_operations",
        title="New York Fed repo and reverse-repo operations",
        endpoint_path="api/rp/all/all/results/last/1.json",
        row_path=("repo", "operations"),
        required_fields=(
            "operationId",
            "operationDate",
            "settlementDate",
            "maturityDate",
            "operationType",
            "totalAmtSubmitted",
            "totalAmtAccepted",
            "lastUpdated",
        ),
        identity_fields=("operationId",),
        valid_time_field="operationDate",
        semantic_kind=NYFedSemanticKind.REPO_OPERATION,
    ),
    NYFedDatasetSpec(
        slug="securities_lending",
        title="New York Fed securities-lending operations",
        endpoint_path="api/seclending/all/results/summary/last/1.json",
        row_path=("seclending", "operations"),
        required_fields=(
            "operationId",
            "operationDate",
            "settlementDate",
            "maturityDate",
            "totalParAmtSubmitted",
            "totalParAmtAccepted",
            "lastUpdated",
        ),
        identity_fields=("operationId",),
        valid_time_field="operationDate",
        semantic_kind=NYFedSemanticKind.SECURITIES_LENDING,
    ),
    NYFedDatasetSpec(
        slug="soma_summary",
        title="New York Fed System Open Market Account holdings summary",
        endpoint_path="api/soma/summary.json",
        row_path=("soma", "summary"),
        required_fields=(
            "asOfDate",
            "mbs",
            "cmbs",
            "tips",
            "frn",
            "notesbonds",
            "bills",
            "agencies",
            "total",
        ),
        identity_fields=("asOfDate",),
        valid_time_field="asOfDate",
        semantic_kind=NYFedSemanticKind.SOMA_SUMMARY,
    ),
    NYFedDatasetSpec(
        slug="treasury_operations",
        title="New York Fed Treasury securities operations",
        endpoint_path="api/tsy/all/results/summary/last/1.json",
        row_path=("treasury", "auctions"),
        required_fields=(
            "operationId",
            "operationDate",
            "settlementDate",
            "maturityRangeStart",
            "maturityRangeEnd",
            "totalParAmtSubmitted",
            "totalParAmtAccepted",
            "lastUpdated",
        ),
        identity_fields=("operationId",),
        valid_time_field="operationDate",
        semantic_kind=NYFedSemanticKind.TREASURY_OPERATION,
    ),
)

NYFED_DATASET_BY_SLUG = {spec.slug: spec for spec in NYFED_DATASET_SPECS}


class NYFedMarketsAdapter:
    """Strict latest-snapshot parser for one documented Markets API product."""

    endpoint_root = "https://markets.newyorkfed.org/"

    def __init__(self, http: SafeHttpClient, spec: NYFedDatasetSpec) -> None:
        self.http = http
        self.spec = spec
        self.endpoint = f"{self.endpoint_root}{spec.endpoint_path}"
        special_rate_notice = (
            " Reference-rate reuse also requires the specific notice and non-endorsement "
            "disclaimers in the New York Fed Terms of Use."
            if spec.semantic_kind is NYFedSemanticKind.REFERENCE_RATE
            else ""
        )
        self.metadata = AdapterMetadata(
            adapter_id=spec.adapter_id,
            title=spec.title,
            publisher="Federal Reserve Bank of New York",
            documentation_url=_HTTP_URL_ADAPTER.validate_python(
                "https://markets.newyorkfed.org/static/docs/markets-api.html"
            ),
            allowed_hosts=("markets.newyorkfed.org",),
            authentication=AuthenticationMode.NONE,
            rate_limit_policy=(
                "The official OpenAPI document publishes no numeric quota. Requests are "
                "sequential, bounded, and never retry HTTP failures implicitly."
            ),
            pagination_policy=(
                "The selected official latest/last-one/summary endpoint is a single bounded "
                "response; no undocumented pagination is assumed."
            ),
            availability_rule=(
                "The API response is a current snapshot. Economic dates and lastUpdated fields "
                "are preserved but this connector makes the exact values knowable only at the "
                "recorded retrieval time."
            ),
            revision_behavior=(
                "The New York Fed may revise content, calculation methods, schedules, and rate "
                "practices. Every retrieved response is content-addressed; missing prior versions "
                "are never reconstructed from a current response."
            ),
            temporal_coverage=TemporalCoverage.LATEST_ONLY,
            license_class=LicenseClass.REVIEW_REQUIRED,
            redistribution_note=(
                "Before redistributing source content, apply the current New York Fed Terms of "
                "Use, source identification, attribution, same-permissions, modification, and "
                "non-endorsement conditions. Preserve only code, hashes, receipts, and bounded "
                "fixtures in this repository." + special_rate_notice
            ),
        )

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type not in {"application/json", "text/json"}:
            raise SourceSchemaError(f"unexpected New York Fed content type: {content_type!r}")
        try:
            root = require_json_object(response.json(), f"New York Fed {self.spec.slug} response")
        except (ValueError, TypeError) as error:
            raise SourceSchemaError(
                f"New York Fed {self.spec.slug} response is not valid JSON"
            ) from error
        rows = self._extract_rows(root)
        digest = source_response_sha256(content)
        valid_times = [
            self._date(row[self.spec.valid_time_field], self.spec.valid_time_field)
            for row in rows
        ]
        through = max(valid_times).date().isoformat()
        source_version = f"latest-through:{through}:sha256:{digest[:24]}"
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
        for position, (row, valid_from) in enumerate(zip(rows, valid_times, strict=True)):
            self._validate_semantics(row, position)
            identity = self._identity(row)
            if identity in identities:
                raise SourceSchemaError(f"duplicate New York Fed record identity: {identity}")
            identities.add(identity)
            record_suffix = identity
            if len(self.metadata.adapter_id) + len(record_suffix) + 1 > 300:
                record_suffix = f"sha256:{hashlib.sha256(identity.encode()).hexdigest()}"
            records.append(
                BitemporalRecord(
                    record_id=f"{self.metadata.adapter_id}:{record_suffix}",
                    entity_id=f"nyfed_product:{self.spec.slug}",
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
                    payload=dict(row),
                )
            )
        warning = (
            f"Latest-only New York Fed {self.spec.slug} snapshot: economic, release, and "
            "lastUpdated fields do not prove when this exact current value first became public."
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

    def _extract_rows(self, root: dict[str, Any]) -> list[dict[str, Any]]:
        current: Any = root
        for component in self.spec.row_path:
            current = require_json_object(
                current, f"New York Fed {self.spec.slug} path before {component}"
            ).get(component)
        if self.spec.object_row:
            values: list[Any] = [
                require_json_object(current, f"New York Fed {self.spec.slug} object row")
            ]
        else:
            if not isinstance(current, list):
                raise SourceSchemaError(
                    f"New York Fed {self.spec.slug} row path must resolve to a list"
                )
            values = current
        if not values:
            raise SourceSchemaError(f"New York Fed {self.spec.slug} returned no records")
        rows: list[dict[str, Any]] = []
        for position, value in enumerate(values):
            row = require_json_object(value, f"New York Fed {self.spec.slug} row[{position}]")
            missing = set(self.spec.required_fields) - set(row)
            if missing:
                raise SourceSchemaError(
                    f"New York Fed {self.spec.slug} row is missing fields: {sorted(missing)}"
                )
            rows.append(row)
        return rows

    def _identity(self, row: dict[str, Any]) -> str:
        parts: list[str] = []
        for field in self.spec.identity_fields:
            value = row.get(field)
            if value is None or str(value).strip() == "":
                raise SourceSchemaError(f"New York Fed identity field {field} is empty")
            parts.append(str(value).strip())
        return ":".join(parts)

    def _validate_semantics(self, row: dict[str, Any], position: int) -> None:
        kind = self.spec.semantic_kind
        context = f"New York Fed {self.spec.slug} row[{position}]"
        if kind is NYFedSemanticKind.AMBS_OPERATION:
            self._validate_dates(row, ("operationDate", "settlementDate"), context)
            pairs = (
                ("totalSubmittedOrigFace", "totalAcceptedOrigFace"),
                ("totalSubmittedCurrFace", "totalAcceptedCurrFace"),
                ("totalAmtSubmittedPar", "totalAmtAcceptedPar"),
            )
            if not any(self._validate_optional_amount_pair(row, *pair, context) for pair in pairs):
                raise SourceSchemaError(f"{context} has no complete submitted/accepted amount pair")
        elif kind is NYFedSemanticKind.FX_SWAP:
            self._validate_dates(
                row, ("tradeDate", "settlementDate", "maturityDate"), context
            )
            self._nonnegative(row["termInDays"], "termInDays", context)
            self._nonnegative(row["amount"], "amount", context)
            self._decimal(row["interestRate"], "interestRate", context)
        elif kind is NYFedSemanticKind.GUIDE_SHEET:
            self._validate_dates(
                row,
                ("reportWeeksFromDate", "reportWeeksToDate", "nextDistributionDate"),
                context,
            )
            details = row["details"]
            if not isinstance(details, list) or not details:
                raise SourceSchemaError(f"{context} details must be a non-empty list")
            cusips: set[str] = set()
            for index, value in enumerate(details):
                detail = require_json_object(value, f"{context} details[{index}]")
                required = {"formLine", "secType", "cusip", "issueDate", "maturityDate"}
                missing = required - set(detail)
                if missing:
                    raise SourceSchemaError(
                        f"{context} guide detail missing fields: {sorted(missing)}"
                    )
                cusip = detail["cusip"]
                if not isinstance(cusip, str) or not _CUSIP_PATTERN.fullmatch(cusip):
                    raise SourceSchemaError(f"{context} has invalid guide-sheet CUSIP")
                if cusip in cusips:
                    raise SourceSchemaError(f"{context} has duplicate guide-sheet CUSIP")
                cusips.add(cusip)
                self._validate_dates(detail, ("issueDate", "maturityDate"), context)
        elif kind is NYFedSemanticKind.PRIMARY_DEALER_ASOF:
            seriesbreak = row["seriesbreak"]
            if not isinstance(seriesbreak, str) or not _SERIES_BREAK_PATTERN.fullmatch(seriesbreak):
                raise SourceSchemaError(f"{context} has invalid seriesbreak")
        elif kind is NYFedSemanticKind.REFERENCE_RATE:
            rate_type = row["type"]
            if rate_type not in _REFERENCE_RATE_TYPES:
                raise SourceSchemaError(f"{context} has unknown reference-rate type")
            measure_fields = (
                "percentRate",
                "average30day",
                "average90day",
                "average180day",
                "index",
            )
            present = [
                field
                for field in measure_fields
                if field in row and row[field] not in (None, "")
            ]
            if not present:
                raise SourceSchemaError(f"{context} has no reference-rate measure")
            for field in present:
                self._decimal(row[field], field, context)
            if "volumeInBillions" in row:
                self._nonnegative(row["volumeInBillions"], "volumeInBillions", context)
        elif kind is NYFedSemanticKind.REPO_OPERATION:
            self._validate_dates(
                row, ("operationDate", "settlementDate", "maturityDate"), context
            )
            self._validate_amount_pair(row, "totalAmtSubmitted", "totalAmtAccepted", context)
        elif kind is NYFedSemanticKind.SECURITIES_LENDING:
            self._validate_dates(
                row, ("operationDate", "settlementDate", "maturityDate"), context
            )
            self._validate_amount_pair(
                row, "totalParAmtSubmitted", "totalParAmtAccepted", context
            )
        elif kind is NYFedSemanticKind.SOMA_SUMMARY:
            component_fields = ("mbs", "cmbs", "tips", "frn", "notesbonds", "bills", "agencies")
            components = sum(
                (
                    self._decimal(row[field], field, context, blank_as_zero=True)
                    for field in component_fields
                ),
                start=Decimal(0),
            )
            total = self._nonnegative(row["total"], "total", context)
            # The API displays the seven component fields at whole-dollar precision while some
            # historical totals retain cents. Independent half-dollar rounding across seven
            # displayed components therefore permits at most $3.50 of arithmetic drift.
            rounding_bound = Decimal("0.50") * len(component_fields)
            if abs(total - components) > rounding_bound:
                raise SourceSchemaError(f"{context} SOMA total does not equal reported components")
        elif kind is NYFedSemanticKind.TREASURY_OPERATION:
            self._validate_dates(
                row,
                (
                    "operationDate",
                    "settlementDate",
                    "maturityRangeStart",
                    "maturityRangeEnd",
                ),
                context,
            )
            self._validate_amount_pair(
                row, "totalParAmtSubmitted", "totalParAmtAccepted", context
            )

    @classmethod
    def _validate_dates(
        cls, row: dict[str, Any], fields: tuple[str, ...], context: str
    ) -> None:
        parsed = [cls._date(row.get(field), field) for field in fields]
        if parsed != sorted(parsed):
            raise SourceSchemaError(f"{context} dates are not chronological: {fields}")

    @staticmethod
    def _date(value: Any, field: str) -> datetime:
        if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
            raise SourceSchemaError(f"New York Fed {field} must be YYYY-MM-DD")
        try:
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError as error:
            raise SourceSchemaError(f"New York Fed {field} is not a valid date") from error

    @classmethod
    def _validate_optional_amount_pair(
        cls,
        row: dict[str, Any],
        submitted_field: str,
        accepted_field: str,
        context: str,
    ) -> bool:
        submitted = row.get(submitted_field)
        accepted = row.get(accepted_field)
        if submitted in (None, "") and accepted in (None, ""):
            return False
        if submitted in (None, "") or accepted in (None, ""):
            raise SourceSchemaError(f"{context} has a partial submitted/accepted amount pair")
        cls._validate_amount_pair(row, submitted_field, accepted_field, context)
        return True

    @classmethod
    def _validate_amount_pair(
        cls,
        row: dict[str, Any],
        submitted_field: str,
        accepted_field: str,
        context: str,
    ) -> None:
        submitted = cls._nonnegative(row.get(submitted_field), submitted_field, context)
        accepted = cls._nonnegative(row.get(accepted_field), accepted_field, context)
        if accepted > submitted:
            raise SourceSchemaError(f"{context} accepted amount exceeds submitted amount")

    @classmethod
    def _nonnegative(cls, value: Any, field: str, context: str) -> Decimal:
        parsed = cls._decimal(value, field, context)
        if parsed < 0:
            raise SourceSchemaError(f"{context} {field} must be non-negative")
        return parsed

    @staticmethod
    def _decimal(
        value: Any,
        field: str,
        context: str,
        *,
        blank_as_zero: bool = False,
    ) -> Decimal:
        if blank_as_zero and value in (None, ""):
            return Decimal(0)
        if isinstance(value, bool) or value is None or str(value).strip() == "":
            raise SourceSchemaError(f"{context} {field} must be numeric")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise SourceSchemaError(f"{context} {field} must be numeric") from error
        if not parsed.is_finite():
            raise SourceSchemaError(f"{context} {field} must be finite")
        return parsed
