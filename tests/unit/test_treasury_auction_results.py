from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import SourceSchemaError, TreasuryAuction91DayArchiveAdapter
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

AUCTION_DATE = date(2020, 3, 16)
XML_URL = "https://www.treasurydirect.gov/xml/R_20200316_2.xml"
PDF_URL = (
    "https://www.treasurydirect.gov/instit/annceresult/press/preanre/2020/"
    "R_20200316_2.pdf"
)


def xml_bytes(
    *,
    root_tag: str = "td:AuctionData",
    include_announcement: bool = True,
    duplicate_announcement: bool = False,
    cusip: str = "912796SV2",
    announcement_date: str = "2020-03-12",
    auction_date: str = "2020-03-16",
    issue_date: str = "2020-03-19",
    maturity_date: str = "2020-06-18",
    week_term: str = "13-WEEK",
    day_term: str = "91-DAY",
    security_type: str = "BILL",
    auction_type: str = "SINGLE PRICE",
    competitive_close: str = "11:30",
    noncompetitive_close: str = "11:00",
    release_time: str = "11:32",
    high_rate: str = "0.290",
    median_rate: str = "0.200",
    low_rate: str = "0.100",
    investment_rate: str = "0.294",
    high_price: str = "99.926694",
    high_allocation: str = "3.74",
    bid_to_cover: str = "2.58",
    competitive_tendered: str = "106585264000",
    competitive_accepted: str = "40409534000",
    noncompetitive_accepted: str = "815208900",
    fima_tendered: str = "775300000",
    fima_accepted: str = "775300000",
    soma_tendered: str = "1438976400",
    soma_accepted: str = "1438976400",
    total_tendered: str = "109614749300",
    total_accepted: str = "43439019300",
    primary_tendered: str = "82580000000",
    primary_accepted: str = "20608050000",
    direct_tendered: str = "2845000000",
    direct_accepted: str = "1302480000",
    indirect_tendered: str = "21160264000",
    indirect_accepted: str = "18499004000",
    treasury_direct_accepted: str = "421751000",
    results_pdf: str = "R_20200316_2.pdf",
    doctype: bool = False,
) -> bytes:
    announcement = f"""
  <AuctionAnnouncement>
    <SecurityTermWeekYear>{week_term}</SecurityTermWeekYear>
    <SecurityTermDayMonth>{day_term}</SecurityTermDayMonth>
    <SecurityType>{security_type}</SecurityType>
    <CUSIP>{cusip}</CUSIP>
    <AnnouncementDate>{announcement_date}</AnnouncementDate>
    <AuctionDate>{auction_date}</AuctionDate>
    <IssueDate>{issue_date}</IssueDate>
    <MaturityDate>{maturity_date}</MaturityDate>
    <TypeOfAuction>{auction_type}</TypeOfAuction>
    <CompetitiveClosingTime>{competitive_close}</CompetitiveClosingTime>
    <NonCompetitiveClosingTime>{noncompetitive_close}</NonCompetitiveClosingTime>
  </AuctionAnnouncement>"""
    announcements = announcement if include_announcement else ""
    if duplicate_announcement:
        announcements += announcement
    declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    if doctype:
        declaration += '<!DOCTYPE x [<!ENTITY payload "bad">]>\n'
    content = f"""{declaration}<{root_tag} xmlns:td="http://www.treasurydirect.gov/">
{announcements}
  <AuctionResults>
    <PrimaryDealerTendered>{primary_tendered}</PrimaryDealerTendered>
    <PrimaryDealerAccepted>{primary_accepted}</PrimaryDealerAccepted>
    <DirectBidderTendered>{direct_tendered}</DirectBidderTendered>
    <DirectBidderAccepted>{direct_accepted}</DirectBidderAccepted>
    <IndirectBidderTendered>{indirect_tendered}</IndirectBidderTendered>
    <IndirectBidderAccepted>{indirect_accepted}</IndirectBidderAccepted>
    <CompetitiveTendered>{competitive_tendered}</CompetitiveTendered>
    <CompetitiveAccepted>{competitive_accepted}</CompetitiveAccepted>
    <NonCompetitiveAccepted>{noncompetitive_accepted}</NonCompetitiveAccepted>
    <SOMATendered>{soma_tendered}</SOMATendered>
    <SOMAAccepted>{soma_accepted}</SOMAAccepted>
    <FIMATendered>{fima_tendered}</FIMATendered>
    <FIMAAccepted>{fima_accepted}</FIMAAccepted>
    <TotalTendered>{total_tendered}</TotalTendered>
    <TotalAccepted>{total_accepted}</TotalAccepted>
    <BidToCoverRatio>{bid_to_cover}</BidToCoverRatio>
    <ReleaseTime>{release_time}</ReleaseTime>
    <HighAllocationPercentage>{high_allocation}</HighAllocationPercentage>
    <LowDiscountRate>{low_rate}</LowDiscountRate>
    <HighDiscountRate>{high_rate}</HighDiscountRate>
    <MedianDiscountRate>{median_rate}</MedianDiscountRate>
    <HighPrice>{high_price}</HighPrice>
    <TreasuryDirectAccepted>{treasury_direct_accepted}</TreasuryDirectAccepted>
    <InvestmentRate>{investment_rate}</InvestmentRate>
    <ResultsPDFName>{results_pdf}</ResultsPDFName>
  </AuctionResults>
</{root_tag}>
"""
    return content.encode()


def pdf_bytes(
    *,
    replacements: dict[str, str] | None = None,
    duplicate_title: bool = False,
    blank: bool = False,
    pages: int = 1,
    width: int = 612,
    height: int = 792,
) -> bytes:
    lines = [
        "1All tenders at lower rates were accepted in full.",
        "2Equivalent coupon-issue yield.",
        "350% of the amount of accepted competitive tenders was tendered at or below that rate.",
        "45% of the amount of accepted competitive tenders was tendered at or below that rate.",
        "5Bid-to-Cover Ratio: $108,175,772,900/$42,000,042,900 = 2.58",
        "6Awards to TreasuryDirect = $421,751,000.",
        "For Immediate Release",
        "CONTACT: Treasury Auctions",
        "March 16, 2020",
        "TREASURY AUCTION RESULTS",
        "Term and Type of Security 91-Day Bill",
        "CUSIP Number 912796SV2",
        (
            "High Rate 1 0.290% Allotted at High 3.74% Price 99.926694 "
            "Investment Rate 2 0.294% Median Rate 3 0.200% Low Rate 4 0.100%"
        ),
        "Issue Date March 19, 2020",
        "Maturity Date June 18, 2020",
        "Competitive $106,585,264,000 $40,409,534,000",
        "Noncompetitive $815,208,900 $815,208,900",
        "FIMA (Noncompetitive) $775,300,000 $775,300,000",
        "Subtotal 5 $108,175,772,900 $42,000,042,900",
        "SOMA $1,438,976,400 $1,438,976,400",
        "Total $109,614,749,300 $43,439,019,300",
        "Primary Dealer 7 $82,580,000,000 $20,608,050,000",
        "Direct Bidder 8 $2,845,000,000 $1,302,480,000",
        "Indirect Bidder 9 $21,160,264,000 $18,499,004,000",
    ]
    if replacements:
        lines = [
            _replace_all(line, replacements)
            for line in lines
        ]
    if duplicate_title:
        lines.append("TREASURY AUCTION RESULTS")
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    if not blank:
        _write_page_text(writer, lines)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _replace_all(value: str, replacements: dict[str, str]) -> str:
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def _write_page_text(writer: PdfWriter, lines: list[str]) -> None:
    page = writer.pages[0]
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
    )
    commands = ["BT", "/F1 7 Tf", "30 760 Td", "10 TL"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.extend((f"({escaped}) Tj", "T*"))
    commands.append("ET")
    stream = StreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)


def response(
    request: httpx.Request,
    content: bytes,
    *,
    content_type: str,
) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"Content-Type": content_type},
        request=request,
    )


def adapter(
    *,
    xml_content: bytes | None = None,
    pdf_content: bytes | None = None,
    xml_content_type: str = "text/xml",
    pdf_content_type: str = "application/pdf",
) -> TreasuryAuction91DayArchiveAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/xml/"):
            return response(
                request,
                xml_content if xml_content is not None else xml_bytes(),
                content_type=xml_content_type,
            )
        return response(
            request,
            pdf_content if pdf_content is not None else pdf_bytes(),
            content_type=pdf_content_type,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return TreasuryAuction91DayArchiveAdapter(safe, auction_date=AUCTION_DATE)


def test_archived_auction_pair_is_exact_reconciled_and_knowledge_safe() -> None:
    xml_content = xml_bytes()
    pdf_content = pdf_bytes()
    batch = adapter(xml_content=xml_content, pdf_content=pdf_content).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("20200316:912796SV2:high_discount_rate")
    assert record.entity_id == "us_treasury_auction:91_day_bill"
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.vintage_as_of == datetime(2020, 3, 16, 15, 32, tzinfo=UTC)
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.sha256 == hashlib.sha256(xml_content).hexdigest()
    assert record.interval.valid_from == datetime(2020, 3, 16, tzinfo=UTC)
    assert record.interval.published_at == datetime(2020, 3, 16, 15, 32, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 3, 17, 4, tzinfo=UTC)
    assert record.interval.availability_confidence == 1.0
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_basis_points"] == 29
    assert record.payload["reported_high_rate_percent"] == "0.290"
    assert record.payload["reported_price_per_100"] == "99.926694"
    assert record.payload["bid_to_cover_ratio"] == "2.58"
    assert record.payload["subtotal_tendered_dollars"] == 108_175_772_900
    assert record.payload["subtotal_accepted_dollars"] == 42_000_042_900
    assert record.payload["official_release_at"] == "2020-03-16T15:32:00+00:00"
    assert record.payload["xml_pdf_crosscheck_verified"] is True
    assert record.payload["auction_arithmetic_verified"] is True
    assert record.payload["price_formula_verified"] is True
    assert record.payload["release_pdf_sha256"] == hashlib.sha256(pdf_content).hexdigest()
    assert [receipt.record_count for receipt in batch.receipts] == [1, 0]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert len(batch.artifacts) == 2
    assert {artifact.content for artifact in batch.artifacts} == {xml_content, pdf_content}

    with TimeVault() as vault:
        vault.append(batch.records)
        before = datetime(2020, 3, 17, 3, 59, 59, tzinfo=UTC)
        assert vault.records_as_of(before) == []
        assert vault.records_as_of(datetime(2020, 3, 17, 4, tzinfo=UTC)) == [record]


def test_verified_auction_calendar_maps_all_three_results_and_rejects_other_dates() -> None:
    client = cast(SafeHttpClient, object())
    march09 = TreasuryAuction91DayArchiveAdapter(client, auction_date=date(2020, 3, 9))
    march23 = TreasuryAuction91DayArchiveAdapter(client, auction_date=date(2020, 3, 23))
    assert march09.identity.cusip == "912796TZ2"
    assert march09.identity.release_time == "11:32"
    assert march23.identity.cusip == "912796UA5"
    assert march23.identity.release_time == "11:31"
    with pytest.raises(ValueError, match="verified 91-day bill calendar"):
        TreasuryAuction91DayArchiveAdapter(client, auction_date=date(2020, 3, 30))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-xml", "declaration is invalid"),
        (xml_bytes(doctype=True), "declaration is invalid"),
        (b'<?xml version="1.0"?><broken>', "could not be parsed"),
        (xml_bytes(root_tag="td:OtherData"), "root identity"),
        (xml_bytes(include_announcement=False), "one AuctionAnnouncement"),
        (xml_bytes(duplicate_announcement=True), "one AuctionAnnouncement"),
        (xml_bytes(cusip="bad"), "identity or calendar"),
        (xml_bytes(auction_date="2020-03-15"), "identity or calendar"),
        (xml_bytes(release_time="25:00"), "valid clock time"),
        (xml_bytes(high_rate="0.29"), "three decimal places"),
        (
            xml_bytes(high_rate="0.291", high_price="99.926442"),
            "whole basis points",
        ),
        (xml_bytes(low_rate="0.300"), "rate ordering"),
        (xml_bytes(primary_tendered="82580000001"), "bidder-category amounts"),
        (xml_bytes(fima_accepted="775299999"), "FIMA amounts"),
        (xml_bytes(soma_accepted="1438976399"), "SOMA amounts"),
        (xml_bytes(total_tendered="109614749301"), "totals do not reconcile"),
        (xml_bytes(bid_to_cover="2.57"), "bid-to-cover ratio"),
        (xml_bytes(high_price="99.926695"), "bill price"),
    ],
)
def test_xml_identity_precision_arithmetic_and_time_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(xml_content=content).fetch()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (pdf_bytes(pages=2), "one page"),
        (pdf_bytes(width=600), "US Letter"),
        (pdf_bytes(blank=True), "no extractable text"),
        (
            pdf_bytes(replacements={"TREASURY AUCTION RESULTS": "OTHER RESULT"}),
            "identity or values",
        ),
        (
            pdf_bytes(replacements={"912796SV2": "912796UA5"}),
            "identity or values",
        ),
        (
            pdf_bytes(replacements={"High Rate 1 0.290%": "High Rate 1 0.291%"}),
            "identity or values",
        ),
        (
            pdf_bytes(replacements={"$42,000,042,900": "$42,000,042,901"}),
            "identity or values",
        ),
        (pdf_bytes(duplicate_title=True), "identity or values"),
    ],
)
def test_pdf_layout_identity_and_cross_form_values_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(pdf_content=content).fetch()


def test_response_content_types_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="XML content type"):
        adapter(xml_content_type="text/html").fetch()
    with pytest.raises(SourceSchemaError, match="PDF content type"):
        adapter(pdf_content_type="text/html").fetch()


class PairClient:
    def __init__(
        self,
        *,
        xml_url: str,
        pdf_url: str,
        retrieved_at: datetime,
    ) -> None:
        self.urls = (xml_url, pdf_url)
        self.retrieved_at = retrieved_at
        self.calls = 0

    def get(self, *_args: Any, **_kwargs: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
        position = self.calls
        self.calls += 1
        if position == 0:
            content = xml_bytes()
            content_type = "text/xml"
        else:
            content = pdf_bytes()
            content_type = "application/pdf"
        snapshot = HttpResponseSnapshot(
            status_code=200,
            headers={"Content-Type": content_type},
            request_url=self.urls[position],
            content=content,
        )
        return snapshot, content, self.retrieved_at


def test_response_urls_and_future_result_cannot_be_backdated() -> None:
    wrong_xml = TreasuryAuction91DayArchiveAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                xml_url=XML_URL.replace("20200316", "20200309"),
                pdf_url=PDF_URL,
                retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        auction_date=AUCTION_DATE,
    )
    with pytest.raises(SourceSchemaError, match="XML response URL"):
        wrong_xml.fetch()

    wrong_pdf = TreasuryAuction91DayArchiveAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                xml_url=XML_URL,
                pdf_url=PDF_URL + "?download=1",
                retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        auction_date=AUCTION_DATE,
    )
    with pytest.raises(SourceSchemaError, match="PDF response URL"):
        wrong_pdf.fetch()

    early = TreasuryAuction91DayArchiveAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                xml_url=XML_URL,
                pdf_url=PDF_URL,
                retrieved_at=datetime(2020, 3, 17, 3, 59, 59, tzinfo=UTC),
            ),
        ),
        auction_date=AUCTION_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not conservatively knowable"):
        early.fetch()
