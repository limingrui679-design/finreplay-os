from __future__ import annotations

from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import SourceSchemaError, TreasuryDTSPublishedReportAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

REPORT_DATE = date(2023, 6, 1)


def pdf_bytes(
    *,
    page_date: str = "Thursday, June 1, 2023",
    title: str = "DAILY TREASURY STATEMENT",
    table: str = "TABLE I - Operating Cash Balance",
    opening: str = "48,512",
    deposits: str = "207,439",
    withdrawals: str = "233,059",
    closing: str = "22,892",
    duplicate_closing: bool = False,
    pages: int = 4,
) -> bytes:
    lines = [
        title,
        "Cash and debt operations of the United States Treasury",
        page_date,
        "(Detail, rounded in millions, may not add to totals)",
        table,
        f"Treasury General Account (TGA) Opening Balance $ {opening} $ {opening} $ 635,994",
        f"Total TGA Deposits (Table II) {deposits} {deposits} 16,268,683",
        f"Total TGA Withdrawals (Table II) (-) {withdrawals} {withdrawals} 16,881,785",
        f"Treasury General Account (TGA) Closing Balance $ {closing} $ {closing} $ {closing}",
    ]
    if duplicate_closing:
        lines.append(f"Treasury General Account (TGA) Closing Balance $ {closing}")
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
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
    commands = ["BT", "/F1 9 Tf", "72 750 Td", "12 TL"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.extend((f"({escaped}) Tj", "T*"))
    commands.append("ET")
    stream = StreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    for _ in range(pages - 1):
        writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def response(
    request: httpx.Request,
    content: bytes,
    content_type: str = "application/pdf",
) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"Content-Type": content_type},
        request=request,
    )


def adapter(handler: Any) -> TreasuryDTSPublishedReportAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return TreasuryDTSPublishedReportAdapter(safe, report_date=REPORT_DATE)


def test_published_dts_report_is_versioned_reconciled_and_knowledge_safe() -> None:
    content = pdf_bytes()
    batch = adapter(lambda request: response(request, content)).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("20230601:tga_closing_balance")
    assert record.entity_id == "us_treasury:treasury_general_account"
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.vintage_as_of == datetime(2023, 6, 1, tzinfo=UTC)
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.interval.valid_from == datetime(2023, 6, 1, tzinfo=UTC)
    assert record.interval.available_at == datetime(2023, 6, 2, 20, tzinfo=UTC)
    assert record.interval.published_at == record.interval.available_at
    assert record.interval.availability_confidence == 1.0
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_millions"] == 22_892
    assert record.payload["opening_balance_millions"] == 48_512
    assert record.payload["deposits_millions"] == 207_439
    assert record.payload["withdrawals_millions"] == 233_059
    assert record.payload["arithmetic_verified"] is True
    assert batch.receipts[0].historical_replay_eligible is True
    assert len(batch.receipts[0].warnings) == 3
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2023, 6, 2, 19, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2023, 6, 2, 20, tzinfo=UTC)) == [record]


def test_verified_report_calendar_handles_friday_to_monday_and_rejects_other_dates() -> None:
    client = cast(SafeHttpClient, object())
    friday = TreasuryDTSPublishedReportAdapter(client, report_date=date(2023, 6, 2))
    assert friday.publication_date == date(2023, 6, 5)
    with pytest.raises(ValueError, match="verified DTS publication calendar"):
        TreasuryDTSPublishedReportAdapter(client, report_date=date(2023, 5, 30))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"page_date": "Wednesday, May 31, 2023"}, "identity or Table I"),
        ({"title": "TREASURY CASH REPORT"}, "identity or Table I"),
        ({"table": "TABLE II - Cash Flows"}, "identity or Table I"),
        ({"opening": "48.5"}, "one valid.*Opening Balance"),
        ({"duplicate_closing": True}, "one valid.*Closing Balance"),
        ({"withdrawals": "233,058"}, "do not reconcile"),
        ({"opening": "1", "deposits": "1", "withdrawals": "2", "closing": "0"}, "positive"),
        ({"pages": 3}, "exactly four pages"),
    ],
)
def test_report_identity_money_arithmetic_and_page_corruption_fail_closed(
    kwargs: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(lambda request: response(request, pdf_bytes(**kwargs))).fetch()


def test_non_pdf_blank_pdf_and_content_type_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="not a PDF"):
        adapter(lambda request: response(request, b"not-pdf")).fetch()

    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    with pytest.raises(SourceSchemaError, match="no extractable text"):
        adapter(lambda request: response(request, output.getvalue())).fetch()

    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(lambda request: response(request, pdf_bytes(), "text/html")).fetch()


def test_response_url_and_early_retrieval_fail_closed() -> None:
    class WrongURLClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "application/pdf"},
                    "request_url": (
                        "https://fiscaldata.treasury.gov/static-data/published-reports/dts/"
                        "DailyTreasuryStatement_20230531.pdf"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, pdf_bytes(), datetime(2026, 1, 1, tzinfo=UTC)

    wrong = TreasuryDTSPublishedReportAdapter(
        cast(SafeHttpClient, WrongURLClient()),
        report_date=REPORT_DATE,
    )
    with pytest.raises(SourceSchemaError, match="requested report"):
        wrong.fetch()

    class EarlyClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "application/pdf"},
                    "request_url": (
                        "https://fiscaldata.treasury.gov/static-data/published-reports/dts/"
                        "DailyTreasuryStatement_20230601.pdf"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, pdf_bytes(), datetime(2023, 6, 2, 19, 59, tzinfo=UTC)

    early = TreasuryDTSPublishedReportAdapter(
        cast(SafeHttpClient, EarlyClient()),
        report_date=REPORT_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet conservatively knowable"):
        early.fetch()
