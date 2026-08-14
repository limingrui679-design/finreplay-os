from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import BLSPPIArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 4, 9)
TECHNICAL_DEFINITION = (
    "measures the average change over time in prices received (price changes) by producers "
    "for domestically produced goods, services, and construction."
)
REVISION_MARKER = "All indexes are subject to revision 4 months after original publication."
MARCH_COVID_MARKER = (
    "The Producer Price Index (PPI) pricing date was March 10. Response rates for March were "
    "consistent with those of February, and no changes in estimation procedures were necessary."
)
APRIL_COVID_MARKER = (
    "The Producer Price Index (PPI) response rates for April were consistent with those of "
    "March and February, and no changes in estimation procedures were necessary."
)
RELEASES = {
    date(2020, 3, 12): {
        "reference": "February 2020",
        "reference_month": "2020-02",
        "release_number": "USDL 20-0404",
        "weekday": "Thursday",
        "headline": (
            "The Producer Price Index for final demand fell 0.6 percent in February, seasonally "
            "adjusted, the U.S. Bureau of Labor Statistics reported today."
        ),
        "prior": (
            "Final demand prices advanced 0.5 percent in January and 0.2 percent in December."
        ),
        "yoy": (
            "On an unadjusted basis, the final demand index increased 1.3 percent for the 12 "
            "months ended in February."
        ),
        "table_row": "Final demand 100.000 118.8 119.1 118.6 1.3 -0.4 0.2 0.5 -0.6",
        "pages": 32,
        "technical_page": 6,
        "table_page": 13,
        "value": -6,
        "prior_value": 5,
        "previous_value": None,
        "revision_delta": None,
        "covid": None,
    },
    date(2020, 4, 9): {
        "reference": "March 2020",
        "reference_month": "2020-03",
        "release_number": "USDL 20-0567",
        "weekday": "Thursday",
        "headline": (
            "The Producer Price Index for final demand fell 0.2 percent in March, seasonally "
            "adjusted, the U.S. Bureau of Labor Statistics reported today."
        ),
        "prior": (
            "Final demand prices declined 0.6 percent in February and increased 0.5 percent "
            "in January."
        ),
        "yoy": (
            "On an unadjusted basis, the final demand index advanced 0.7 percent for the 12 "
            "months ended in March."
        ),
        "table_row": "Final demand 100.000 118.3 118.6 118.5 0.7 -0.1 0.5 -0.6 -0.2",
        "pages": 31,
        "technical_page": 5,
        "table_page": 12,
        "value": -2,
        "prior_value": -6,
        "previous_value": -6,
        "revision_delta": 0,
        "covid": MARCH_COVID_MARKER,
    },
    date(2020, 5, 13): {
        "reference": "April 2020",
        "reference_month": "2020-04",
        "release_number": "USDL 20-0920",
        "weekday": "Wednesday",
        "headline": (
            "The Producer Price Index for final demand declined 1.3 percent in April, "
            "seasonally adjusted, the U.S. Bureau of Labor Statistics reported today."
        ),
        "prior": "Final demand prices fell 0.2 percent in March and 0.6 percent in February.",
        "yoy": (
            "On an unadjusted basis, the final demand index moved down 1.2 percent for the 12 "
            "months ended in April"
        ),
        "table_row": "Final demand 100.000 118.4 118.5 117.1 -1.2 -1.2 -0.6 -0.2 -1.3",
        "pages": 31,
        "technical_page": 5,
        "table_page": 12,
        "value": -13,
        "prior_value": -2,
        "previous_value": -2,
        "revision_delta": 0,
        "covid": APRIL_COVID_MARKER,
    },
}


def release_lines(release_date: date) -> list[str]:
    spec = RELEASES[release_date]
    return [
        (
            "Transmission of material in this release is embargoed until "
            f"{spec['release_number']} 8:30 a.m. (EDT), {spec['weekday']}, "
            f"{release_date:%B} {release_date.day}, {release_date:%Y}"
        ),
        f"PRODUCER PRICE INDEXES - {str(spec['reference']).upper()}",
        str(spec["headline"]),
        str(spec["prior"]),
        str(spec["yoy"]),
    ]


def html_bytes(
    *,
    release_date: date = RELEASE_DATE,
    replacements: dict[str, str] | None = None,
    extra: tuple[str, ...] = (),
) -> bytes:
    spec = RELEASES[release_date]
    lines = [
        *release_lines(release_date),
        TECHNICAL_DEFINITION,
        *([REVISION_MARKER] * 7),
        str(spec["table_row"]),
        *((str(spec["covid"]),) if spec["covid"] else ()),
        *extra,
    ]
    if replacements:
        lines = [_replace_all(line, replacements) for line in lines]
    body = "".join(f"<p>{line}</p>" for line in lines)
    return f"<!doctype html><html><body>{body}</body></html>".encode()


def pdf_bytes(
    *,
    release_date: date = RELEASE_DATE,
    replacements: dict[str, str] | None = None,
    pages: int | None = None,
    width: int = 612,
    height: int = 792,
    blank_page: int | None = None,
) -> bytes:
    spec = RELEASES[release_date]
    page_count = cast(int, spec["pages"]) if pages is None else pages
    page_lines = [[f"official PPI content page {index + 1}"] for index in range(page_count)]
    page_lines[0] = release_lines(release_date)
    technical_page = cast(int, spec["technical_page"])
    table_page = cast(int, spec["table_page"])
    if technical_page < page_count:
        page_lines[technical_page] = [
            "Technical Note Brief Explanation of Producer Price Indexes",
            TECHNICAL_DEFINITION,
            *([REVISION_MARKER] * 7),
        ]
    if table_page < page_count:
        page_lines[table_page] = [
            "Table 1. Producer price indexes and percent changes for final demand",
            str(spec["table_row"]),
        ]
    if table_page + 1 < page_count:
        page_lines[table_page + 1] = [
            "Table 1. Producer price indexes and percent changes for final demand - Continued"
        ]
    if page_count >= 2:
        page_lines[-2] = ["Table 7. Producer price indexes for selected final demand categories"]
        page_lines[-1] = ["Table 8. Producer price indexes for selected commodity groupings"]
    if spec["covid"]:
        page_lines[0].append(str(spec["covid"]))
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
) -> BLSPPIArchiveAdapter:
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
    return BLSPPIArchiveAdapter(safe, release_date=release_date)


def test_ppi_pair_is_exact_versioned_and_knowledge_safe() -> None:
    html = html_bytes()
    pdf = pdf_bytes()
    batch = adapter(html=html, pdf=pdf).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("202003:final_demand_monthly_change")
    assert record.entity_id == "bls_ppi:final_demand_united_states"
    assert record.source.sha256 == hashlib.sha256(pdf).hexdigest()
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.license_class is LicenseClass.REDISTRIBUTABLE
    assert record.source.vintage_as_of == datetime(2020, 4, 9, 12, 30, tzinfo=UTC)
    assert record.interval.valid_from == datetime(2020, 3, 1, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 4, 9, 12, 30, tzinfo=UTC)
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_tenths_percent"] == -2
    assert record.payload["value_basis_points"] == -20
    assert record.payload["prior_month_change_tenths_percent"] == -6
    assert record.payload["prior_month_revision_delta_tenths_percent"] == 0
    assert record.payload["year_over_year_change_tenths_percent"] == 7
    assert record.payload["table1_current_unadjusted_index"] == "118.5"
    assert record.payload["table1_unadjusted_monthly_change_tenths_percent"] == -1
    assert record.payload["html_pdf_crosscheck_verified"] is True
    assert record.payload["revision_window_months"] == 4
    assert record.payload["covid_methodology_statement"] == MARCH_COVID_MARKER
    assert [receipt.record_count for receipt in batch.receipts] == [0, 1]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert {artifact.content for artifact in batch.artifacts} == {html, pdf}

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 4, 9, 12, 29, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 4, 9, 12, 30, tzinfo=UTC)) == [record]


@pytest.mark.parametrize("release_date", tuple(RELEASES))
def test_verified_calendar_preserves_values_timing_and_adjacent_snapshot(
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
    assert record.payload["release_pdf_pages"] == spec["pages"]
    assert record.interval.available_at == datetime.combine(
        release_date,
        datetime.min.time(),
        tzinfo=UTC,
    ).replace(hour=12, minute=30)


def test_verified_calendar_and_response_urls_are_closed() -> None:
    client = cast(SafeHttpClient, object())
    with pytest.raises(ValueError, match="verified BLS PPI calendar"):
        BLSPPIArchiveAdapter(client, release_date=date(2020, 6, 11))

    item = BLSPPIArchiveAdapter(client, release_date=RELEASE_DATE)
    for kind, invalid in (
        ("html", "http://www.bls.gov/news.release/archives/ppi_04092020.htm"),
        ("html", "https://evil.example/news.release/archives/ppi_04092020.htm"),
        ("html", "https://www.bls.gov/news.release/archives/ppi_04092020.pdf"),
        ("pdf", "https://www.bls.gov/news.release/archives/other.pdf"),
        ("pdf", "https://www.bls.gov/news.release/archives/ppi_04092020.pdf?q=1"),
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
        (pdf_bytes(pages=30), "page count"),
        (pdf_bytes(width=611), "geometry"),
        (pdf_bytes(blank_page=4), "blank text layer"),
        (
            pdf_bytes(replacements={"PRODUCER PRICE INDEXES": "OTHER INDEXES"}),
            "first-page identity",
        ),
        (
            pdf_bytes(replacements={"Technical Note": "Other Note"}),
            "technical-note page",
        ),
        (
            pdf_bytes(replacements={"118.5 0.7": "118.4 0.7"}),
            "Table 1 values",
        ),
        (
            pdf_bytes(replacements={"Table 7.": "Other Table."}),
            "Table 7 page",
        ),
        (
            pdf_bytes(replacements={"Table 8.": "Other Table."}),
            "Table 8 page",
        ),
        (
            pdf_bytes(replacements={MARCH_COVID_MARKER: "no methodology marker"}),
            "COVID-19 methodology",
        ),
    ],
)
def test_pdf_structure_identity_table_and_methodology_fail_closed(
    pdf: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(pdf=pdf).fetch()


@pytest.mark.parametrize(
    ("replacements", "message"),
    [
        ({"USDL 20-0567": "USDL 20-9999"}, "identity or headline"),
        ({"fell 0.2 percent": "rose 0.2 percent"}, "identity or headline"),
        ({"118.5 0.7": "118.4 0.7"}, "Table 1 values"),
        ({REVISION_MARKER: "No revision rule."}, "revision rule"),
        ({TECHNICAL_DEFINITION: "different definition"}, "identity or headline"),
        ({MARCH_COVID_MARKER: "no methodology marker"}, "COVID-19 methodology"),
    ],
)
def test_html_pair_corruption_fails_closed(
    replacements: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(html=html_bytes(replacements=replacements)).fetch()


def test_content_types_and_invalid_html_encoding_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="HTML content type"):
        adapter(html_content_type="application/json").fetch()
    with pytest.raises(SourceSchemaError, match="PDF content type"):
        adapter(pdf_content_type="text/plain").fetch()
    with pytest.raises(SourceSchemaError, match="neither valid UTF-8 nor Windows-1252"):
        adapter(html=b"\x81").fetch()


def test_response_url_and_prepublication_retrieval_fail_closed() -> None:
    valid_html = html_bytes()
    valid_pdf = pdf_bytes()

    class WrongURLClient:
        def get(self, url: str, **_kwargs: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
            content = valid_html if url.endswith(".htm") else valid_pdf
            content_type = "text/html" if url.endswith(".htm") else "application/pdf"
            request_url = (
                "https://www.bls.gov/news.release/archives/ppi_03122020.htm"
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

    wrong = BLSPPIArchiveAdapter(cast(SafeHttpClient, WrongURLClient()), release_date=RELEASE_DATE)
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
                datetime(2020, 4, 9, 12, 29, 59, tzinfo=UTC)
                if url.endswith(".htm")
                else datetime(2026, 1, 1, tzinfo=UTC)
            )
            return snapshot, content, retrieved_at

    early = BLSPPIArchiveAdapter(cast(SafeHttpClient, EarlyClient()), release_date=RELEASE_DATE)
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
