from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import FederalReserveG17ArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 3, 17)
HTML_URL = "https://www.federalreserve.gov/releases/g17/20200317/default.htm"
PDF_URL = "https://www.federalreserve.gov/releases/g17/20200317/g17.pdf"


def html_bytes(
    *,
    release_date: str = "March 17, 2020",
    title: str = "Industrial Production and Capacity Utilization - G.17",
    headline: str = (
        "Industrial production rose 0.6 percent in February after falling 0.5 percent in January"
    ),
    index: str = "At 109.6 percent of its 2012 average",
    capacity: str = (
        "Capacity utilization for the industrial sector increased 0.4 percentage point in "
        "February to 77.0 percent"
    ),
    manufacturing: str = "Manufacturing output edged up 0.1 percent in February",
    mining: str = "Mining output fell 1.5 percent in February",
    utilities: str = "output of utilities jumped 7.1 percent in February",
    total_row: str = ("Total index 109.5 109.0 110.0 109.6 109.0 109.6 -.3 -.4 .9 -.4 -.5 .6 .0"),
    previous_row: str = ("Previous estimates 109.5 109.0 110.0 109.5 109.2 -.3 -.4 .9 -.4 -.3"),
    pdf_path: str = "g17.pdf",
    doctype: bool = True,
    duplicate_document: bool = False,
    invalid_utf8: bool = False,
    shell_token: str = "token-a",
) -> bytes:
    document = f"""
<html><body><h1>{title}</h1>
<div>Release Date: {release_date}</div>
<a href="{pdf_path}">PDF</a>
<p>{headline}. {index}. {capacity}.</p>
<p>{manufacturing}. {mining}. The {utilities}.</p>
<table><tr><td>{total_row}</td></tr><tr><td>{previous_row}</td></tr></table>
<div>Last Update: {release_date}</div><script data-shell="{shell_token}"></script>
</body></html>
"""
    content = f"{'<!DOCTYPE html>' if doctype else ''}{document}"
    if duplicate_document:
        content += document
    encoded = content.encode()
    return encoded + (b"\xff" if invalid_utf8 else b"")


def pdf_bytes(
    *,
    replacements: dict[str, str] | None = None,
    pages: int = 19,
    width: int = 612,
    height: int = 792,
    blank_page: int | None = None,
) -> bytes:
    lines = [
        "FEDERAL RESERVE statistical release",
        "G.17 (419)",
        "For release at 9:15 a.m. (EDT) March 17, 2020",
        "INDUSTRIAL PRODUCTION AND CAPACITY UTILIZATION",
        "Industrial production rose 0.6 percent in February after falling 0.5 percent in January",
        "At 109.6 percent of its 2012 average",
        (
            "Capacity utilization for the industrial sector increased 0.4 percentage point in "
            "February to 77.0 percent"
        ),
        "Manufacturing output edged up 0.1 percent in February",
        "Mining output fell 1.5 percent in February",
        "output of utilities jumped 7.1 percent in February",
        ("Total index 109.5 109.0 110.0 109.6 109.0 109.6 -.3 -.4 .9 -.4 -.5 .6 .0"),
        "Previous estimates 109.5 109.0 110.0 109.5 109.2 -.3 -.4 .9 -.4 -.3",
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
                lines if index == 0 else [f"G.17 table page {index + 1}"],
            )
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
    html_content: bytes | None = None,
    pdf_content: bytes | None = None,
    html_content_type: str = "text/html; charset=UTF-8",
    pdf_content_type: str = "application/pdf",
) -> FederalReserveG17ArchiveAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("default.htm"):
            return response(
                request,
                html_content if html_content is not None else html_bytes(),
                content_type=html_content_type,
            )
        return response(
            request,
            pdf_content if pdf_content is not None else pdf_bytes(),
            content_type=pdf_content_type,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return FederalReserveG17ArchiveAdapter(safe, release_date=RELEASE_DATE)


def test_archived_g17_pair_is_exact_versioned_and_knowledge_safe() -> None:
    html_content = html_bytes()
    pdf_content = pdf_bytes()
    batch = adapter(html_content=html_content, pdf_content=pdf_content).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("202002:monthly_change")
    assert record.entity_id == "federal_reserve_g17:total_industrial_production"
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.vintage_as_of == datetime(2020, 3, 17, 13, 15, tzinfo=UTC)
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.sha256 == hashlib.sha256(pdf_content).hexdigest()
    assert record.interval.valid_from == datetime(2020, 2, 1, tzinfo=UTC)
    assert record.interval.published_at == datetime(2020, 3, 17, 13, 15, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 3, 17, 13, 15, tzinfo=UTC)
    assert record.interval.availability_confidence == 1.0
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_basis_points"] == 60
    assert record.payload["reported_monthly_change_percent"] == "0.6"
    assert record.payload["total_index_2012_equals_100"] == "109.6"
    assert record.payload["capacity_utilization_percent"] == "77.0"
    assert record.payload["manufacturing_monthly_change_percent"] == "0.1"
    assert record.payload["mining_monthly_change_percent"] == "-1.5"
    assert record.payload["utilities_monthly_change_percent"] == "7.1"
    assert record.payload["prior_month_change_in_current_release_basis_points"] == -50
    assert record.payload["prior_month_change_in_previous_release_basis_points"] == -30
    assert record.payload["prior_month_revision_delta_basis_points"] == -20
    assert record.payload["official_release_at"] == "2020-03-17T13:15:00+00:00"
    assert record.payload["html_pdf_crosscheck_verified"] is True
    assert record.payload["summary_table_snapshot_verified"] is True
    assert len(record.payload["release_html_fact_sha256"]) == 64
    assert record.payload["release_pdf_sha256"] == hashlib.sha256(pdf_content).hexdigest()
    assert [receipt.record_count for receipt in batch.receipts] == [0, 1]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert len(batch.artifacts) == 2
    assert {artifact.content for artifact in batch.artifacts} == {html_content, pdf_content}

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 3, 17, 13, 14, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 3, 17, 13, 15, tzinfo=UTC)) == [record]


def test_dynamic_html_shell_changes_raw_receipt_but_not_normalized_release_fact() -> None:
    first = adapter(html_content=html_bytes(shell_token="token-a")).fetch()
    second = adapter(html_content=html_bytes(shell_token="token-b")).fetch()

    assert first.receipts[0].response_sha256 != second.receipts[0].response_sha256
    assert first.records[0].source.source_version == second.records[0].source.source_version
    assert (
        first.records[0].payload["release_html_fact_sha256"]
        == second.records[0].payload["release_html_fact_sha256"]
    )
    assert first.records[0].payload == second.records[0].payload


def test_verified_calendar_preserves_release_snapshots_and_rejects_other_dates() -> None:
    client = cast(SafeHttpClient, object())
    january = FederalReserveG17ArchiveAdapter(client, release_date=date(2020, 2, 14))
    march = FederalReserveG17ArchiveAdapter(client, release_date=date(2020, 4, 15))
    assert january.spec.monthly_change_percent == "-0.3"
    assert january.spec.timezone_abbreviation == "EST"
    assert january.spec.prior_month_previous_release_percent == "-0.3"
    assert march.spec.monthly_change_percent == "-5.4"
    assert march.spec.prior_month_change_percent == "0.5"
    assert march.spec.prior_month_previous_release_percent == "0.6"
    with pytest.raises(ValueError, match=r"verified Federal Reserve G\.17 calendar"):
        FederalReserveG17ArchiveAdapter(client, release_date=date(2020, 5, 15))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (html_bytes(doctype=False), "not an HTML"),
        (html_bytes(invalid_utf8=True), "not valid UTF-8"),
        (html_bytes(duplicate_document=True), "one HTML document"),
        (html_bytes(release_date="March 18, 2020"), "release date identity"),
        (html_bytes(title="Other release"), "release identity"),
        (html_bytes(headline="Industrial production was unavailable"), "headline"),
        (html_bytes(total_row="Total index unavailable"), "summary-table"),
        (html_bytes(pdf_path="other.pdf"), "PDF link"),
    ],
)
def test_html_identity_values_table_and_link_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(html_content=content).fetch()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (pdf_bytes(pages=18), "page count"),
        (pdf_bytes(width=600), "US Letter"),
        (pdf_bytes(blank_page=4), "blank text layer"),
        (
            pdf_bytes(replacements={"9:15 a.m. (EDT)": "9:30 a.m. (EDT)"}),
            "release-time identity",
        ),
        (pdf_bytes(replacements={"G.17 (419)": "G.17 (999)"}), "release identity"),
        (
            pdf_bytes(replacements={"INDUSTRIAL PRODUCTION": "OTHER PRODUCTION"}),
            "release identity",
        ),
        (pdf_bytes(replacements={"rose 0.6": "rose 0.7"}), "headline"),
        (pdf_bytes(replacements={"-.5 .6 .0": "-.5 .7 .0"}), "summary-table"),
    ],
)
def test_pdf_layout_time_identity_and_table_snapshot_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(pdf_content=content).fetch()


def test_response_content_types_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="HTML content type"):
        adapter(html_content_type="application/json").fetch()
    with pytest.raises(SourceSchemaError, match="PDF content type"):
        adapter(pdf_content_type="text/html").fetch()


class PairClient:
    def __init__(
        self,
        *,
        html_url: str,
        pdf_url: str,
        retrieved_at: datetime,
    ) -> None:
        self.urls = (html_url, pdf_url)
        self.retrieved_at = retrieved_at
        self.calls = 0

    def get(self, *_args: Any, **_kwargs: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
        position = self.calls
        self.calls += 1
        if position == 0:
            content = html_bytes()
            content_type = "text/html"
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


def test_response_urls_and_future_release_cannot_be_backdated() -> None:
    wrong_html = FederalReserveG17ArchiveAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                html_url=HTML_URL.replace("20200317", "20200214"),
                pdf_url=PDF_URL,
                retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="HTML response URL"):
        wrong_html.fetch()

    wrong_pdf = FederalReserveG17ArchiveAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                html_url=HTML_URL,
                pdf_url=PDF_URL + "?download=1",
                retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="PDF response URL"):
        wrong_pdf.fetch()

    early = FederalReserveG17ArchiveAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                html_url=HTML_URL,
                pdf_url=PDF_URL,
                retrieved_at=datetime(2020, 3, 17, 13, 14, 59, tzinfo=UTC),
            ),
        ),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
