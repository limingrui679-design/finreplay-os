"""Archived U.S. Treasury Daily Treasury Statement report adapter."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from io import BytesIO
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
_MONEY = re.compile(r"^[0-9]{1,3}(?:,[0-9]{3})*$|^[0-9]+$")
_VERIFIED_PUBLICATION_DATES = {
    date(2023, 5, 31): date(2023, 6, 1),
    date(2023, 6, 1): date(2023, 6, 2),
    date(2023, 6, 2): date(2023, 6, 5),
}


class TreasuryDTSPublishedReportAdapter:
    """Retrieve one strictly bounded, date-stamped DTS PDF snapshot."""

    availability_rule = (
        "Treasury states that each Daily Treasury Statement is available by 4:00 p.m. on the "
        "following business day. For the explicitly verified 2023 report calendar in this "
        "adapter, FinReplay uses 16:00 America/New_York on that following business day as a "
        "conservative knowledge deadline, not the exact publication instant."
    )
    metadata = AdapterMetadata(
        adapter_id="treasury.dts.published_report",
        title="U.S. Treasury archived Daily Treasury Statement TGA balance",
        publisher="U.S. Department of the Treasury, Bureau of the Fiscal Service",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://home.treasury.gov/data/receipts-outlays"
        ),
        allowed_hosts=("fiscaldata.treasury.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only explicitly approved historical report dates sequentially; do not "
            "crawl or enumerate the report archive."
        ),
        pagination_policy="Each date-stamped DTS PDF is one complete report without pagination.",
        availability_rule=availability_rule,
        revision_behavior=(
            "Each date-stamped PDF is content-addressed as a versioned report snapshot. Later "
            "API values or reports never overwrite an earlier report fact."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Keep full Treasury report PDFs in local content-addressed storage. Repository "
            "scenarios retain only minimal reported balances, source links, hashes, and Treasury "
            "attribution."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, report_date: date) -> None:
        if report_date not in _VERIFIED_PUBLICATION_DATES:
            raise ValueError("report date is not in the verified DTS publication calendar")
        self.http = http
        self.report_date = report_date
        self.publication_date = _VERIFIED_PUBLICATION_DATES[report_date]
        self.endpoint = (
            "https://fiscaldata.treasury.gov/static-data/published-reports/dts/"
            f"DailyTreasuryStatement_{report_date:%Y%m%d}.pdf"
        )

    def fetch(self) -> AdapterBatch:
        response, content, retrieved_at = self.http.get(
            self.endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(response.request_url)
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        if content_type != "application/pdf":
            raise SourceSchemaError(f"unexpected DTS report content type: {content_type!r}")
        values = self._parse_report(content)
        digest = source_response_sha256(content)
        vintage_as_of = datetime.combine(self.report_date, time.min, tzinfo=UTC)
        available_at = datetime.combine(
            self.publication_date,
            time(hour=16),
            tzinfo=_NEW_YORK,
        ).astimezone(UTC)
        if retrieved_at < available_at:
            raise SourceSchemaError("selected DTS report is not yet conservatively knowable")
        source_version = f"DTS:{self.report_date.isoformat()}:sha256:{digest[:24]}"
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(response.request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            vintage_as_of=vintage_as_of,
            redistribution_note=self.metadata.redistribution_note,
        )
        record = BitemporalRecord(
            record_id=f"{self.metadata.adapter_id}:{self.report_date:%Y%m%d}:tga_closing_balance",
            entity_id="us_treasury:treasury_general_account",
            source=source,
            interval=BitemporalInterval(
                valid_from=vintage_as_of,
                published_at=available_at,
                available_at=available_at,
                ingested_at=retrieved_at,
                availability_rule=self.availability_rule,
                availability_confidence=1.0,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload={
                "report_date": self.report_date.isoformat(),
                "publication_business_date": self.publication_date.isoformat(),
                "metric": "tga_closing_balance",
                "value_millions": values["closing"],
                "opening_balance_millions": values["opening"],
                "deposits_millions": values["deposits"],
                "withdrawals_millions": values["withdrawals"],
                "unit": "Millions of Dollars",
                "table": "Daily Treasury Statement Table I",
                "arithmetic_verified": True,
                "availability_method": (
                    "official_following_business_day_deadline_1600_america_new_york"
                ),
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
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            historical_replay_eligible=True,
            warnings=(
                "The knowledge timestamp is Treasury's following-business-day 4:00 p.m. "
                "deadline, not a claim about the exact publication instant.",
                "Only the explicitly verified three-date publication calendar is supported.",
                "Full Treasury PDFs remain download-only local evidence and are not redistributed.",
            ),
        )
        return AdapterBatch(
            records=(record,),
            receipts=(receipt,),
            artifacts=(RawArtifact(sha256=digest, content_type=content_type, content=content),),
        )

    def _parse_report(self, content: bytes) -> dict[str, int]:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("DTS report is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 4:
                raise SourceSchemaError("DTS report must contain exactly four pages")
            extracted = reader.pages[0].extract_text()
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("DTS report PDF could not be parsed") from error
        if not isinstance(extracted, str) or not extracted.strip():
            raise SourceSchemaError("DTS report first page has no extractable text")
        text = " ".join(extracted.split())
        display_date = (
            f"{self.report_date:%A, %B} {self.report_date.day}, {self.report_date:%Y}"
        )
        required_markers = (
            "DAILY TREASURY STATEMENT",
            "Cash and debt operations of the United States Treasury",
            display_date,
            "TABLE I - Operating Cash Balance",
            "(Detail, rounded in millions, may not add to totals)",
        )
        if any(marker not in text for marker in required_markers):
            raise SourceSchemaError("DTS report identity or Table I heading does not match request")
        values = {
            "opening": self._single_money_value(
                text,
                "Treasury General Account (TGA) Opening Balance",
            ),
            "deposits": self._single_money_value(text, "Total TGA Deposits (Table II)"),
            "withdrawals": self._single_money_value(
                text,
                "Total TGA Withdrawals (Table II) (-)",
            ),
            "closing": self._single_money_value(
                text,
                "Treasury General Account (TGA) Closing Balance",
            ),
        }
        if values["opening"] + values["deposits"] - values["withdrawals"] != values["closing"]:
            raise SourceSchemaError("DTS Table I balances do not reconcile arithmetically")
        if values["closing"] <= 0:
            raise SourceSchemaError("DTS TGA closing balance must be positive")
        return values

    @staticmethod
    def _single_money_value(text: str, label: str) -> int:
        pattern = re.compile(
            rf"{re.escape(label)}\s+\$?\s*([0-9][0-9,]*)(?![0-9.,])"
        )
        matches = pattern.findall(text)
        if len(matches) != 1 or _MONEY.fullmatch(matches[0]) is None:
            raise SourceSchemaError(f"DTS Table I must contain one valid {label} value")
        return int(matches[0].replace(",", ""))

    def _validate_response_url(self, response_url: str) -> None:
        parsed = urlparse(response_url)
        expected_path = (
            "/static-data/published-reports/dts/"
            f"DailyTreasuryStatement_{self.report_date:%Y%m%d}.pdf"
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError("DTS response URL does not match the requested report")
