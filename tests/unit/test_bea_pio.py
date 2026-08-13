from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import BEAPersonalIncomeOutlaysArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 3, 27)
HTML_URL = "https://www.bea.gov/news/2020/personal-income-and-outlays-february-2020"
PDF_URL = "https://www.bea.gov/sites/default/files/2020-03/pi0220_1.pdf"


def html_bytes(
    *,
    embargo: str = "EMBARGOED UNTIL RELEASE AT 8:30 A.M. EDT, FRIDAY, MARCH 27, 2020",
    release_number: str = "BEA 20-14",
    title: str = "Personal Income and Outlays: February 2020",
    headline: str = (
        "Personal income increased $106.8 billion (0.6 percent) in February according to "
        "estimates released today by the Bureau of Economic Analysis."
    ),
    disposable: str = ("Disposable personal income (DPI) increased $88.7 billion (0.5 percent)."),
    pce: str = ("personal consumption expenditures (PCE) increased $27.7 billion (0.2 percent)."),
    saving: str = (
        "Personal saving was $1.38 trillion and the personal saving rate, personal saving as a "
        "percentage of disposable personal income, was 8.2 percent (table 1)."
    ),
    pdf_path: str = "/sites/default/files/2020-03/pi0220_1.pdf",
    doctype: bool = True,
    duplicate_article: bool = False,
    invalid_utf8: bool = False,
) -> bytes:
    article = f"""
<article about="/news/2020/personal-income-and-outlays-february-2020">
<div>{embargo}</div><div>{release_number}</div><h1>{title}</h1>
<p>{headline} {disposable} {pce}</p><p>{saving}</p>
<a href="{pdf_path}">Full Release &amp; Tables</a>
</article>
"""
    content = f"{'<!doctype html>' if doctype else ''}<html>{article}"
    if duplicate_article:
        content += article
    content += "</html>"
    encoded = content.encode()
    return encoded + (b"\xff" if invalid_utf8 else b"")


def pdf_bytes(
    *,
    replacements: dict[str, str] | None = None,
    pages: int = 11,
    width: int = 612,
    height: int = 792,
    blank_page: int | None = None,
) -> bytes:
    lines = [
        "EMBARGOED UNTIL RELEASE AT 8:30 A.M. EDT, FRIDAY, MARCH 27, 2020",
        "BEA 20-14",
        "Personal Income and Outlays: February 2020",
        "Personal income increased $106.8 billion (0.6 percent) in February",
        "Disposable personal income (DPI) increased $88.7 billion (0.5 percent)",
        "personal consumption expenditures (PCE) increased $27.7 billion (0.2 percent)",
        "Personal saving was $1.38 trillion",
        "was 8.2 percent (table 1).",
        "Table 1. Personal Income and Its Disposition (Months)",
        (
            "Personal saving as a percentage of disposable personal income "
            "7.4 7.7 7.8 7.6 7.7 7.5 7.9 8.2 44"
        ),
    ]
    if replacements:
        lines = [_replace_all(line, replacements) for line in lines]
    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=width, height=height)
        if index != blank_page:
            _write_page_text(
                writer, index, lines if index == 0 else [f"BEA table page {index + 1}"]
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
) -> BEAPersonalIncomeOutlaysArchiveAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/news/"):
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
    return BEAPersonalIncomeOutlaysArchiveAdapter(safe, release_date=RELEASE_DATE)


def test_archived_pio_pair_is_exact_versioned_and_knowledge_safe() -> None:
    html_content = html_bytes()
    pdf_content = pdf_bytes()
    batch = adapter(html_content=html_content, pdf_content=pdf_content).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("202002:personal_saving_rate")
    assert record.entity_id == "bea_pio:united_states"
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.vintage_as_of == datetime(2020, 3, 27, 12, 30, tzinfo=UTC)
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.sha256 == hashlib.sha256(pdf_content).hexdigest()
    assert record.interval.valid_from == datetime(2020, 2, 1, tzinfo=UTC)
    assert record.interval.published_at == datetime(2020, 3, 27, 12, 30, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 3, 27, 12, 30, tzinfo=UTC)
    assert record.interval.availability_confidence == 1.0
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_basis_points"] == 820
    assert record.payload["reported_saving_rate_percent"] == "8.2"
    assert record.payload["personal_saving_trillion_dollars"] == "1.38"
    assert record.payload["prior_month_rate_in_current_release_basis_points"] == 790
    assert record.payload["prior_month_rate_in_previous_release_basis_points"] == 790
    assert record.payload["prior_month_revision_delta_basis_points"] == 0
    assert record.payload["official_release_at"] == "2020-03-27T12:30:00+00:00"
    assert record.payload["html_pdf_crosscheck_verified"] is True
    assert record.payload["table1_snapshot_verified"] is True
    assert record.payload["release_html_sha256"] == hashlib.sha256(html_content).hexdigest()
    assert record.payload["release_pdf_sha256"] == hashlib.sha256(pdf_content).hexdigest()
    assert [receipt.record_count for receipt in batch.receipts] == [0, 1]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert len(batch.artifacts) == 2
    assert {artifact.content for artifact in batch.artifacts} == {html_content, pdf_content}

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 3, 27, 12, 29, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 3, 27, 12, 30, tzinfo=UTC)) == [record]


def test_verified_calendar_preserves_release_snapshots_and_rejects_other_dates() -> None:
    client = cast(SafeHttpClient, object())
    january = BEAPersonalIncomeOutlaysArchiveAdapter(client, release_date=date(2020, 2, 28))
    march = BEAPersonalIncomeOutlaysArchiveAdapter(client, release_date=date(2020, 4, 30))
    assert january.spec.saving_rate_percent == "7.9"
    assert january.spec.timezone_abbreviation == "EST"
    assert january.spec.prior_month_previous_release_basis_points is None
    assert march.spec.saving_rate_percent == "13.1"
    assert march.spec.prior_month_rate_basis_points == 800
    assert march.spec.prior_month_previous_release_basis_points == 820
    with pytest.raises(ValueError, match="verified BEA PIO calendar"):
        BEAPersonalIncomeOutlaysArchiveAdapter(client, release_date=date(2020, 5, 29))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (html_bytes(doctype=False), "not an HTML"),
        (html_bytes(invalid_utf8=True), "not valid UTF-8"),
        (html_bytes(duplicate_article=True), "one matching release article"),
        (html_bytes(embargo="EMBARGOED UNTIL 9:00 A.M."), "embargo identity"),
        (html_bytes(release_number="BEA 20-99"), "headline values"),
        (html_bytes(title="Other release"), "headline values"),
        (html_bytes(headline="Personal income was unavailable."), "headline values"),
        (html_bytes(saving="Personal saving was unavailable."), "headline values"),
        (html_bytes(pdf_path="/sites/default/files/other.pdf"), "PDF link"),
    ],
)
def test_html_identity_embargo_values_and_link_fail_closed(
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
        (pdf_bytes(pages=10), "page count"),
        (pdf_bytes(width=600), "US Letter"),
        (pdf_bytes(blank_page=4), "blank text layer"),
        (
            pdf_bytes(replacements={"8:30 A.M. EDT": "9:30 A.M. EDT"}),
            "embargo identity",
        ),
        (pdf_bytes(replacements={"BEA 20-14": "BEA 20-99"}), "identity"),
        (
            pdf_bytes(replacements={"Personal Income and Outlays": "Other Release"}),
            "identity",
        ),
        (pdf_bytes(replacements={"$1.38 trillion": "$1.39 trillion"}), "identity"),
        (pdf_bytes(replacements={"7.9 8.2": "7.9 8.3"}), "Table 1"),
    ],
)
def test_pdf_layout_embargo_identity_and_table_snapshot_fail_closed(
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
    wrong_html = BEAPersonalIncomeOutlaysArchiveAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                html_url=HTML_URL.replace("february", "january"),
                pdf_url=PDF_URL,
                retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="HTML response URL"):
        wrong_html.fetch()

    wrong_pdf = BEAPersonalIncomeOutlaysArchiveAdapter(
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

    early = BEAPersonalIncomeOutlaysArchiveAdapter(
        cast(
            SafeHttpClient,
            PairClient(
                html_url=HTML_URL,
                pdf_url=PDF_URL,
                retrieved_at=datetime(2020, 3, 27, 12, 29, 59, tzinfo=UTC),
            ),
        ),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
