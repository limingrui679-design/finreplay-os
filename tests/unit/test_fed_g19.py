# ruff: noqa: E501  # Exact official table-row fixtures intentionally remain unwrapped.

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import FederalReserveG19ArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 4, 7)
PDF_URL = "https://www.federalreserve.gov/releases/g19/20200407/g19.pdf"
NOTES = (
    "Starting with the April 2020 G.19 Consumer Credit release, scheduled to be published "
    "on June 5, 2020, the release will no longer report the levels and flows of on-book "
    "loan balances and off-book securitized loan balances as separate line items."
)
SIMPLE_RATE = "percent changes are at a simple annual rate and are calculated from unrounded data"
LEGEND = "r=revised. p=preliminary. n.a.=not available. ...=not applicable."
RELEASES = {
    date(2020, 3, 6): {
        "month": "January 2020",
        "release": "March 6, 2020",
        "headline": (
            "In January, consumer credit increased at a seasonally adjusted annual rate of "
            "3-1/2 percent. Revolving credit decreased at an annual rate of 3-1/4 percent, "
            "while nonrevolving credit increased at an annual rate of 5-3/4 percent."
        ),
        "header": "Novr Decr Janp",
        "rows": (
            "Total percent change (annual rate)2 7.1 6.8 5.1 4.7 4.5 5.4 4.3 4.2 4.9 4.5 2.7 5.8 3.4",
            "Revolving 5.4 6.8 5.6 3.1 3.8 5.0 1.5 5.2 3.6 4.6 -5.0 12.2 -3.3",
            "Nonrevolving3 7.7 6.9 4.9 5.4 4.8 5.6 5.3 3.8 5.3 4.4 5.5 3.6 5.8",
            "Total flow (annual rate)2,4 233.8 233.1 184.0 181.8 181.0 214.7 171.2 168.5 199.7 184.8 114.2 243.0 144.3",
            "Revolving 48.0 61.2 54.2 31.6 39.7 52.0 15.9 54.8 38.6 49.4 -54.3 132.3 -36.4",
            "Nonrevolving3 185.9 171.9 129.9 150.2 141.3 162.7 155.2 113.6 161.1 135.4 168.4 110.7 180.7",
            "Total outstanding 3,411.0 3,644.1 3,828.2 4,009.7 4,190.7 4,009.7 4,052.5 4,094.6 4,144.5 4,190.7 4,170.5 4,190.7 4,202.7",
            "Revolving 906.7 968.0 1,022.1 1,053.5 1,093.2 1,053.5 1,057.5 1,071.2 1,080.8 1,093.2 1,082.2 1,093.2 1,090.1",
            "Nonrevolving3 2,504.3 2,676.2 2,806.1 2,956.2 3,097.5 2,956.2 2,995.0 3,023.4 3,063.7 3,097.5 3,088.3 3,097.5 3,112.6",
        ),
    },
    date(2020, 4, 7): {
        "month": "February 2020",
        "release": "April 7, 2020",
        "headline": (
            "In February, consumer credit increased at a seasonally adjusted annual rate of "
            "6-1/2 percent. Revolving credit increased at an annual rate of 4-1/2 percent, "
            "while nonrevolving credit increased at an annual rate of 7 percent."
        ),
        "header": "Decr Janr Febp",
        "rows": (
            "Total percent change (annual rate)2 7.1 6.8 5.1 4.8 4.5 5.1 4.6 4.3 4.8 4.0 6.0 3.5 6.4",
            "Revolving 5.5 6.8 5.6 3.1 3.8 4.1 2.5 4.6 4.3 3.4 12.6 -2.7 4.6",
            "Nonrevolving3 7.6 6.9 4.9 5.3 4.8 5.5 5.3 4.2 5.0 4.2 3.7 5.6 7.0",
            "Total flow (annual rate)2,4 233.7 233.1 185.6 182.0 180.4 202.4 182.7 174.8 197.0 167.2 252.0 144.7 268.0",
            "Revolving 48.7 61.3 54.4 32.0 39.7 42.8 26.3 49.0 46.2 37.3 136.9 -29.4 50.4",
            "Nonrevolving3 185.0 171.8 131.2 150.0 140.7 159.6 156.4 125.8 150.8 129.9 115.0 174.2 217.6",
            "Total outstanding 3,410.3 3,643.4 3,829.0 4,010.7 4,191.1 4,010.7 4,056.4 4,100.1 4,149.3 4,191.1 4,191.1 4,203.2 4,225.5",
            "Revolving 907.2 968.5 1,022.9 1,054.6 1,094.3 1,054.6 1,061.2 1,073.5 1,085.0 1,094.3 1,094.3 1,091.9 1,096.1",
            "Nonrevolving3 2,503.1 2,674.9 2,806.1 2,956.1 3,096.8 2,956.1 2,995.1 3,026.6 3,064.3 3,096.8 3,096.8 3,111.3 3,129.4",
        ),
    },
    date(2020, 5, 7): {
        "month": "March 2020",
        "release": "May 7, 2020",
        "headline": (
            "Consumer credit increased at a seasonally adjusted annual rate of 1-3/4 percent "
            "during the first quarter. Revolving credit decreased at an annual rate of "
            "10-1/4 percent, while nonrevolving credit increased at an annual rate of 6 "
            "percent. In March, revolving credit decreased at an annual rate of 31 percent, "
            "while nonrevolving credit increased at an annual rate of 6-1/4 percent."
        ),
        "header": "Janr Febr Marp",
        "rows": (
            "Total percent change (annual rate)2 7.1 6.8 5.1 4.8 4.5 4.5 4.3 4.8 4.0 1.7 3.0 5.7 -3.4",
            "Revolving 5.5 6.8 5.6 3.1 3.8 2.5 4.6 4.3 3.5 -10.3 -3.7 3.6 -30.9",
            "Nonrevolving3 7.6 6.9 4.9 5.3 4.8 5.3 4.2 5.0 4.2 6.0 5.3 6.4 6.2",
            "Total flow (annual rate)2,4 233.7 233.1 185.6 182.0 180.4 182.5 174.8 197.1 167.4 72.8 123.8 239.0 -144.5",
            "Revolving 48.7 61.3 54.4 32.0 39.7 26.1 49.0 46.3 37.5 -113.0 -40.5 39.4 -338.1",
            "Nonrevolving3 185.0 171.8 131.2 150.0 140.7 156.4 125.8 150.8 129.9 185.8 164.3 199.6 193.6",
            "Total outstanding 3,410.3 3,643.4 3,829.0 4,010.7 4,191.1 4,056.3 4,100.0 4,149.3 4,191.1 4,209.3 4,201.4 4,221.4 4,209.3",
            "Revolving 907.2 968.5 1,022.9 1,054.6 1,094.3 1,061.2 1,073.4 1,085.0 1,094.3 1,066.1 1,091.0 1,094.3 1,066.1",
            "Nonrevolving3 2,503.1 2,674.9 2,806.1 2,956.1 3,096.8 2,995.1 3,026.6 3,064.3 3,096.8 3,143.2 3,110.5 3,127.1 3,143.2",
        ),
    },
}


def pdf_bytes(
    *,
    release_date: date = RELEASE_DATE,
    replacements: dict[str, str] | None = None,
    pages: int = 4,
    width: int = 612,
    height: int = 792,
    rotation: int = 90,
    blank_page: int | None = None,
) -> bytes:
    spec = RELEASES[release_date]
    page_lines = [
        [
            "G.19",
            "Consumer Credit",
            str(spec["month"]),
            "For release at 3 p.m. (Eastern Time)",
            str(spec["release"]),
            "Notes about the Data",
            NOTES,
        ],
        [
            "G.19 Consumer Credit",
            "For release at 3 p.m. (Eastern Time)",
            str(spec["month"]),
            str(spec["release"]),
            str(spec["headline"]),
            "Consumer Credit Outstanding Seasonally adjusted. Billions of dollars except as noted.",
            str(spec["header"]),
            *cast(tuple[str, ...], spec["rows"]),
            "This release is generally issued on the fifth business day of each month.",
        ],
        [
            "Consumer Credit Outstanding (Levels)",
            "Non seasonally adjusted",
            "Billions of dollars",
            SIMPLE_RATE,
            "official G.19 levels table",
        ],
        [
            "Consumer Credit Outstanding (Flows)",
            "Not seasonally adjusted",
            "Billions of dollars, annual rate",
            "official G.19 flows table",
            LEGEND,
        ],
    ]
    if replacements:
        page_lines = [[_replace_all(line, replacements) for line in lines] for lines in page_lines]
    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=width, height=height)
        if rotation:
            writer.pages[index].rotate(rotation)
        if index != blank_page:
            lines = page_lines[index] if index < len(page_lines) else ["extra page"]
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
    commands = ["BT", "/F1 5 Tf", "30 760 Td", "7 TL"]
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
    content: bytes | None = None,
    content_type: str = "application/pdf",
) -> FederalReserveG19ArchiveAdapter:
    selected = content if content is not None else pdf_bytes(release_date=release_date)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=selected,
            headers={"Content-Type": content_type},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return FederalReserveG19ArchiveAdapter(safe, release_date=release_date)


def test_g19_snapshot_is_versioned_exact_and_knowledge_safe() -> None:
    content = pdf_bytes()
    batch = adapter(content=content).fetch()
    assert len(batch.records) == 2
    january, february = batch.records
    assert january.record_id.endswith("202001:revolving_percent_change_annual_rate")
    assert february.record_id.endswith("202002:revolving_percent_change_annual_rate")
    assert january.source.sha256 == hashlib.sha256(content).hexdigest()
    assert january.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert january.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert january.source.vintage_as_of == datetime(2020, 4, 7, 19, 0, tzinfo=UTC)
    assert january.interval.valid_from == datetime(2020, 1, 1, tzinfo=UTC)
    assert january.interval.available_at == datetime(2020, 4, 7, 19, 0, tzinfo=UTC)
    assert january.interval.revised_at == datetime(2020, 4, 7, 19, 0, tzinfo=UTC)
    assert february.interval.revised_at is None
    assert january.evidence_class is EvidenceClass.REPORTED
    assert january.payload["value_basis_points"] == -270
    assert january.payload["previous_release_same_reference_revolving_change_basis_points"] == -330
    assert january.payload["revision_delta_basis_points"] == 60
    assert february.payload["value_basis_points"] == 460
    assert february.payload["reported_total_change_percent"] == "6.4"
    assert february.payload["reported_nonrevolving_change_percent"] == "7.0"
    assert february.payload["revolving_flow_tenths_billion_dollars"] == 504
    assert february.payload["revolving_outstanding_tenths_billion_dollars"] == 10_961
    assert february.payload["estimate_status"] == "preliminary"
    assert february.payload["simple_annual_rate_from_unrounded_data"] is True
    assert february.payload["release_pdf_pages"] == 4
    assert batch.receipts[0].record_count == 2
    assert batch.receipts[0].historical_replay_eligible is True
    assert batch.artifacts[0].content == content


def test_three_releases_preserve_all_six_versions_and_asof_views() -> None:
    batches = [adapter(release_date=item).fetch() for item in sorted(RELEASES)]
    records = tuple(record for batch in batches for record in batch.records)
    assert len(records) == 6
    with TimeVault() as vault:
        receipt = vault.append(records)
        assert receipt.inserted_records == 6
        assert vault.records_as_of(datetime(2020, 3, 6, 19, 59, 59, tzinfo=UTC)) == []
        march_view = vault.records_as_of(datetime(2020, 3, 6, 20, 0, tzinfo=UTC))
        assert [record.payload["value_basis_points"] for record in march_view] == [-330]
        april_view = vault.records_as_of(datetime(2020, 4, 7, 19, 0, tzinfo=UTC))
        assert [record.payload["value_basis_points"] for record in april_view] == [-270, 460]
        may_view = vault.records_as_of(datetime(2020, 5, 7, 19, 0, tzinfo=UTC))
        assert [record.payload["value_basis_points"] for record in may_view] == [
            -370,
            360,
            -3090,
        ]
        january_id = march_view[0].record_id
        assert [item.payload["value_basis_points"] for item in vault.history(january_id)] == [
            -330,
            -270,
            -370,
        ]


@pytest.mark.parametrize(
    ("release_date", "record_count", "available_at", "values", "statuses"),
    [
        (
            date(2020, 3, 6),
            1,
            datetime(2020, 3, 6, 20, 0, tzinfo=UTC),
            [-330],
            ["preliminary"],
        ),
        (
            date(2020, 4, 7),
            2,
            datetime(2020, 4, 7, 19, 0, tzinfo=UTC),
            [-270, 460],
            ["revised", "preliminary"],
        ),
        (
            date(2020, 5, 7),
            3,
            datetime(2020, 5, 7, 19, 0, tzinfo=UTC),
            [-370, 360, -3090],
            ["revised", "revised", "preliminary"],
        ),
    ],
)
def test_verified_calendar_values_statuses_and_timezone_offsets(
    release_date: date,
    record_count: int,
    available_at: datetime,
    values: list[int],
    statuses: list[str],
) -> None:
    records = adapter(release_date=release_date).fetch().records
    assert len(records) == record_count
    assert [record.interval.available_at for record in records] == [available_at] * record_count
    assert [record.payload["value_basis_points"] for record in records] == values
    assert [record.payload["estimate_status"] for record in records] == statuses


def test_verified_calendar_and_response_url_are_closed() -> None:
    client = cast(SafeHttpClient, object())
    for release_date in RELEASES:
        item = FederalReserveG19ArchiveAdapter(client, release_date=release_date)
        assert item.endpoint.endswith(f"/{release_date:%Y%m%d}/g19.pdf")
    with pytest.raises(ValueError, match=r"verified Federal Reserve G\.19 calendar"):
        FederalReserveG19ArchiveAdapter(client, release_date=date(2020, 6, 5))
    item = FederalReserveG19ArchiveAdapter(client, release_date=RELEASE_DATE)
    for invalid in (
        "http://www.federalreserve.gov/releases/g19/20200407/g19.pdf",
        "https://evil.example/releases/g19/20200407/g19.pdf",
        "https://www.federalreserve.gov/releases/g19/20200507/g19.pdf",
        PDF_URL + "?download=1",
    ):
        with pytest.raises(SourceSchemaError, match="response URL"):
            item._validate_response_url(invalid)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (pdf_bytes(pages=3), "exactly four pages"),
        (pdf_bytes(blank_page=2), "blank text layer"),
        (pdf_bytes(width=611), "dimensions"),
        (pdf_bytes(rotation=0), "rotation"),
        (
            pdf_bytes(replacements={"3 p.m. (Eastern Time)": "3:01 p.m. (Eastern Time)"}),
            "release-time identity",
        ),
        (
            pdf_bytes(replacements={"April 7, 2020": "April 8, 2020"}),
            "release-date identity",
        ),
        (
            pdf_bytes(replacements={"February 2020": "Other month"}),
            "release identity",
        ),
        (
            pdf_bytes(replacements={"increased at an annual rate of 4-1/2": "was unavailable"}),
            "headline",
        ),
        (
            pdf_bytes(replacements={"-2.7 4.6": "-2.7 4.7"}),
            "table values",
        ),
        (
            pdf_bytes(replacements={"on-book loan balances": "other balances"}),
            "release notes",
        ),
        (
            pdf_bytes(replacements={SIMPLE_RATE: "rates are unavailable"}),
            "rate footnote",
        ),
        (
            pdf_bytes(replacements={LEGEND: "estimate legend unavailable"}),
            "estimate legend",
        ),
    ],
)
def test_pdf_structure_timing_values_and_revision_markers_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(content=content).fetch()


def test_content_type_and_prepublication_retrieval_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match=r"unexpected G\.19 PDF content type"):
        adapter(content_type="text/html").fetch()
    content = pdf_bytes()
    snapshot = HttpResponseSnapshot(
        status_code=200,
        headers={"Content-Type": "application/pdf"},
        request_url=PDF_URL,
        content=content,
    )

    class EarlyClient:
        def get(self, *_: Any, **__: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
            return snapshot, content, datetime(2020, 4, 7, 18, 59, 59, tzinfo=UTC)

    early = FederalReserveG19ArchiveAdapter(
        cast(SafeHttpClient, EarlyClient()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
