from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import BLSExportPriceArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 3, 13)
TECHNICAL_MARKER = (
    "Import and Export Goods and Services Price Indexes - All indexes use a modified "
    "Laspeyres formula and are not seasonally adjusted."
)
MEASUREMENT_MARKER = (
    "Export Price Goods Indexes - Items are classified by the Harmonized Schedule B "
    "classification system of the U.S. Bureau of the Census. The prices used are generally "
    'either "free alongside ship" (f.a.s.) factory or "free on board" (f.o.b.) transaction '
    "prices, depending on the practices of the individual industry."
)
REVISION_MARKER = "Data may be revised in each of the 3 months after original publication."
COVID_MARKER = (
    "Coronavirus (COVID-19) Impact on March 2020 Import and Export Price Index Survey Data "
    "The import and export price quotes are requested for transactions occurring as close "
    "to the first day of the month as possible. While not directly related to the COVID-19 "
    "pandemic, response rates for March were approximately 6.5 percentage points lower than "
    "March 2019. No changes in estimation procedures were necessary."
)
RELEASES = {
    date(2020, 2, 14): {
        "reference": "January 2020",
        "reference_month": "2020-01",
        "release_number": "USDL-20-0247",
        "timezone": "EST",
        "weekday": "Friday",
        "headline": (
            "Prices for U.S. exports advanced 0.7 percent in January, after declining "
            "0.2 percent the previous month."
        ),
        "table_row": "All commodities 100.000 125.0 125.9 0.5 0.0 0.1 -0.2 0.7",
        "value": 7,
        "prior_value": -2,
        "previous_value": None,
        "revision_delta": None,
        "available_at": datetime(2020, 2, 14, 13, 30, tzinfo=UTC),
        "covid": None,
    },
    date(2020, 3, 13): {
        "reference": "February 2020",
        "reference_month": "2020-02",
        "release_number": "USDL-20-0405",
        "timezone": "EDT",
        "weekday": "Friday",
        "headline": (
            "Prices for U.S. exports decreased 1.1 percent in February, after advancing "
            "0.6 percent the previous month."
        ),
        "table_row": "All commodities 100.000 125.8 124.4 -1.3 0.1 -0.2 0.6 -1.1",
        "value": -11,
        "prior_value": 6,
        "previous_value": 7,
        "revision_delta": -1,
        "available_at": datetime(2020, 3, 13, 12, 30, tzinfo=UTC),
        "covid": None,
    },
    date(2020, 4, 14): {
        "reference": "March 2020",
        "reference_month": "2020-03",
        "release_number": "USDL-20-0610",
        "timezone": "EDT",
        "weekday": "Tuesday",
        "headline": (
            "U.S. export prices decreased 1.6 percent in March, after falling 1.1 percent "
            "in February."
        ),
        "table_row": "All commodities 100.000 124.4 122.4 -3.6 -0.2 0.6 -1.1 -1.6",
        "value": -16,
        "prior_value": -11,
        "previous_value": -11,
        "revision_delta": 0,
        "available_at": datetime(2020, 4, 14, 12, 30, tzinfo=UTC),
        "covid": COVID_MARKER,
    },
}


def release_lines(release_date: date) -> list[str]:
    spec = RELEASES[release_date]
    return [
        "Transmission of material in this release is embargoed until",
        (
            f"8:30 a.m. ({spec['timezone']}) {spec['weekday']}, "
            f"{release_date:%B} {release_date.day}, {release_date:%Y}"
        ),
        str(spec["release_number"]),
        f"U.S. IMPORT AND EXPORT PRICE INDEXES - {str(spec['reference']).upper()}",
        str(spec["headline"]),
    ]


def html_bytes(
    *,
    release_date: date = RELEASE_DATE,
    replacements: dict[str, str] | None = None,
) -> bytes:
    spec = RELEASES[release_date]
    lines = [
        *release_lines(release_date),
        TECHNICAL_MARKER,
        MEASUREMENT_MARKER,
        *([REVISION_MARKER] * 7),
        str(spec["table_row"]),
        *((str(spec["covid"]),) if spec["covid"] else ()),
    ]
    if replacements:
        lines = [_replace_all(line, replacements) for line in lines]
    body = "".join(f"<p>{line}</p>" for line in lines)
    return f"<!doctype html><html><body>{body}</body></html>".encode()


def pdf_bytes(
    *,
    release_date: date = RELEASE_DATE,
    replacements: dict[str, str] | None = None,
    pages: int = 18,
    width: int = 612,
    height: int = 792,
    blank_page: int | None = None,
) -> bytes:
    spec = RELEASES[release_date]
    page_lines = [[f"official MXP content page {index + 1}"] for index in range(pages)]
    page_lines[0] = release_lines(release_date)
    if spec["covid"]:
        page_lines[0].append(str(spec["covid"]))
    if pages > 5:
        page_lines[5] = [
            "Table 2. U.S. export price indexes and percent changes",
            str(spec["table_row"]),
        ]
    if pages > 15:
        page_lines[15] = ["Table 10. U.S. international price indexes"]
    if pages > 16:
        page_lines[16] = [
            "TECHNICAL NOTE Import and Export Goods and Services Price Indexes",
            TECHNICAL_MARKER,
            MEASUREMENT_MARKER,
            *([REVISION_MARKER] * 7),
        ]
    if pages > 17:
        page_lines[17] = ["Import Price Indexes by Locality of Origin"]
    if replacements:
        page_lines = [[_replace_all(line, replacements) for line in lines] for lines in page_lines]

    writer = PdfWriter()
    for index, lines in enumerate(page_lines):
        writer.add_blank_page(width=width, height=height)
        if index != blank_page:
            _write_page_text(writer, index, lines)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


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
    commands = ["BT", "/F1 7 Tf", "20 770 Td", "10 TL"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.extend((f"({escaped}) Tj", "T*"))
    commands.append("ET")
    stream = StreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)


def adapter(
    *,
    release_date: date = RELEASE_DATE,
    html: bytes | None = None,
    pdf: bytes | None = None,
    html_content_type: str = "text/html; charset=utf-8",
    pdf_content_type: str = "application/pdf",
) -> BLSExportPriceArchiveAdapter:
    selected_html = html if html is not None else html_bytes(release_date=release_date)
    selected_pdf = pdf if pdf is not None else pdf_bytes(release_date=release_date)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".htm"):
            return httpx.Response(
                200,
                content=selected_html,
                headers={"Content-Type": html_content_type},
                request=request,
            )
        return httpx.Response(
            200,
            content=selected_pdf,
            headers={"Content-Type": pdf_content_type},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return BLSExportPriceArchiveAdapter(safe, release_date=release_date)


def test_export_price_pair_is_exact_versioned_and_knowledge_safe() -> None:
    html = html_bytes()
    pdf = pdf_bytes()
    batch = adapter(html=html, pdf=pdf).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("202002:all_exports_monthly_change")
    assert record.entity_id == "bls_export_price_index:all_exports_united_states"
    assert record.source.sha256 == hashlib.sha256(pdf).hexdigest()
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.license_class is LicenseClass.REDISTRIBUTABLE
    assert record.source.vintage_as_of == datetime(2020, 3, 13, 12, 30, tzinfo=UTC)
    assert record.interval.valid_from == datetime(2020, 2, 1, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 3, 13, 12, 30, tzinfo=UTC)
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_tenths_percent"] == -11
    assert record.payload["value_basis_points"] == -110
    assert record.payload["prior_month_change_tenths_percent"] == 6
    assert record.payload["prior_month_revision_delta_tenths_percent"] == -1
    assert record.payload["table2_current_unadjusted_index"] == "124.4"
    assert record.payload["revision_window_months"] == 3
    assert record.payload["seasonally_adjusted"] is False
    assert record.payload["index_formula"] == "modified Laspeyres"
    assert record.payload["html_pdf_crosscheck_verified"] is True
    assert [receipt.record_count for receipt in batch.receipts] == [0, 1]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert {artifact.content for artifact in batch.artifacts} == {html, pdf}

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 3, 13, 12, 29, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 3, 13, 12, 30, tzinfo=UTC)) == [record]


@pytest.mark.parametrize("release_date", tuple(RELEASES))
def test_export_calendar_preserves_first_reports_revisions_and_timing(
    release_date: date,
) -> None:
    spec = RELEASES[release_date]
    record = adapter(release_date=release_date).fetch().records[0]
    assert record.payload["reference_month"] == spec["reference_month"]
    assert record.payload["value_tenths_percent"] == spec["value"]
    assert record.payload["prior_month_change_tenths_percent"] == spec["prior_value"]
    assert (
        record.payload["prior_month_value_in_previous_release_tenths_percent"]
        == spec["previous_value"]
    )
    assert record.payload["prior_month_revision_delta_tenths_percent"] == spec["revision_delta"]
    assert record.interval.available_at == spec["available_at"]
    assert record.payload["covid_methodology_statement"] == spec["covid"]


def test_export_calendar_and_response_urls_are_closed() -> None:
    client = cast(SafeHttpClient, object())
    with pytest.raises(ValueError, match="verified BLS export-price calendar"):
        BLSExportPriceArchiveAdapter(client, release_date=date(2020, 5, 14))

    item = BLSExportPriceArchiveAdapter(client, release_date=RELEASE_DATE)
    for kind, invalid in (
        ("html", "http://www.bls.gov/news.release/archives/ximpim_03132020.htm"),
        ("html", "https://evil.example/news.release/archives/ximpim_03132020.htm"),
        ("html", "https://www.bls.gov/news.release/archives/ximpim_03132020.pdf"),
        ("pdf", "https://www.bls.gov/news.release/archives/other.pdf"),
        ("pdf", "https://www.bls.gov/news.release/archives/ximpim_03132020.pdf?q=1"),
    ):
        with pytest.raises(SourceSchemaError, match="response URL"):
            item._validate_response_url(invalid, kind=kind)
    with pytest.raises(ValueError, match="html or pdf"):
        item._validate_response_url(item.pdf_endpoint, kind="csv")


@pytest.mark.parametrize(
    ("pdf", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (pdf_bytes(pages=17), "page count"),
        (pdf_bytes(width=611), "geometry"),
        (pdf_bytes(blank_page=3), "blank text layer"),
        (
            pdf_bytes(replacements={"U.S. IMPORT AND EXPORT": "OTHER"}),
            "first-page identity",
        ),
        (pdf_bytes(replacements={"Table 2.": "Other Table."}), "Table 2 page"),
        (pdf_bytes(replacements={"Table 10.": "Other Table."}), "Table 10 page"),
        (pdf_bytes(replacements={"TECHNICAL NOTE": "Other Note"}), "technical-note"),
        (
            pdf_bytes(replacements={"Import Price Indexes by Locality": "Other Indexes"}),
            "final technical page",
        ),
    ],
)
def test_export_pdf_structure_and_identity_fail_closed(pdf: bytes, message: str) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(pdf=pdf).fetch()


@pytest.mark.parametrize(
    ("replacements", "message"),
    [
        ({"USDL-20-0405": "USDL-20-9999"}, "identity or headline"),
        ({"decreased 1.1 percent": "rose 1.1 percent"}, "identity or headline"),
        ({"124.4 -1.3": "124.3 -1.3"}, "Table 2 values"),
        ({REVISION_MARKER: "No revision rule."}, "revision rule"),
        ({TECHNICAL_MARKER: "different formula"}, "identity or headline"),
        ({MEASUREMENT_MARKER: "different price scope"}, "identity or headline"),
    ],
)
def test_export_html_corruption_fails_closed(
    replacements: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(html=html_bytes(replacements=replacements)).fetch()


def test_export_covid_marker_is_required_only_for_march_release() -> None:
    with pytest.raises(SourceSchemaError, match="COVID-19 methodology"):
        adapter(
            release_date=date(2020, 4, 14),
            html=html_bytes(
                release_date=date(2020, 4, 14),
                replacements={COVID_MARKER: "no methodology marker"},
            ),
            pdf=pdf_bytes(release_date=date(2020, 4, 14)),
        ).fetch()
    with pytest.raises(SourceSchemaError, match="COVID-19 methodology"):
        adapter(html=html_bytes() + f"<p>{COVID_MARKER}</p>".encode()).fetch()


def test_export_content_types_and_invalid_html_encoding_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="HTML content type"):
        adapter(html_content_type="application/json").fetch()
    with pytest.raises(SourceSchemaError, match="PDF content type"):
        adapter(pdf_content_type="text/plain").fetch()
    with pytest.raises(SourceSchemaError, match="neither valid UTF-8 nor Windows-1252"):
        adapter(html=b"\x81").fetch()


def test_export_response_url_and_prepublication_retrieval_fail_closed() -> None:
    valid_html = html_bytes()
    valid_pdf = pdf_bytes()

    class WrongURLClient:
        def get(self, url: str, **_kwargs: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
            content = valid_html if url.endswith(".htm") else valid_pdf
            content_type = "text/html" if url.endswith(".htm") else "application/pdf"
            request_url = (
                "https://www.bls.gov/news.release/archives/ximpim_02142020.htm"
                if url.endswith(".htm")
                else url
            )
            snapshot = HttpResponseSnapshot(
                status_code=200,
                headers={"Content-Type": content_type},
                request_url=request_url,
                content=content,
            )
            return snapshot, content, datetime(2026, 1, 1, tzinfo=UTC)

    wrong = BLSExportPriceArchiveAdapter(
        cast(SafeHttpClient, WrongURLClient()), release_date=RELEASE_DATE
    )
    with pytest.raises(SourceSchemaError, match="response URL"):
        wrong.fetch()

    class EarlyClient:
        def get(self, url: str, **_kwargs: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
            content = valid_html if url.endswith(".htm") else valid_pdf
            content_type = "text/html" if url.endswith(".htm") else "application/pdf"
            snapshot = HttpResponseSnapshot(
                status_code=200,
                headers={"Content-Type": content_type},
                request_url=url,
                content=content,
            )
            retrieved_at = (
                datetime(2020, 3, 13, 12, 29, 59, tzinfo=UTC)
                if url.endswith(".htm")
                else datetime(2026, 1, 1, tzinfo=UTC)
            )
            return snapshot, content, retrieved_at

    early = BLSExportPriceArchiveAdapter(
        cast(SafeHttpClient, EarlyClient()), release_date=RELEASE_DATE
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
