"""Archived TreasuryDirect 91-day bill auction-result adapter."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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
_AUCTION_ROOT_TAG = "{http://www.treasurydirect.gov/}AuctionData"
_INTEGER = re.compile(r"^(?:0|[1-9][0-9]*)$")
_RATE = re.compile(r"^(?:0|[1-9][0-9]?)\.[0-9]{3}$")
_RATIO = re.compile(r"^(?:0|[1-9][0-9]?)\.[0-9]{2}$")
_CLOCK = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_CUSIP = re.compile(r"^[0-9A-Z*@#]{9}$")


@dataclass(frozen=True, slots=True)
class _ReleaseIdentity:
    cusip: str
    announcement_date: date
    issue_date: date
    maturity_date: date
    release_time: str
    filename: str


_VERIFIED_AUCTIONS = {
    date(2020, 3, 9): _ReleaseIdentity(
        cusip="912796TZ2",
        announcement_date=date(2020, 3, 5),
        issue_date=date(2020, 3, 12),
        maturity_date=date(2020, 6, 11),
        release_time="11:32",
        filename="R_20200309_2",
    ),
    date(2020, 3, 16): _ReleaseIdentity(
        cusip="912796SV2",
        announcement_date=date(2020, 3, 12),
        issue_date=date(2020, 3, 19),
        maturity_date=date(2020, 6, 18),
        release_time="11:32",
        filename="R_20200316_2",
    ),
    date(2020, 3, 23): _ReleaseIdentity(
        cusip="912796UA5",
        announcement_date=date(2020, 3, 19),
        issue_date=date(2020, 3, 26),
        maturity_date=date(2020, 6, 25),
        release_time="11:31",
        filename="R_20200323_2",
    ),
}


@dataclass(frozen=True, slots=True)
class _ParsedAuction:
    cusip: str
    announcement_date: date
    auction_date: date
    issue_date: date
    maturity_date: date
    release_time: str
    high_rate: Decimal
    median_rate: Decimal
    low_rate: Decimal
    investment_rate: Decimal
    high_price: Decimal
    high_allocation: Decimal
    bid_to_cover: Decimal
    competitive_tendered: int
    competitive_accepted: int
    noncompetitive_accepted: int
    fima_tendered: int
    fima_accepted: int
    soma_tendered: int
    soma_accepted: int
    total_tendered: int
    total_accepted: int
    primary_tendered: int
    primary_accepted: int
    direct_tendered: int
    direct_accepted: int
    indirect_tendered: int
    indirect_accepted: int
    treasury_direct_accepted: int

    @property
    def subtotal_tendered(self) -> int:
        return (
            self.competitive_tendered
            + self.noncompetitive_accepted
            + self.fima_tendered
        )

    @property
    def subtotal_accepted(self) -> int:
        return (
            self.competitive_accepted
            + self.noncompetitive_accepted
            + self.fima_accepted
        )


class TreasuryAuction91DayArchiveAdapter:
    """Retrieve one fixed 2020 91-day bill result as paired XML and PDF."""

    availability_rule = (
        "Treasury's timeline says the results XML delivery time has been the official auction "
        "release time since 2003. The selected XML records that time and its paired PDF says For "
        "Immediate Release on the auction date. FinReplay interprets it under Treasury's "
        "America/New_York auction-time convention but delays eligibility until the next local "
        "midnight. This is conservative, not a claimed first-observation second."
    )
    metadata = AdapterMetadata(
        adapter_id="treasury.auctions.archived_91_day_bill_results",
        title="TreasuryDirect archived 91-day bill auction results",
        publisher="U.S. Department of the Treasury, Bureau of the Fiscal Service",
        documentation_url=_HTTP_URL_ADAPTER.validate_python(
            "https://www.treasurydirect.gov/research-center/timeline/auctions/"
        ),
        allowed_hosts=("www.treasurydirect.gov",),
        authentication=AuthenticationMode.NONE,
        rate_limit_policy=(
            "Retrieve only the three explicitly approved March 2020 result pairs "
            "sequentially; do not crawl or enumerate the auction archive."
        ),
        pagination_policy=(
            "Each auction uses one complete results XML and one complete one-page PDF without "
            "pagination."
        ),
        availability_rule=availability_rule,
        revision_behavior=(
            "Each date- and CUSIP-specific XML/PDF pair is content-addressed as one result "
            "snapshot. Later query-table rows or migrated file metadata never overwrite it."
        ),
        temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
        license_class=LicenseClass.DOWNLOAD_ONLY,
        redistribution_note=(
            "Keep full Treasury XML and PDF result files in local content-addressed storage. "
            "Repository scenarios retain only minimal auction facts, URLs, hashes, and "
            "attribution; Treasury seals and marks are not reused."
        ),
    )

    def __init__(self, http: SafeHttpClient, *, auction_date: date) -> None:
        if auction_date not in _VERIFIED_AUCTIONS:
            raise ValueError("auction date is not in the verified 91-day bill calendar")
        self.http = http
        self.auction_date = auction_date
        self.identity = _VERIFIED_AUCTIONS[auction_date]
        filename = self.identity.filename
        self.xml_endpoint = f"https://www.treasurydirect.gov/xml/{filename}.xml"
        self.pdf_endpoint = (
            "https://www.treasurydirect.gov/instit/annceresult/press/preanre/2020/"
            f"{filename}.pdf"
        )

    def fetch(self) -> AdapterBatch:
        xml_response, xml_content, xml_retrieved_at = self.http.get(
            self.xml_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        pdf_response, pdf_content, pdf_retrieved_at = self.http.get(
            self.pdf_endpoint,
            allowed_hosts=self.metadata.allowed_hosts,
        )
        self._validate_response_url(xml_response.request_url, kind="xml")
        self._validate_response_url(pdf_response.request_url, kind="pdf")
        xml_content_type = xml_response.headers.get("Content-Type", "").split(";", 1)[0]
        if xml_content_type not in {"application/xml", "text/xml"}:
            raise SourceSchemaError(
                f"unexpected Treasury auction XML content type: {xml_content_type!r}"
            )
        pdf_content_type = pdf_response.headers.get("Content-Type", "").split(";", 1)[0]
        if pdf_content_type != "application/pdf":
            raise SourceSchemaError(
                f"unexpected Treasury auction PDF content type: {pdf_content_type!r}"
            )
        parsed = self._parse_xml(xml_content)
        self._validate_pdf(pdf_content, parsed)
        release_clock = time.fromisoformat(parsed.release_time)
        published_at = datetime.combine(
            self.auction_date,
            release_clock,
            tzinfo=_NEW_YORK,
        ).astimezone(UTC)
        available_at = datetime.combine(
            self.auction_date + timedelta(days=1),
            time.min,
            tzinfo=_NEW_YORK,
        ).astimezone(UTC)
        retrieved_at = max(xml_retrieved_at, pdf_retrieved_at)
        if not published_at < available_at <= retrieved_at:
            raise SourceSchemaError("selected Treasury auction is not conservatively knowable")
        xml_digest = source_response_sha256(xml_content)
        pdf_digest = source_response_sha256(pdf_content)
        source_version = (
            f"TreasuryAuction:{self.auction_date.isoformat()}:{parsed.cusip}:"
            f"xml:{xml_digest[:20]}:pdf:{pdf_digest[:20]}"
        )
        source = SourceReference(
            source_id=self.metadata.adapter_id,
            publisher=self.metadata.publisher,
            url=_HTTP_URL_ADAPTER.validate_python(xml_response.request_url),
            retrieved_at=retrieved_at,
            source_version=source_version,
            sha256=xml_digest,
            license_class=self.metadata.license_class,
            temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
            vintage_as_of=published_at,
            redistribution_note=self.metadata.redistribution_note,
        )
        high_rate_basis_points = _basis_points(parsed.high_rate, "high discount rate")
        record = BitemporalRecord(
            record_id=(
                f"{self.metadata.adapter_id}:{self.auction_date:%Y%m%d}:"
                f"{parsed.cusip}:high_discount_rate"
            ),
            entity_id="us_treasury_auction:91_day_bill",
            source=source,
            interval=BitemporalInterval(
                valid_from=datetime.combine(self.auction_date, time.min, tzinfo=UTC),
                published_at=published_at,
                available_at=available_at,
                ingested_at=retrieved_at,
                availability_rule=self.availability_rule,
                availability_confidence=1.0,
            ),
            evidence_class=EvidenceClass.REPORTED,
            payload_schema_version="1.0.0",
            payload={
                "auction_date": self.auction_date.isoformat(),
                "announcement_date": parsed.announcement_date.isoformat(),
                "issue_date": parsed.issue_date.isoformat(),
                "maturity_date": parsed.maturity_date.isoformat(),
                "cusip": parsed.cusip,
                "security_term": "91-Day Bill",
                "metric": "high_discount_rate",
                "value_basis_points": high_rate_basis_points,
                "reported_high_rate_percent": _three_places(parsed.high_rate),
                "reported_median_rate_percent": _three_places(parsed.median_rate),
                "reported_low_rate_percent": _three_places(parsed.low_rate),
                "reported_investment_rate_percent": _three_places(
                    parsed.investment_rate
                ),
                "reported_price_per_100": _six_places(parsed.high_price),
                "bid_to_cover_ratio": _two_places(parsed.bid_to_cover),
                "competitive_tendered_dollars": parsed.competitive_tendered,
                "competitive_accepted_dollars": parsed.competitive_accepted,
                "subtotal_tendered_dollars": parsed.subtotal_tendered,
                "subtotal_accepted_dollars": parsed.subtotal_accepted,
                "total_tendered_dollars": parsed.total_tendered,
                "total_accepted_dollars": parsed.total_accepted,
                "official_release_time_local": parsed.release_time,
                "official_release_timezone": "America/New_York",
                "official_release_at": published_at.isoformat(),
                "unit": "Basis Points",
                "xml_pdf_crosscheck_verified": True,
                "auction_arithmetic_verified": True,
                "price_formula_verified": True,
                "release_pdf_url": pdf_response.request_url,
                "release_pdf_sha256": pdf_digest,
                "availability_method": "official_release_time_then_next_local_midnight",
            },
        )
        warnings = (
            "The XML release time is retained, but knowledge eligibility is delayed until the "
            "next America/New_York midnight rather than asserted at the first observable second.",
            "The paired one-page PDF and XML must match on identity, rates, price, amounts, "
            "bid-to-cover arithmetic, and result filename.",
            "Only the explicitly verified March 9, 16, and 23, 2020 auctions are supported.",
            "Full XML and PDF files remain local download evidence.",
        )
        receipts = (
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(xml_response.request_url),
                retrieved_at=xml_retrieved_at,
                status_code=xml_response.status_code,
                content_type=xml_content_type,
                response_sha256=xml_digest,
                response_bytes=len(xml_content),
                record_count=1,
                source_version=source_version,
                temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
            FetchReceipt(
                adapter_id=self.metadata.adapter_id,
                request_url=_HTTP_URL_ADAPTER.validate_python(pdf_response.request_url),
                retrieved_at=pdf_retrieved_at,
                status_code=pdf_response.status_code,
                content_type=pdf_content_type,
                response_sha256=pdf_digest,
                response_bytes=len(pdf_content),
                record_count=0,
                source_version=source_version,
                temporal_coverage=TemporalCoverage.VERSIONED_SNAPSHOT,
                historical_replay_eligible=True,
                warnings=warnings,
            ),
        )
        artifacts = (
            RawArtifact(
                sha256=xml_digest,
                content_type=xml_content_type,
                content=xml_content,
            ),
            RawArtifact(
                sha256=pdf_digest,
                content_type=pdf_content_type,
                content=pdf_content,
            ),
        )
        return AdapterBatch(records=(record,), receipts=receipts, artifacts=artifacts)

    def _parse_xml(self, content: bytes) -> _ParsedAuction:
        if not content.startswith(b"<?xml") or b"<!DOCTYPE" in content.upper():
            raise SourceSchemaError("Treasury auction XML declaration is invalid")
        try:
            root = ET.fromstring(content)
        except ET.ParseError as error:
            raise SourceSchemaError("Treasury auction XML could not be parsed") from error
        if root.tag != _AUCTION_ROOT_TAG:
            raise SourceSchemaError("Treasury auction XML root identity does not match")
        announcement = _single_element(root, "AuctionAnnouncement")
        results = _single_element(root, "AuctionResults")
        parsed = _ParsedAuction(
            cusip=_single_text(announcement, "CUSIP"),
            announcement_date=_iso_date(announcement, "AnnouncementDate"),
            auction_date=_iso_date(announcement, "AuctionDate"),
            issue_date=_iso_date(announcement, "IssueDate"),
            maturity_date=_iso_date(announcement, "MaturityDate"),
            release_time=_clock(results, "ReleaseTime"),
            high_rate=_rate(results, "HighDiscountRate"),
            median_rate=_rate(results, "MedianDiscountRate"),
            low_rate=_rate(results, "LowDiscountRate"),
            investment_rate=_rate(results, "InvestmentRate"),
            high_price=_decimal(results, "HighPrice", places=6),
            high_allocation=_decimal(results, "HighAllocationPercentage", places=2),
            bid_to_cover=_ratio(results, "BidToCoverRatio"),
            competitive_tendered=_integer(results, "CompetitiveTendered"),
            competitive_accepted=_integer(results, "CompetitiveAccepted"),
            noncompetitive_accepted=_integer(results, "NonCompetitiveAccepted"),
            fima_tendered=_integer(results, "FIMATendered"),
            fima_accepted=_integer(results, "FIMAAccepted"),
            soma_tendered=_integer(results, "SOMATendered"),
            soma_accepted=_integer(results, "SOMAAccepted"),
            total_tendered=_integer(results, "TotalTendered"),
            total_accepted=_integer(results, "TotalAccepted"),
            primary_tendered=_integer(results, "PrimaryDealerTendered"),
            primary_accepted=_integer(results, "PrimaryDealerAccepted"),
            direct_tendered=_integer(results, "DirectBidderTendered"),
            direct_accepted=_integer(results, "DirectBidderAccepted"),
            indirect_tendered=_integer(results, "IndirectBidderTendered"),
            indirect_accepted=_integer(results, "IndirectBidderAccepted"),
            treasury_direct_accepted=_integer(results, "TreasuryDirectAccepted"),
        )
        expected = self.identity
        identity = (
            _CUSIP.fullmatch(parsed.cusip) is not None
            and parsed.cusip == expected.cusip
            and parsed.announcement_date == expected.announcement_date
            and parsed.auction_date == self.auction_date
            and parsed.issue_date == expected.issue_date
            and parsed.maturity_date == expected.maturity_date
            and parsed.release_time == expected.release_time
            and _single_text(announcement, "SecurityTermWeekYear") == "13-WEEK"
            and _single_text(announcement, "SecurityTermDayMonth") == "91-DAY"
            and _single_text(announcement, "SecurityType") == "BILL"
            and _single_text(announcement, "TypeOfAuction") == "SINGLE PRICE"
            and _single_text(announcement, "CompetitiveClosingTime") == "11:30"
            and _single_text(announcement, "NonCompetitiveClosingTime") == "11:00"
            and _single_text(results, "ResultsPDFName") == f"{expected.filename}.pdf"
        )
        if not identity or not (
            parsed.announcement_date <= parsed.auction_date
            < parsed.issue_date
            < parsed.maturity_date
        ):
            raise SourceSchemaError("Treasury auction XML identity or calendar does not match")
        if not (
            Decimal("0")
            <= parsed.low_rate
            <= parsed.median_rate
            <= parsed.high_rate
            <= Decimal("100")
            and Decimal("0") <= parsed.high_allocation <= Decimal("100")
        ):
            raise SourceSchemaError("Treasury auction rate ordering is invalid")
        _basis_points(parsed.high_rate, "high discount rate")
        if parsed.competitive_tendered != (
            parsed.primary_tendered + parsed.direct_tendered + parsed.indirect_tendered
        ) or parsed.competitive_accepted != (
            parsed.primary_accepted + parsed.direct_accepted + parsed.indirect_accepted
        ):
            raise SourceSchemaError("Treasury auction bidder-category amounts do not reconcile")
        if parsed.fima_tendered != parsed.fima_accepted:
            raise SourceSchemaError("Treasury auction FIMA amounts do not reconcile")
        if parsed.soma_tendered != parsed.soma_accepted:
            raise SourceSchemaError("Treasury auction SOMA amounts do not reconcile")
        if parsed.total_tendered != parsed.subtotal_tendered + parsed.soma_tendered or (
            parsed.total_accepted != parsed.subtotal_accepted + parsed.soma_accepted
        ):
            raise SourceSchemaError("Treasury auction totals do not reconcile")
        computed_cover = (
            Decimal(parsed.subtotal_tendered) / Decimal(parsed.subtotal_accepted)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if computed_cover != parsed.bid_to_cover:
            raise SourceSchemaError("Treasury auction bid-to-cover ratio does not reconcile")
        computed_price = (
            Decimal("100") - parsed.high_rate * Decimal(91) / Decimal(360)
        ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        if computed_price != parsed.high_price:
            raise SourceSchemaError("Treasury auction bill price does not reconcile")
        return parsed

    def _validate_pdf(self, content: bytes, parsed: _ParsedAuction) -> None:
        if not content.startswith(b"%PDF-"):
            raise SourceSchemaError("Treasury auction result is not a PDF document")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if len(reader.pages) != 1:
                raise SourceSchemaError("Treasury auction result PDF must contain one page")
            page = reader.pages[0]
            if float(page.mediabox.width) != 612 or float(page.mediabox.height) != 792:
                raise SourceSchemaError("Treasury auction result PDF must use US Letter size")
            extracted = page.extract_text()
        except SourceSchemaError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError) as error:
            raise SourceSchemaError("Treasury auction result PDF could not be parsed") from error
        if not isinstance(extracted, str) or not extracted.strip():
            raise SourceSchemaError("Treasury auction result PDF has no extractable text")
        text_value = " ".join(extracted.split())
        subtotal_tendered = _money(parsed.subtotal_tendered)
        subtotal_accepted = _money(parsed.subtotal_accepted)
        markers = (
            "For Immediate Release",
            "CONTACT: Treasury Auctions",
            "TREASURY AUCTION RESULTS",
            self.auction_date.strftime("%B %d, %Y"),
            "Term and Type of Security 91-Day Bill",
            f"CUSIP Number {parsed.cusip}",
            (
                f"High Rate 1 {_three_places(parsed.high_rate)}% "
                f"Allotted at High {_two_places(parsed.high_allocation)}% "
                f"Price {_six_places(parsed.high_price)} "
                f"Investment Rate 2 {_three_places(parsed.investment_rate)}% "
                f"Median Rate 3 {_three_places(parsed.median_rate)}% "
                f"Low Rate 4 {_three_places(parsed.low_rate)}%"
            ),
            f"Issue Date {parsed.issue_date.strftime('%B %d, %Y')}",
            f"Maturity Date {parsed.maturity_date.strftime('%B %d, %Y')}",
            (
                f"Competitive ${_money(parsed.competitive_tendered)} "
                f"${_money(parsed.competitive_accepted)}"
            ),
            (
                f"Noncompetitive ${_money(parsed.noncompetitive_accepted)} "
                f"${_money(parsed.noncompetitive_accepted)}"
            ),
            (
                f"FIMA (Noncompetitive) ${_money(parsed.fima_tendered)} "
                f"${_money(parsed.fima_accepted)}"
            ),
            f"Subtotal 5 ${subtotal_tendered} ${subtotal_accepted}",
            f"SOMA ${_money(parsed.soma_tendered)} ${_money(parsed.soma_accepted)}",
            f"Total ${_money(parsed.total_tendered)} ${_money(parsed.total_accepted)}",
            (
                f"Primary Dealer 7 ${_money(parsed.primary_tendered)} "
                f"${_money(parsed.primary_accepted)}"
            ),
            (
                f"Direct Bidder 8 ${_money(parsed.direct_tendered)} "
                f"${_money(parsed.direct_accepted)}"
            ),
            (
                f"Indirect Bidder 9 ${_money(parsed.indirect_tendered)} "
                f"${_money(parsed.indirect_accepted)}"
            ),
            (
                f"5Bid-to-Cover Ratio: ${subtotal_tendered}/${subtotal_accepted} = "
                f"{_two_places(parsed.bid_to_cover)}"
            ),
            f"6Awards to TreasuryDirect = ${_money(parsed.treasury_direct_accepted)}.",
        )
        if text_value.count("TREASURY AUCTION RESULTS") != 1 or any(
            marker not in text_value for marker in markers
        ):
            raise SourceSchemaError("Treasury auction PDF identity or values do not match XML")

    def _validate_response_url(self, response_url: str, *, kind: str) -> None:
        parsed = urlparse(response_url)
        expected_path = (
            f"/xml/{self.identity.filename}.xml"
            if kind == "xml"
            else (
                "/instit/annceresult/press/preanre/2020/"
                f"{self.identity.filename}.pdf"
            )
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.metadata.allowed_hosts
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise SourceSchemaError(
                f"Treasury auction {kind.upper()} response URL does not match request"
            )


def _single_element(parent: ET.Element, name: str) -> ET.Element:
    matches = parent.findall(name)
    if len(matches) != 1:
        raise SourceSchemaError(f"Treasury auction XML must contain one {name}")
    return matches[0]


def _single_text(parent: ET.Element, name: str) -> str:
    element = _single_element(parent, name)
    value = element.text
    if value is None or not value.strip() or value != value.strip():
        raise SourceSchemaError(f"Treasury auction XML {name} must be non-empty canonical text")
    return value


def _iso_date(parent: ET.Element, name: str) -> date:
    raw = _single_text(parent, name)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as error:
        raise SourceSchemaError(f"Treasury auction XML {name} is not an ISO date") from error
    if parsed.isoformat() != raw:
        raise SourceSchemaError(f"Treasury auction XML {name} is not a canonical ISO date")
    return parsed


def _clock(parent: ET.Element, name: str) -> str:
    raw = _single_text(parent, name)
    if _CLOCK.fullmatch(raw) is None:
        raise SourceSchemaError(f"Treasury auction XML {name} is not a valid clock time")
    return raw


def _integer(parent: ET.Element, name: str) -> int:
    raw = _single_text(parent, name)
    if _INTEGER.fullmatch(raw) is None:
        raise SourceSchemaError(f"Treasury auction XML {name} is not a canonical integer")
    return int(raw)


def _rate(parent: ET.Element, name: str) -> Decimal:
    raw = _single_text(parent, name)
    if _RATE.fullmatch(raw) is None:
        raise SourceSchemaError(f"Treasury auction XML {name} must have three decimal places")
    return Decimal(raw)


def _ratio(parent: ET.Element, name: str) -> Decimal:
    raw = _single_text(parent, name)
    if _RATIO.fullmatch(raw) is None:
        raise SourceSchemaError(f"Treasury auction XML {name} must have two decimal places")
    return Decimal(raw)


def _decimal(parent: ET.Element, name: str, *, places: int) -> Decimal:
    raw = _single_text(parent, name)
    pattern = re.compile(rf"^(?:0|[1-9][0-9]*)\.[0-9]{{{places}}}$")
    if pattern.fullmatch(raw) is None:
        raise SourceSchemaError(
            f"Treasury auction XML {name} must have {places} decimal places"
        )
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise SourceSchemaError(f"Treasury auction XML {name} is not decimal") from error
    if not value.is_finite():
        raise SourceSchemaError(f"Treasury auction XML {name} must be finite")
    return value


def _basis_points(rate_percent: Decimal, label: str) -> int:
    value = rate_percent * 100
    if value != value.to_integral_value():
        raise SourceSchemaError(f"Treasury auction {label} does not map to whole basis points")
    return int(value)


def _money(value: int) -> str:
    return f"{value:,}"


def _two_places(value: Decimal) -> str:
    return f"{value:.2f}"


def _three_places(value: Decimal) -> str:
    return f"{value:.3f}"


def _six_places(value: Decimal) -> str:
    return f"{value:.6f}"
