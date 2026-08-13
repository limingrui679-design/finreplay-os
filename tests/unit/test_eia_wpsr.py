from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import EIAWPSRCommercialCrudeStocksAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 4, 15)
CSV_LAST_MODIFIED = "Wed, 15 Apr 2020 22:02:44 GMT"
PDF_LAST_MODIFIED = "Wed, 15 Apr 2020 22:45:00 GMT"


def csv_bytes(
    *,
    header_current: str = "4/10/20",
    header_prior: str = "4/3/20",
    current: str = "503.618",
    prior: str = "484.370",
    difference: str = "19.248",
    commercial_rows: int = 1,
    include_spr: bool = True,
    short_commercial_row: bool = False,
) -> bytes:
    header = (
        f'"STUB_1","{header_current}","{header_prior}","Difference",'
        '"4/12/19","Percent Change","4/13/18","Percent Change"'
    )
    rows = [
        '"Crude Oil","1,138.590","1,119.340","19.248","1,104.280",'
        '"3.100","1,093.020","4.200"',
    ]
    commercial = (
        f'"Commercial (Excluding SPR)","{current}","{prior}","{difference}",'
        '"455.154","10.600","427.567","17.800"'
    )
    if short_commercial_row:
        commercial = f'"Commercial (Excluding SPR)","{current}"'
    rows.extend(commercial for _ in range(commercial_rows))
    if include_spr:
        rows.append('"SPR","634.967","634.967","0.000","649.126","-2.200","665.456","-4.600"')
    rows.append(
        '"Total Stocks (Excluding SPR)","1,341.617","1,314.378","27.239",'
        '"1,229.630","9.100","1,178.367","13.900"'
    )
    return ("\r\n".join((header, *rows)) + "\r\n").encode()


def pdf_bytes(
    *,
    release_title: str = "EIA DATA ARE AVAILABLE IN ELECTRONIC FORM",
    schedule: str = (
        "The tables in the Weekly Petroleum Status Report (WPSR) are posted to the web site "
        "after 10:30 a.m. Eastern Standard Time (EST) on Wednesdays in CSV and XLS formats."
    ),
    holiday: str = "For some weeks that include holidays, posting is delayed by one day.",
    release_date: str = "Release Date: April 15, 2020",
    table_title: str = (
        "Table 4. Stocks of Crude Oil by PAD District, and Stocks of Petroleum Products, "
        "U.S. Totals"
    ),
    current_week: str = "4/10/20",
    row: str = "Commercial (Excluding SPR)3 ....... 503.6 484.4 19.2 455.2 10.6 427.6 17.8",
    duplicate_row: bool = False,
    pages: int = 62,
    blank_release: bool = False,
    blank_table: bool = False,
) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if pages > 1 and not blank_release:
        _write_page_text(
            writer,
            1,
            [release_title, schedule, holiday, release_date],
        )
    if pages > 8 and not blank_table:
        lines = [
            table_title,
            "(Million Barrels)",
            "Product / Region Current Week Last Week Year Ago 2 Years Ago",
            current_week,
            row,
        ]
        if duplicate_row:
            lines.append(row)
        _write_page_text(writer, 8, lines)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _write_page_text(writer: PdfWriter, page_number: int, lines: list[str]) -> None:
    page = writer.pages[page_number]
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
    commands = ["BT", "/F1 8 Tf", "36 750 Td", "11 TL"]
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
    last_modified: str | None,
) -> httpx.Response:
    headers = {"Content-Type": content_type}
    if last_modified is not None:
        headers["Last-Modified"] = last_modified
    return httpx.Response(200, content=content, headers=headers, request=request)


def adapter(
    *,
    csv_content: bytes | None = None,
    pdf_content: bytes | None = None,
    csv_content_type: str = "application/octet-stream",
    pdf_content_type: str = "application/pdf",
    csv_last_modified: str | None = CSV_LAST_MODIFIED,
    pdf_last_modified: str | None = PDF_LAST_MODIFIED,
) -> EIAWPSRCommercialCrudeStocksAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/csv/table4.csv"):
            return response(
                request,
                csv_content if csv_content is not None else csv_bytes(),
                content_type=csv_content_type,
                last_modified=csv_last_modified,
            )
        return response(
            request,
            pdf_content if pdf_content is not None else pdf_bytes(),
            content_type=pdf_content_type,
            last_modified=pdf_last_modified,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return EIAWPSRCommercialCrudeStocksAdapter(safe, release_date=RELEASE_DATE)


def test_archived_wpsr_pair_is_versioned_exact_and_knowledge_safe() -> None:
    csv_content = csv_bytes()
    pdf_content = pdf_bytes()
    batch = adapter(csv_content=csv_content, pdf_content=pdf_content).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("20200415:commercial_crude_excluding_spr")
    assert record.entity_id == "eia_series:weekly_us_commercial_crude_stocks_excluding_spr"
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.vintage_as_of == datetime(2020, 4, 15, 22, 45, tzinfo=UTC)
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.sha256 == hashlib.sha256(csv_content).hexdigest()
    assert record.interval.valid_from == datetime(2020, 4, 10, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 4, 16, 4, tzinfo=UTC)
    assert record.interval.published_at == record.interval.available_at
    assert record.interval.availability_confidence == 1.0
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_thousand_barrels"] == 503_618
    assert record.payload["prior_value_thousand_barrels"] == 484_370
    assert record.payload["reported_difference_thousand_barrels"] == 19_248
    assert record.payload["reported_value_million_barrels"] == "503.618"
    assert record.payload["release_pdf_sha256"] == hashlib.sha256(pdf_content).hexdigest()
    assert record.payload["arithmetic_verified"] is True
    assert [receipt.record_count for receipt in batch.receipts] == [1, 0]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert len(batch.artifacts) == 2
    assert {artifact.content for artifact in batch.artifacts} == {csv_content, pdf_content}

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 4, 16, 3, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 4, 16, 4, tzinfo=UTC)) == [record]


def test_verified_release_calendar_maps_all_three_weeks_and_rejects_other_dates() -> None:
    client = cast(SafeHttpClient, object())
    april08 = EIAWPSRCommercialCrudeStocksAdapter(client, release_date=date(2020, 4, 8))
    april22 = EIAWPSRCommercialCrudeStocksAdapter(client, release_date=date(2020, 4, 22))
    assert (april08.week_ending, april08.prior_week_ending) == (
        date(2020, 4, 3),
        date(2020, 3, 27),
    )
    assert (april22.week_ending, april22.prior_week_ending) == (
        date(2020, 4, 17),
        date(2020, 4, 10),
    )
    with pytest.raises(ValueError, match="verified EIA WPSR calendar"):
        EIAWPSRCommercialCrudeStocksAdapter(client, release_date=date(2020, 4, 1))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xff\xfe", "not valid UTF-8"),
        (b'"STUB_1"\x00', "contains a NUL"),
        (b"", "is empty"),
        (b'"unclosed', "could not be parsed"),
        (csv_bytes(header_current="4/9/20"), "header or comparison dates"),
        (csv_bytes(include_spr=False), "required stock rows"),
        (csv_bytes(commercial_rows=2), "one eight-column"),
        (csv_bytes(short_commercial_row=True), "one eight-column"),
        (csv_bytes(current="503.61"), "three decimal places"),
        (csv_bytes(current="-1.000", prior="-2.000", difference="1.000"), "outside"),
        (csv_bytes(difference="19.247"), "do not reconcile"),
    ],
)
def test_csv_identity_precision_range_and_arithmetic_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(csv_content=content).fetch()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (pdf_bytes(pages=61), "exactly 62 pages"),
        (pdf_bytes(blank_release=True), "release page has no extractable text"),
        (pdf_bytes(blank_table=True), "Table 4 page has no extractable text"),
        (pdf_bytes(release_title="OTHER DATA"), "release identity or schedule"),
        (pdf_bytes(schedule="Tables are released later."), "release identity or schedule"),
        (pdf_bytes(release_date="Release Date: April 8, 2020"), "release identity or schedule"),
        (pdf_bytes(table_title="Table 3. Refinery inputs"), "Table 4 identity"),
        (pdf_bytes(current_week="4/9/20"), "Table 4 identity"),
        (pdf_bytes(row="Commercial (Excluding SPR)3 missing"), "one valid"),
        (pdf_bytes(duplicate_row=True), "one valid"),
        (
            pdf_bytes(
                row=(
                    "Commercial (Excluding SPR)3 ....... "
                    "503.5 484.4 19.2 455.2 10.6 427.6 17.8"
                )
            ),
            "do not match archived CSV",
        ),
    ],
)
def test_pdf_release_table_identity_and_rounded_values_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(pdf_content=content).fetch()


def test_response_content_types_and_last_modified_headers_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="Table 4 content type"):
        adapter(csv_content_type="text/html").fetch()
    with pytest.raises(SourceSchemaError, match="report content type"):
        adapter(pdf_content_type="text/html").fetch()
    with pytest.raises(SourceSchemaError, match="CSV response lacks"):
        adapter(csv_last_modified=None).fetch()
    with pytest.raises(SourceSchemaError, match="PDF response lacks"):
        adapter(pdf_last_modified=None).fetch()
    with pytest.raises(SourceSchemaError, match="CSV Last-Modified is invalid"):
        adapter(csv_last_modified="not-a-date").fetch()
    with pytest.raises(SourceSchemaError, match="lacks a timezone"):
        adapter(csv_last_modified="Wed, 15 Apr 2020 22:02:44").fetch()
    with pytest.raises(SourceSchemaError, match="outside the verified release date"):
        adapter(csv_last_modified="Tue, 14 Apr 2020 22:02:44 GMT").fetch()
    with pytest.raises(SourceSchemaError, match="outside the verified release date"):
        adapter(pdf_last_modified="Thu, 16 Apr 2020 05:00:00 GMT").fetch()


class PairClient:
    def __init__(
        self,
        *,
        csv_url: str,
        pdf_url: str,
        retrieved_at: datetime,
    ) -> None:
        self.urls = (csv_url, pdf_url)
        self.retrieved_at = retrieved_at
        self.calls = 0

    def get(self, *_args: Any, **_kwargs: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
        position = self.calls
        self.calls += 1
        if position == 0:
            content = csv_bytes()
            content_type = "application/octet-stream"
            last_modified = CSV_LAST_MODIFIED
        else:
            content = pdf_bytes()
            content_type = "application/pdf"
            last_modified = PDF_LAST_MODIFIED
        snapshot = HttpResponseSnapshot(
            status_code=200,
            headers={"Content-Type": content_type, "Last-Modified": last_modified},
            request_url=self.urls[position],
            content=content,
        )
        return snapshot, content, self.retrieved_at


def test_response_urls_and_future_release_cannot_be_backdated() -> None:
    correct_csv = (
        "https://www.eia.gov/petroleum/supply/weekly/archive/2020/"
        "2020_04_15/csv/table4.csv"
    )
    correct_pdf = (
        "https://www.eia.gov/petroleum/supply/weekly/archive/2020/"
        "2020_04_15/pdf/wpsrall.pdf"
    )
    wrong_csv = correct_csv.replace("2020_04_15", "2020_04_08")
    wrong = EIAWPSRCommercialCrudeStocksAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                csv_url=wrong_csv,
                pdf_url=correct_pdf,
                retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="CSV response URL"):
        wrong.fetch()

    wrong_pdf = EIAWPSRCommercialCrudeStocksAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                csv_url=correct_csv,
                pdf_url=correct_pdf + "?download=1",
                retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="PDF response URL"):
        wrong_pdf.fetch()

    early = EIAWPSRCommercialCrudeStocksAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                csv_url=correct_csv,
                pdf_url=correct_pdf,
                retrieved_at=datetime(2020, 4, 16, 3, 59, tzinfo=UTC),
            ),
        ),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet conservatively knowable"):
        early.fetch()
