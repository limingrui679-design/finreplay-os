from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import cast

import httpx
import pytest
import xlrd
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import CensusMARTSArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 3, 17)
PDF_URL = "https://www2.census.gov/retail/releases/historical/marts/adv2002.pdf"
XLS_URL = "https://www2.census.gov/retail/releases/historical/marts/rs2002.xls"


def pdf_bytes(
    *,
    replacements: dict[str, str] | None = None,
    pages: int = 6,
    width: int = 612,
    height: int = 792,
    blank_page: int | None = None,
) -> bytes:
    lines = [
        "FOR RELEASE AT 8:30 AM EDT, TUESDAY, MARCH 17, 2020",
        "ADVANCE MONTHLY SALES FOR RETAIL AND FOOD SERVICES, FEBRUARY 2020",
        "Release Number: CB20-36",
        (
            "were $528.1 billion, a decrease of 0.5 percent "
            "(±0.4 percent) from the previous month"
        ),
        "4.3 percent (±0.7 percent) above February 2019",
        (
            "was revised from up 0.3 percent (±0.4 percent) to up 0.6 percent "
            "(±0.3 percent)"
        ),
        "Table 2. Estimated Change in Monthly Sales for Retail and Food Services",
        (
            "Table 3. Estimated Measures of Sampling Variability and Revision to Advance "
            "Estimates"
        ),
        "scheduled for release on April 27, 2020 at 10:00 a.m. EDT",
    ]
    if replacements:
        lines = [_replace_all(line, replacements) for line in lines]
    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=width, height=height)
        if index != blank_page:
            _write_page_text(
                writer,
                index,
                lines if index == 0 else [f"MARTS table page {index + 1}"],
            )
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def xls_bytes(*, monthly_change: float = -0.5, sheet_names: tuple[str, ...] | None = None) -> bytes:
    book = _FakeBook()
    names = sheet_names or ("Table 1.", "Table 2.", "Table 3.")
    for name in names:
        book.sheets.append(_FakeSheet(name))
    table1, table2, table3 = book.sheets
    table1.cells.update(
        {
            (0, 0): (
                "Table 1.  Estimated Monthly Sales for Retail and Food Services, by Kind of "
                "Business"
            ),
            (10, 1): "Retail & food services, ",
            (11, 9): 528113.0,
            (11, 10): 530930.0,
        }
    )
    table2.cells.update(
        {
            (0, 0): (
                "Table 2.  Estimated Change in Monthly Sales for Retail and Food Services, by "
                "Kind of Business"
            ),
            (7, 2): "Feb. 2020 Advance",
            (10, 2): "Jan. 2020",
            (13, 1): "Retail & food services, ",
            (14, 2): monthly_change,
            (14, 3): 4.3,
            (14, 4): 0.6,
        }
    )
    table3.cells.update(
        {
            (0, 0): (
                "Table 3.   Estimated Measures of Sampling Variability and Revision to "
                "Advance Estimates Feb. 2020"
            ),
            (8, 1): "Retail & food services, ",
            (9, 2): 0.7,
            (9, 3): 0.2,
            (9, 6): 0.1,
            (9, 7): 0.1,
        }
    )
    # xlrd is read-only; encode the deterministic fake book through the adapter's test seam.
    # A valid OLE header keeps the production format guard covered.
    content = bytes.fromhex("d0cf11e0a1b11ae1") + f"TEST-MARTS-XLS:{monthly_change}".encode()
    _FAKE_BOOKS[content] = book
    return content


class _FakeBook:
    def __init__(self) -> None:
        self.sheets: list[_FakeSheet] = []

    def sheet_names(self) -> list[str]:
        return [sheet.name for sheet in self.sheets]

    def sheet_by_name(self, name: str) -> _FakeSheet:
        return next(sheet for sheet in self.sheets if sheet.name == name)

    def release_resources(self) -> None:
        return None


class _FakeSheet:
    def __init__(self, name: str) -> None:
        self.name = name
        self.nrows = {"Table 1.": 87, "Table 2.": 89, "Table 3.": 43}.get(name, 1)
        self.ncols = {"Table 1.": 14, "Table 2.": 11, "Table 3.": 8}.get(name, 1)
        self.cells: dict[tuple[int, int], object] = {}

    def cell_value(self, row: int, column: int) -> object:
        return self.cells.get((row, column), "")


_FAKE_BOOKS: dict[bytes, _FakeBook] = {}


def _open_fake_workbook(*, file_contents: bytes, on_demand: bool) -> _FakeBook:
    assert on_demand is True
    return _FAKE_BOOKS[file_contents]


def _replace_all(value: str, replacements: dict[str, str]) -> str:
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def _write_page_text(writer: PdfWriter, page_index: int, lines: list[str]) -> None:
    page = writer.pages[page_index]
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
    )
    commands = ["BT", "/F1 7 Tf", "30 760 Td", "10 TL"]
    for line in lines:
        escaped = (
            line.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("±", "\\261")
        )
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
    monkeypatch: pytest.MonkeyPatch,
    *,
    pdf_content: bytes | None = None,
    xls_content: bytes | None = None,
    pdf_content_type: str = "application/pdf",
    xls_content_type: str = "application/vnd.ms-excel",
) -> CensusMARTSArchiveAdapter:
    xls_value = xls_content if xls_content is not None else xls_bytes()
    monkeypatch.setattr(xlrd, "open_workbook", _open_fake_workbook)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".pdf"):
            return response(
                request,
                pdf_content if pdf_content is not None else pdf_bytes(),
                content_type=pdf_content_type,
            )
        return response(request, xls_value, content_type=xls_content_type)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return CensusMARTSArchiveAdapter(safe, release_date=RELEASE_DATE)


def test_marts_pair_is_exact_versioned_and_knowledge_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_content = pdf_bytes()
    xls_content = xls_bytes()
    batch = adapter(
        monkeypatch,
        pdf_content=pdf_content,
        xls_content=xls_content,
    ).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("202002:monthly_change")
    assert record.entity_id == "census_marts:retail_and_food_services_total"
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.vintage_as_of == datetime(2020, 3, 17, 12, 30, tzinfo=UTC)
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.sha256 == hashlib.sha256(pdf_content).hexdigest()
    assert record.interval.valid_from == datetime(2020, 2, 1, tzinfo=UTC)
    assert record.interval.published_at == datetime(2020, 3, 17, 12, 30, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 3, 17, 12, 30, tzinfo=UTC)
    assert record.interval.availability_confidence == 1.0
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_basis_points"] == -50
    assert record.payload["reported_monthly_change_percent"] == "-0.5"
    assert record.payload["reported_monthly_margin_90_percent"] == "0.4"
    assert record.payload["reported_sales_billion_dollars"] == "528.1"
    assert record.payload["xls_adjusted_sales_million_dollars"] == 528113
    assert record.payload["year_over_year_change_percent"] == "4.3"
    assert record.payload["prior_month_change_in_current_release_basis_points"] == 60
    assert record.payload["prior_month_change_in_previous_release_basis_points"] == 30
    assert record.payload["prior_month_revision_delta_basis_points"] == 30
    assert record.payload["official_release_at"] == "2020-03-17T12:30:00+00:00"
    assert record.payload["pdf_xls_crosscheck_verified"] is True
    assert record.payload["release_pdf_sha256"] == hashlib.sha256(pdf_content).hexdigest()
    assert record.payload["release_xls_sha256"] == hashlib.sha256(xls_content).hexdigest()
    assert [receipt.record_count for receipt in batch.receipts] == [1, 0]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert len(batch.artifacts) == 2
    assert {artifact.content for artifact in batch.artifacts} == {pdf_content, xls_content}

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 3, 17, 12, 29, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 3, 17, 12, 30, tzinfo=UTC)) == [record]


def test_verified_calendar_preserves_release_snapshots_and_rejects_other_dates() -> None:
    client = cast(SafeHttpClient, object())
    january = CensusMARTSArchiveAdapter(client, release_date=date(2020, 2, 14))
    march = CensusMARTSArchiveAdapter(client, release_date=date(2020, 4, 15))
    assert january.spec.monthly_change_percent == "0.3"
    assert january.spec.timezone_abbreviation == "EST"
    assert january.spec.prior_month_change_percent == "0.2"
    assert march.spec.monthly_change_percent == "-8.7"
    assert march.spec.prior_month_change_percent == "-0.4"
    assert march.spec.prior_month_previous_release_percent == "-0.5"
    with pytest.raises(ValueError, match="verified Census MARTS calendar"):
        CensusMARTSArchiveAdapter(client, release_date=date(2020, 5, 15))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"pdf_content_type": "text/html"}, "PDF content type"),
        ({"xls_content_type": "text/csv"}, "XLS content type"),
        ({"pdf_content": b"not-pdf"}, "not a PDF"),
        ({"pdf_content": pdf_bytes(pages=5)}, "page count"),
        ({"pdf_content": pdf_bytes(blank_page=4)}, "blank text layer"),
        ({"pdf_content": pdf_bytes(width=613)}, "page dimensions"),
        (
            {"pdf_content": pdf_bytes(replacements={"528.1": "528.2"})},
            "headline, revision, or table",
        ),
        ({"xls_content": b"not-xls"}, "legacy OLE XLS"),
        ({"xls_content": xls_bytes(monthly_change=-0.4)}, "do not cross-check"),
    ],
)
def test_marts_rejects_schema_and_crosscheck_failures(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(monkeypatch, **kwargs).fetch()  # type: ignore[arg-type]


def test_marts_rejects_wrong_response_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = adapter(monkeypatch)
    with pytest.raises(SourceSchemaError, match="PDF response URL"):
        instance._validate_response_url(
            "https://www2.census.gov/retail/releases/historical/marts/adv2003.pdf",
            kind="pdf",
        )
    with pytest.raises(SourceSchemaError, match="XLS response URL"):
        instance._validate_response_url(
            "https://www2.census.gov/retail/releases/historical/marts/rs2002.xls?download=1",
            kind="xls",
        )


def test_urls_are_fixed_to_the_verified_release() -> None:
    instance = CensusMARTSArchiveAdapter(cast(SafeHttpClient, object()), release_date=RELEASE_DATE)
    assert instance.pdf_endpoint == PDF_URL
    assert instance.xls_endpoint == XLS_URL
