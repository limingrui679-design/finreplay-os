from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime
from io import BytesIO
from typing import cast
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest
import xlrd
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

import finreplay.adapters.census_ft900 as ft900_module
from finreplay.adapters import CensusBEAFT900ArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 4, 2)
PDF_URL = "https://www.census.gov/foreign-trade/Press-Release/ft900/ft900_2002.pdf"
ZIP_URL = "https://www.census.gov/foreign-trade/Press-Release/ft900/ft900xls_2002.zip"
MEMBER_NAMES = (
    "exh1.xls",
    "exh10.xls",
    "exh11.xls",
    "exh12.xls",
    "exh13.xls",
    "exh14.xls",
    "exh14a.xls",
    "exh15.xls",
    "exh16.xls",
    "exh16a.xls",
    "exh17.xls",
    "exh17a.xls",
    "exh18.xls",
    "exh19.xls",
    "exh1s.xls",
    "exh2.xls",
    "exh20.xls",
    "exh20a.xls",
    "exh20b.xls",
    "exh2as.xls",
    "exh2s.xls",
    "exh3.xls",
    "exh3s.xls",
    "exh4.xls",
    "exh4as.xls",
    "exh4s.xls",
    "exh5.xls",
    "exh6.xls",
    "exh7.xls",
    "exh8.xls",
    "exh9.xls",
)
DIMENSION_COUNTS = (
    (612.0, 792.0, 35),
    (874.29, 1131.43, 1),
    (886.96, 1147.83, 2),
    (1037.29, 1342.37, 2),
    (1055.17, 1365.52, 2),
    (1092.86, 1414.29, 2),
    (1112.73, 1440.0, 2),
    (1133.33, 1466.67, 4),
    (1154.72, 1494.34, 3),
    (1176.92, 1523.08, 1),
    (1200.0, 1552.94, 2),
    (1224.0, 1584.0, 4),
    (1248.98, 1616.33, 1),
    (1302.13, 1685.11, 1),
    (1330.43, 1721.74, 1),
)


def _dimensions() -> list[tuple[float, float]]:
    return [(width, height) for width, height, count in DIMENSION_COUNTS for _ in range(count)]


def pdf_bytes(
    *,
    replacements: dict[str, str] | None = None,
    dimensions: list[tuple[float, float]] | None = None,
    blank_page: int | None = None,
    metadata_replacements: dict[str, str] | None = None,
    include_covid_marker: bool = False,
    rotated_page: int | None = None,
) -> bytes:
    lines = [
        "FOR RELEASE AT 8:30 AM EDT, THURSDAY, APRIL 2, 2020",
        "MONTHLY U.S. INTERNATIONAL TRADE IN GOODS AND SERVICES, FEBRUARY 2020",
        "Release Number: CB 20-52, BEA 20-16",
        (
            "the goods and services deficit was $39.9 billion in February, down "
            "$5.5 billion from $45.5 billion in January, revised"
        ),
        "Statistical significance is not applicable or not measurable",
        "Data adjusted for seasonality but not price changes",
        "Revisions to January exports",
        "Revisions to January imports",
        "January data as published last month:",
        "Monthly Revisions",
        "Annual Revisions",
        (
            "The goods data are a complete enumeration of documents collected by CBP and are "
            "not subject to sampling errors"
        ),
    ]
    if include_covid_marker:
        lines.append("determined estimates in this release meet publication standards")
    if replacements:
        lines = [_replace_all(line, replacements) for line in lines]
    writer = PdfWriter()
    observed_dimensions = dimensions if dimensions is not None else _dimensions()
    for index, (width, height) in enumerate(observed_dimensions):
        writer.add_blank_page(width=width, height=height)
        if index != blank_page:
            _write_page_text(
                writer,
                index,
                lines if index == 0 else [f"FT-900 evidence page {index + 1}"],
            )
        if index == rotated_page:
            writer.pages[index].rotate(90)
    metadata = {
        "/Author": "kebed001",
        "/CreationDate": "D:20200401132145-04'00'",
        "/Creator": "Adobe Acrobat Pro 2017 17.11.30158",
        "/ModDate": "D:20200401132145-04'00'",
        "/Producer": "Adobe Acrobat Pro 2017 17.11.30158",
        "/Title": "",
    }
    if metadata_replacements:
        metadata.update(metadata_replacements)
    writer.add_metadata(metadata)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class _FakeBook:
    def __init__(self, *, current_balance: float = -39_932.0, numeric_override: object = None):
        self.sheet = _FakeSheet(
            current_balance=current_balance,
            numeric_override=numeric_override,
        )
        self.released = False

    def sheet_names(self) -> list[str]:
        return [self.sheet.name]

    def sheet_by_name(self, name: str) -> _FakeSheet:
        if name != self.sheet.name:
            raise KeyError(name)
        return self.sheet

    def release_resources(self) -> None:
        self.released = True


class _FakeSheet:
    name = "1"
    nrows = 55
    ncols = 10

    def __init__(self, *, current_balance: float, numeric_override: object):
        current = (
            current_balance,
            -61_212.0,
            21_280.0,
            207_543.0,
            137_203.0,
            70_341.0,
            247_476.0,
            198_415.0,
            49_061.0,
        )
        prior = (
            -45_482.0,
            -67_122.0,
            21_640.0,
            208_307.0,
            136_251.0,
            72_056.0,
            253_790.0,
            203_374.0,
            50_416.0,
        )
        previous = (
            -45_338.0,
            -67_005.0,
            21_668.0,
            208_569.0,
            136_374.0,
            72_195.0,
            253_906.0,
            203_380.0,
            50_527.0,
        )
        self.cells: dict[tuple[int, int], object] = {
            (1, 0): "Part A: Seasonally Adjusted (by Commodity/Service)",
            (2, 0): "Exhibit 1. U.S. International Trade in Goods and Services",
            (4, 0): "Period",
            (4, 1): "Balance",
            (4, 4): "Exports",
            (4, 7): "Imports",
            (5, 1): "Total",
            (5, 2): "Goods (1)",
            (5, 3): "Services",
            (36, 0): "2020",
            (38, 0): "January (R)",
            (39, 0): "February",
            (50, 0): "January data as published last month:",
        }
        for column, value in enumerate(prior, start=1):
            self.cells[(38, column)] = value
        for column, value in enumerate(current, start=1):
            self.cells[(39, column)] = value
        for column, value in enumerate(previous, start=1):
            self.cells[(51, column)] = value
        if numeric_override is not None:
            self.cells[(39, 1)] = numeric_override

    def cell_value(self, row: int, column: int) -> object:
        return self.cells.get((row, column), "")


_FAKE_BOOKS: dict[bytes, _FakeBook] = {}


def _open_fake_workbook(*, file_contents: bytes, on_demand: bool) -> _FakeBook:
    assert on_demand is True
    return _FAKE_BOOKS[file_contents]


def xls_zip_bytes(
    *,
    current_balance: float = -39_932.0,
    member_names: tuple[str, ...] = MEMBER_NAMES,
    numeric_override: object = None,
    oversized_member: bool = False,
    aggregate_oversized: bool = False,
) -> bytes:
    xls_content = (
        bytes.fromhex("d0cf11e0a1b11ae1")
        + (f"TEST-FT900-XLS:{current_balance}:{numeric_override!r}").encode()
    )
    _FAKE_BOOKS[xls_content] = _FakeBook(
        current_balance=current_balance,
        numeric_override=numeric_override,
    )
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name in member_names:
            if name == "exh1.xls":
                value = xls_content
            elif oversized_member and name == "exh20b.xls":
                value = b"x" * 2_000_001
            elif aggregate_oversized:
                value = b"x" * 400_000
            else:
                value = b""
            archive.writestr(name, value)
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
    commands = ["BT", "/F1 7 Tf", "24 760 Td", "9 TL"]
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
    monkeypatch: pytest.MonkeyPatch,
    *,
    pdf_content: bytes | None = None,
    zip_content: bytes | None = None,
    pdf_content_type: str = "application/pdf",
    zip_content_type: str = "application/zip",
) -> CensusBEAFT900ArchiveAdapter:
    workbook_zip = zip_content if zip_content is not None else xls_zip_bytes()
    monkeypatch.setattr(xlrd, "open_workbook", _open_fake_workbook)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".pdf"):
            return response(
                request,
                pdf_content if pdf_content is not None else pdf_bytes(),
                content_type=pdf_content_type,
            )
        return response(request, workbook_zip, content_type=zip_content_type)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return CensusBEAFT900ArchiveAdapter(safe, release_date=RELEASE_DATE)


def test_ft900_pair_is_exact_versioned_and_knowledge_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_content = pdf_bytes()
    zip_content = xls_zip_bytes()
    batch = adapter(
        monkeypatch,
        pdf_content=pdf_content,
        zip_content=zip_content,
    ).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("202002:goods_services_deficit_level")
    assert record.entity_id == "census_bea_ft900:us_goods_services_deficit"
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.vintage_as_of == datetime(2020, 4, 2, 12, 30, tzinfo=UTC)
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.sha256 == hashlib.sha256(pdf_content).hexdigest()
    assert record.interval.valid_from == datetime(2020, 2, 1, tzinfo=UTC)
    assert record.interval.published_at == datetime(2020, 4, 2, 12, 30, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 4, 2, 12, 30, tzinfo=UTC)
    assert record.interval.availability_confidence == 1.0
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_million_dollars"] == 39_932
    assert record.payload["signed_balance_million_dollars"] == -39_932
    assert record.payload["prior_month_revised_deficit_million_dollars"] == 45_482
    assert record.payload["prior_month_previous_release_deficit_million_dollars"] == 45_338
    assert record.payload["prior_month_revision_delta_million_dollars"] == 144
    assert record.payload["release_snapshot_deficit_million_dollars"] == {
        "2020-01": 45_482,
        "2020-02": 39_932,
    }
    assert record.payload["official_release_at"] == "2020-04-02T12:30:00+00:00"
    assert record.payload["pdf_xls_crosscheck_verified"] is True
    assert record.payload["headline_statistical_significance_applicable_or_measurable"] is False
    assert record.payload["release_pdf_sha256"] == hashlib.sha256(pdf_content).hexdigest()
    assert record.payload["release_xls_zip_sha256"] == hashlib.sha256(zip_content).hexdigest()
    assert record.payload["release_xls_zip_member_count"] == 31
    assert record.payload["release_xls_zip_member_names"] == list(MEMBER_NAMES)
    assert [receipt.record_count for receipt in batch.receipts] == [1, 0]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert len(batch.artifacts) == 2
    assert {artifact.content for artifact in batch.artifacts} == {pdf_content, zip_content}

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 4, 2, 12, 29, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 4, 2, 12, 30, tzinfo=UTC)) == [record]


def test_verified_calendar_and_fixed_urls_reject_unapproved_dates() -> None:
    client = cast(SafeHttpClient, object())
    january = CensusBEAFT900ArchiveAdapter(client, release_date=date(2020, 3, 6))
    february = CensusBEAFT900ArchiveAdapter(client, release_date=RELEASE_DATE)
    march = CensusBEAFT900ArchiveAdapter(client, release_date=date(2020, 5, 5))

    assert january.spec.current_deficit_million == 45_338
    assert january.spec.timezone_abbreviation == "EST"
    assert february.pdf_endpoint == PDF_URL
    assert february.xls_zip_endpoint == ZIP_URL
    assert february.spec.prior_deficit_million == 45_482
    assert march.spec.current_deficit_million == 44_415
    assert march.spec.previous_prior_deficit_million == 39_932
    assert march.spec.covid_publication_statement is True
    with pytest.raises(ValueError, match="verified Census/BEA FT-900 calendar"):
        CensusBEAFT900ArchiveAdapter(client, release_date=date(2020, 6, 4))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"pdf_content_type": "text/html"}, "PDF content type"),
        ({"zip_content_type": "text/plain"}, "ZIP content type"),
        ({"pdf_content": b"not-pdf"}, "not a PDF"),
        ({"pdf_content": pdf_bytes(dimensions=_dimensions()[:-1])}, "page count"),
        ({"pdf_content": pdf_bytes(blank_page=5)}, "blank text layer"),
        ({"pdf_content": pdf_bytes(rotated_page=5)}, "page rotations"),
        (
            {"pdf_content": pdf_bytes(dimensions=[(613.0, 792.0), *_dimensions()[1:]])},
            "dimensions",
        ),
        (
            {"pdf_content": pdf_bytes(metadata_replacements={"/Author": "unknown"})},
            "metadata",
        ),
        (
            {"pdf_content": pdf_bytes(replacements={"THURSDAY": "FRIDAY"})},
            "release-time identity",
        ),
        (
            {"pdf_content": pdf_bytes(replacements={"$39.9": "$40.0"})},
            "headline, revision, or methodology",
        ),
        (
            {"pdf_content": pdf_bytes(include_covid_marker=True)},
            "COVID-19 publication statement",
        ),
        ({"zip_content": b"not-zip"}, "not a ZIP"),
        (
            {"zip_content": xls_zip_bytes(member_names=MEMBER_NAMES[:-1])},
            "member inventory",
        ),
        (
            {"zip_content": xls_zip_bytes(oversized_member=True)},
            "member is too large",
        ),
        (
            {"zip_content": xls_zip_bytes(aggregate_oversized=True)},
            "expands beyond the limit",
        ),
        (
            {"zip_content": xls_zip_bytes(current_balance=-39_931.0)},
            "do not cross-check",
        ),
        (
            {"zip_content": xls_zip_bytes(numeric_override="not-numeric")},
            "workbook cell is not numeric",
        ),
        (
            {"zip_content": xls_zip_bytes(numeric_override=1.5)},
            "workbook cell is not a whole number",
        ),
    ],
)
def test_ft900_rejects_schema_and_crosscheck_failures(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(monkeypatch, **kwargs).fetch()  # type: ignore[arg-type]


def test_ft900_rejects_corrupt_zip_and_legacy_workbook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = adapter(monkeypatch)
    with pytest.raises(SourceSchemaError, match="ZIP could not be parsed"):
        instance._parse_xls_zip(b"PK\x03\x04corrupt")

    bad_xls = b"not-an-ole-workbook"
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name in MEMBER_NAMES:
            archive.writestr(name, bad_xls if name == "exh1.xls" else b"")
    with pytest.raises(SourceSchemaError, match="not a legacy OLE XLS"):
        instance._parse_xls_zip(output.getvalue())


def test_ft900_rejects_wrong_response_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = adapter(monkeypatch)
    with pytest.raises(SourceSchemaError, match="PDF response URL"):
        instance._validate_response_url(
            "https://www.census.gov/foreign-trade/Press-Release/ft900/ft900_2003.pdf",
            kind="pdf",
        )
    with pytest.raises(SourceSchemaError, match="ZIP response URL"):
        instance._validate_response_url(f"{ZIP_URL}?download=1", kind="zip")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {
                "current_row": (
                    -39_932,
                    -61_212,
                    21_280,
                    207_543,
                    137_100,
                    70_341,
                    247_476,
                    198_415,
                    49_061,
                )
            },
            "export components",
        ),
        (
            {
                "current_row": (
                    -39_932,
                    -61_212,
                    21_280,
                    207_543,
                    137_203,
                    70_341,
                    247_476,
                    198_000,
                    49_061,
                )
            },
            "import components",
        ),
        (
            {
                "current_row": (
                    -39_930,
                    -61_210,
                    21_280,
                    207_543,
                    137_203,
                    70_341,
                    247_476,
                    198_415,
                    49_061,
                )
            },
            "total balance arithmetic",
        ),
        (
            {
                "current_row": (
                    -39_932,
                    -61_200,
                    21_280,
                    207_543,
                    137_203,
                    70_341,
                    247_476,
                    198_415,
                    49_061,
                )
            },
            "balance components",
        ),
        ({"headline_deficit_billion": "40.0"}, "current headline"),
        ({"headline_prior_deficit_billion": "45.4"}, "prior headline"),
        ({"headline_direction": "up"}, "headline direction"),
    ],
)
def test_ft900_internal_arithmetic_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, object],
    message: str,
) -> None:
    instance = adapter(monkeypatch)
    instance.spec = replace(instance.spec, **changes)  # type: ignore[arg-type]
    facts: dict[str, object] = {
        "current_row": instance.spec.current_row,
        "prior_row_current_release": instance.spec.prior_row_current_release,
        "prior_row_previous_release": instance.spec.prior_row_previous_release,
        "snapshot_deficits": instance.spec.snapshot_deficits,
    }
    with pytest.raises(SourceSchemaError, match=message):
        instance._crosscheck_workbook(facts)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("sheet", "sheet identity"),
        ("dimensions", "dimensions"),
        ("header", "labels"),
        ("current", "current-month row"),
        ("prior", "prior-month row"),
    ],
)
def test_ft900_rejects_legacy_workbook_structure(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    zip_content = xls_zip_bytes()
    with ZipFile(BytesIO(zip_content)) as archive:
        exhibit1 = archive.read("exh1.xls")
    sheet = _FAKE_BOOKS[exhibit1].sheet
    if mutation == "sheet":
        sheet.name = "wrong"
    elif mutation == "dimensions":
        sheet.nrows = 54
    elif mutation == "header":
        sheet.cells[(2, 0)] = "wrong"
    elif mutation == "current":
        sheet.cells[(39, 0)] = "March"
    else:
        sheet.cells[(38, 0)] = "January"
    monkeypatch.setattr(xlrd, "open_workbook", _open_fake_workbook)
    instance = CensusBEAFT900ArchiveAdapter(
        cast(SafeHttpClient, object()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match=message):
        instance._parse_xls_zip(zip_content)


def test_ft900_wraps_pdf_and_xls_parser_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = adapter(monkeypatch)

    def fail_pdf(*_: object, **__: object) -> object:
        raise ValueError("bad pdf")

    monkeypatch.setattr(ft900_module, "PdfReader", fail_pdf)
    with pytest.raises(SourceSchemaError, match="PDF could not be parsed"):
        instance._parse_pdf(b"%PDF-corrupt")

    def fail_xls(*_: object, **__: object) -> object:
        raise ValueError("bad xls")

    monkeypatch.setattr(xlrd, "open_workbook", fail_xls)
    with pytest.raises(SourceSchemaError, match="legacy XLS could not be parsed"):
        instance._parse_xls(bytes.fromhex("d0cf11e0a1b11ae1") + b"broken")


class _TemporalHTTP:
    def __init__(self, retrieved_at: datetime):
        self.retrieved_at = retrieved_at

    def get(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
    ) -> tuple[HttpResponseSnapshot, bytes, datetime]:
        assert allowed_hosts == ("www.census.gov",)
        content_type = "application/pdf" if url.endswith(".pdf") else "application/zip"
        content = b"stub"
        return (
            HttpResponseSnapshot(
                status_code=200,
                headers=httpx.Headers({"Content-Type": content_type}),
                request_url=url,
                content=content,
            ),
            content,
            self.retrieved_at,
        )


@pytest.mark.parametrize(
    ("timezone", "retrieved_at", "message"),
    [
        ("EST", datetime(2020, 4, 2, 12, 30, tzinfo=UTC), "timezone"),
        ("EDT", datetime(2020, 4, 2, 12, 29, tzinfo=UTC), "not yet knowable"),
    ],
)
def test_ft900_fails_closed_on_timezone_and_pre_release_time(
    monkeypatch: pytest.MonkeyPatch,
    timezone: str,
    retrieved_at: datetime,
    message: str,
) -> None:
    instance = CensusBEAFT900ArchiveAdapter(
        cast(SafeHttpClient, _TemporalHTTP(retrieved_at)),
        release_date=RELEASE_DATE,
    )
    instance.spec = replace(instance.spec, timezone_abbreviation=timezone)
    facts = {
        "current_row": instance.spec.current_row,
        "prior_row_current_release": instance.spec.prior_row_current_release,
        "prior_row_previous_release": instance.spec.prior_row_previous_release,
        "snapshot_deficits": instance.spec.snapshot_deficits,
    }
    monkeypatch.setattr(instance, "_parse_pdf", lambda _: None)
    monkeypatch.setattr(
        instance,
        "_parse_xls_zip",
        lambda _: (facts, bytes.fromhex("d0cf11e0a1b11ae1"), {}),
    )
    with pytest.raises(SourceSchemaError, match=message):
        instance.fetch()


def test_ft900_accepts_octet_stream_zip_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = adapter(monkeypatch, zip_content_type="application/octet-stream").fetch()
    assert batch.receipts[1].content_type == "application/octet-stream"


def test_fake_book_resources_are_released(monkeypatch: pytest.MonkeyPatch) -> None:
    zip_content = xls_zip_bytes()
    monkeypatch.setattr(xlrd, "open_workbook", _open_fake_workbook)
    instance = CensusBEAFT900ArchiveAdapter(
        cast(SafeHttpClient, object()),
        release_date=RELEASE_DATE,
    )
    _, exhibit1, _ = instance._parse_xls_zip(zip_content)
    assert _FAKE_BOOKS[exhibit1].released is True


def test_response_helper_retains_status_and_content() -> None:
    request = httpx.Request("GET", PDF_URL)
    result = response(request, b"abc", content_type="application/pdf")
    assert result.status_code == 200
    assert result.content == b"abc"


def test_fake_sheet_default_is_empty() -> None:
    sheet = _FakeSheet(current_balance=-39_932.0, numeric_override=None)
    assert sheet.cell_value(0, 0) == ""


def test_fake_book_rejects_unknown_sheet() -> None:
    book = _FakeBook()
    with pytest.raises(KeyError):
        book.sheet_by_name("missing")


def test_replace_all_supports_multiple_replacements() -> None:
    assert _replace_all("alpha beta", {"alpha": "a", "beta": "b"}) == "a b"


def test_type_contract_for_payload_is_json_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = adapter(monkeypatch).fetch().records[0].payload
    assert isinstance(payload, dict)
    assert all(isinstance(key, str) for key in payload)
