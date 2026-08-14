from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from io import BytesIO
from typing import cast
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import CensusC30ArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 4, 1)
PDF_URL = "https://www.census.gov/construction/c30/pdf/pr202002.pdf"
XLSX_URL = "https://www.census.gov/construction/c30/xls/pr202002.xlsx"


def _instance(release_date: date) -> CensusC30ArchiveAdapter:
    return CensusC30ArchiveAdapter(cast(SafeHttpClient, object()), release_date=release_date)


def pdf_bytes(
    *,
    release_date: date = RELEASE_DATE,
    replacements: dict[str, str] | None = None,
    pages: int = 6,
    width: int = 612,
    height: int = 792,
    rotation: int = 0,
    blank_page: int | None = None,
) -> bytes:
    spec = _instance(release_date).spec
    direction = "above" if float(spec.monthly_change_percent) > 0 else "below"
    notes = [
        "EXPLANATORY NOTES",
        "subject to sampling variability as well as nonsampling error",
        "All ranges given are 90 percent confidence intervals",
        "average absolute percent changes from preliminary estimate to first revision",
        "Data are at an annual rate, adjusted for seasonality but not price changes",
    ]
    if spec.annual_revision_notice_present:
        notes.append(
            "With the May 2020 release, unadjusted data will be revised back to January 2018"
        )
    if spec.covid_publication_standard_statement_present:
        notes.append("determined estimates in this release meet publication standards")
    if spec.future_imputation_revision_notice_present:
        notes.append("will be revised to reflect changes made to the imputation methodology")
    first_page = [
        (
            f"FOR RELEASE AT 10:00 AM {spec.timezone_abbreviation}, "
            f"{release_date:%A, %B} {release_date.day}, {release_date:%Y}"
        ),
        f"MONTHLY CONSTRUCTION SPENDING, {spec.reference_label.upper()}",
        f"Release Number: {spec.release_number}",
        (
            f"{spec.reference_label.upper()} "
            f"${spec.current_total_million / 1000:.1f} billion"
        ),
        (
            f"{abs(float(spec.monthly_change_percent)):.1f} percent "
            f"(±{spec.monthly_margin_percent} percent) {direction}"
        ),
        (
            f"{abs(float(spec.year_over_year_change_percent)):.1f} percent "
            f"(±{spec.year_over_year_margin_percent} percent)"
        ),
    ]
    table1 = [
        "Table 1. Value of Construction Put in Place in the United States",
        *(f"{value:,}" for _month, value in spec.snapshot_levels),
        f"{spec.current_private_million:,}",
        f"{spec.current_public_million:,}",
    ]
    table2 = [
        "Table 2. Value of Construction Put in Place in the United States",
        f"{spec.unadjusted_current_million:,}",
        f"{spec.year_to_date_current_million:,}",
        f"{spec.year_to_date_prior_year_million:,}",
    ]
    table3 = [
        "Table 3. Coefficients of Variation and Standard Errors",
        " ".join(
            (
                "Total Construction",
                spec.table3_total_monthly_estimate_cv_percent,
                spec.table3_total_year_to_date_estimate_cv_percent,
                spec.table3_total_year_to_date_change_standard_error_percent,
                spec.table3_total_month_to_month_change_standard_error_percent,
                spec.table3_total_month_to_month_prior_year_standard_error_percent,
            )
        ),
    ]
    if release_date == date(2020, 5, 1):
        page_lines = [
            first_page,
            notes,
            ["RESOURCES"],
            table1,
            table2,
            table3,
        ]
    else:
        assert spec.annual_total_current_million is not None
        assert spec.annual_total_prior_million is not None
        table4 = [
            "Table 4. Annual Value of Construction Put in Place in the United States",
            f"{spec.annual_total_current_million:,}",
            f"{spec.annual_total_prior_million:,}",
        ]
        page_lines = [first_page, notes, table1, table2, table3, table4]
    if replacements:
        page_lines = [
            [_replace_all(line, replacements) for line in lines] for lines in page_lines
        ]
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
    commands = ["BT", "/F1 6 Tf", "25 765 Td", "8 TL"]
    for line in lines:
        encoded = (
            line.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("±", "\\261")
        )
        commands.extend((f"({encoded}) Tj", "T*"))
    commands.append("ET")
    stream = StreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)


def _cells_for_release(release_date: date) -> dict[str, dict[str, tuple[str, str]]]:
    spec = _instance(release_date).spec
    table1: dict[str, tuple[str, str]] = {
        "A1": (
            "s",
            "Table 1. Value of Construction Put in Place in the United States, "
            "Seasonally Adjusted Annual Rate",
        ),
        "B5": ("s", f"{spec.reference_month:%b}\n{spec.reference_month:%Y}p"),
        "A7": ("s", "Total Construction"),
        "B7": ("n", str(spec.current_total_million)),
        "C7": ("n", str(spec.prior_total_million)),
        "H7": ("n", spec.monthly_change_percent),
        "I7": (
            "n",
            spec.year_over_year_change_percent.removesuffix(".0"),
        ),
        "A29": ("s", "Total Private Construction1"),
        "B29": ("n", str(spec.current_private_million)),
        "H29": ("n", spec.current_private_change_percent),
        "A48": ("s", "Total Public Construction2"),
        "B48": ("n", str(spec.current_public_million)),
        "H48": ("n", spec.current_public_change_percent),
    }
    for month, value in spec.snapshot_levels:
        reference = date.fromisoformat(f"{month}-01")
        difference = (
            (spec.reference_month.year - reference.year) * 12
            + spec.reference_month.month
            - reference.month
        )
        table1[f"{'BCD'[difference]}7"] = ("n", str(value))
    table2 = {
        "A1": (
            "s",
            "Table 2. Value of Construction Put in Place in the United States, "
            "Not Seasonally Adjusted",
        ),
        "B7": ("n", str(spec.unadjusted_current_million)),
        "H7": ("n", str(spec.year_to_date_current_million)),
        "I7": ("n", str(spec.year_to_date_prior_year_million)),
        "J7": (
            "n",
            (
                "8.1999999999999993"
                if release_date == date(2020, 4, 1)
                else spec.year_to_date_change_percent
            ),
        ),
    }
    table3 = {
        "A1": (
            "s",
            "Table 3. Coefficients of Variation and Standard Errors by Type of "
            "Construction",
        ),
        "B7": ("n", spec.table3_total_monthly_estimate_cv_percent),
        "C7": ("n", spec.table3_total_year_to_date_estimate_cv_percent),
        "D7": (
            "n",
            spec.table3_total_year_to_date_change_standard_error_percent,
        ),
        "E7": (
            "n",
            spec.table3_total_month_to_month_change_standard_error_percent,
        ),
        "F7": (
            "n",
            spec.table3_total_month_to_month_prior_year_standard_error_percent,
        ),
    }
    result = {"Table1": table1, "Table2": table2, "Table3": table3}
    if spec.annual_total_current_million is not None:
        assert spec.annual_total_prior_million is not None
        assert spec.annual_change_percent is not None
        assert spec.annual_cv_percent is not None
        result["Table4"] = {
            "A1": (
                "s",
                "Table 4. Annual Value of Construction Put in Place in the United States",
            ),
            "B6": ("n", str(spec.annual_total_current_million)),
            "C6": ("n", str(spec.annual_total_prior_million)),
            "D6": ("n", spec.annual_change_percent.removesuffix(".0")),
            "E6": ("n", spec.annual_cv_percent),
        }
    return result


def xlsx_bytes(*, release_date: date = RELEASE_DATE) -> bytes:
    spec = _instance(release_date).spec
    cells_by_sheet = _cells_for_release(release_date)
    shared_strings: list[str] = []
    for cells in cells_by_sheet.values():
        for kind, value in cells.values():
            if kind == "s" and value not in shared_strings:
                shared_strings.append(value)
    shared_index = {value: index for index, value in enumerate(shared_strings)}
    sheet_entries: dict[str, bytes] = {}
    relationships = []
    sheet_manifest = []
    content_overrides = []
    dimensions = dict(spec.workbook_dimensions)
    for index, sheet_name in enumerate(spec.workbook_sheet_names, start=1):
        path = f"xl/worksheets/sheet{index}.xml"
        sheet_entries[path] = _sheet_xml(
            dimensions[sheet_name],
            cells_by_sheet[sheet_name],
            shared_index,
        )
        relationships.append(
            f'<Relationship Id="rId{index}" '
            "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/"
            f'relationships/worksheet\" Target="worksheets/sheet{index}.xml"/>'
        )
        sheet_manifest.append(
            f'<sheet name="{sheet_name}" sheetId="{index}" r:id="rId{index}"/>'
        )
        content_overrides.append(
            f'<Override PartName="/{path}" '
            "ContentType=\"application/vnd.openxmlformats-officedocument."
            'spreadsheetml.worksheet+xml\"/>'
        )
    shared_id = len(spec.workbook_sheet_names) + 1
    relationships.append(
        f'<Relationship Id="rId{shared_id}" '
        "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/"
        'relationships/sharedStrings\" Target="sharedStrings.xml"/>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{''.join(sheet_manifest)}</sheets></workbook>"
    ).encode()
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(relationships)}</Relationships>"
    ).encode()
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(
            f'<si><t xml:space="preserve">{escape(value)}</t></si>'
            for value in shared_strings
        )
        + "</sst>"
    ).encode()
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f"{''.join(content_overrides)}"
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    ).encode()
    package_rels = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" '
        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        b'officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )
    entries = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": package_rels,
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": workbook_rels,
        "xl/sharedStrings.xml": shared,
        "xl/styles.xml": b'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
        "xl/theme/theme1.xml": b'<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>',
        **sheet_entries,
    }
    return _zip_entries(entries)


def _sheet_xml(
    dimension: str,
    cells: dict[str, tuple[str, str]],
    shared_index: dict[str, int],
) -> bytes:
    rows: dict[int, list[str]] = {}
    for reference, (kind, value) in cells.items():
        row = int(re.search(r"\d+$", reference).group())  # type: ignore[union-attr]
        encoded = str(shared_index[value]) if kind == "s" else escape(value)
        rows.setdefault(row, []).append(
            f'<c r="{reference}" t="{kind}"><v>{encoded}</v></c>'
        )
    row_xml = "".join(
        f'<row r="{row}">{"".join(values)}</row>' for row, values in sorted(rows.items())
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/><sheetData>{row_xml}</sheetData></worksheet>'
    ).encode()


def _zip_entries(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return output.getvalue()


def mutate_xlsx(
    content: bytes,
    *,
    path: str | None = None,
    old: bytes | None = None,
    new: bytes | None = None,
    drop: str | None = None,
    extra: tuple[str, bytes] | None = None,
) -> bytes:
    with ZipFile(BytesIO(content)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist() if name != drop}
    if path is not None:
        assert old is not None
        assert new is not None
        assert old in entries[path]
        entries[path] = entries[path].replace(old, new, 1)
    if extra is not None:
        entries[extra[0]] = extra[1]
    return _zip_entries(entries)


def adapter(
    *,
    release_date: date = RELEASE_DATE,
    pdf_content: bytes | None = None,
    xlsx_content: bytes | None = None,
    pdf_content_type: str = "application/pdf",
    xlsx_content_type: str = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
) -> CensusC30ArchiveAdapter:
    selected_pdf = pdf_content if pdf_content is not None else pdf_bytes(release_date=release_date)
    selected_xlsx = (
        xlsx_content if xlsx_content is not None else xlsx_bytes(release_date=release_date)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".pdf"):
            return httpx.Response(
                200,
                content=selected_pdf,
                headers={"Content-Type": pdf_content_type},
                request=request,
            )
        return httpx.Response(
            200,
            content=selected_xlsx,
            headers={"Content-Type": xlsx_content_type},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return CensusC30ArchiveAdapter(safe, release_date=release_date)


def test_c30_pair_is_exact_versioned_cross_checked_and_knowledge_safe() -> None:
    pdf_content = pdf_bytes()
    xlsx_content = xlsx_bytes()
    batch = adapter(pdf_content=pdf_content, xlsx_content=xlsx_content).fetch()

    assert len(batch.records) == 2
    january, february = batch.records
    assert january.record_id.endswith("202001:total_construction_saar_level")
    assert february.record_id.endswith("202002:total_construction_saar_level")
    assert january.entity_id == "census_c30:total_construction_value_put_in_place"
    assert january.source.sha256 == hashlib.sha256(pdf_content).hexdigest()
    assert january.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert january.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert january.source.vintage_as_of == datetime(2020, 4, 1, 14, 0, tzinfo=UTC)
    assert january.interval.valid_from == datetime(2020, 1, 1, tzinfo=UTC)
    assert january.interval.available_at == datetime(2020, 4, 1, 14, 0, tzinfo=UTC)
    assert january.interval.revised_at == datetime(2020, 4, 1, 14, 0, tzinfo=UTC)
    assert february.interval.revised_at is None
    assert january.evidence_class is EvidenceClass.REPORTED
    assert january.payload["value_million_dollars"] == 1_384_486
    assert january.payload["revision_delta_million_dollars"] == 15_263
    assert february.payload["value_million_dollars"] == 1_366_697
    assert february.payload["reported_current_month_change_percent"] == "-1.3"
    assert february.payload["reported_current_month_total_saar_billion_dollars"] == "1366.7"
    assert february.payload["table2_year_to_date_change_percent"] == "8.2"
    assert february.payload["table3_total_monthly_estimate_cv_percent"] == "0.7"
    assert february.payload["table3_total_month_to_month_change_standard_error_percent"] == "0.5"
    assert february.payload["data_adjusted_seasonally_but_not_for_price_changes"] is True
    assert february.payload["pdf_xlsx_crosscheck_verified"] is True
    assert february.payload["release_xlsx_sha256"] == hashlib.sha256(xlsx_content).hexdigest()
    assert february.payload_schema_version == "1.1.0"
    assert [receipt.record_count for receipt in batch.receipts] == [2, 0]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert {artifact.content for artifact in batch.artifacts} == {pdf_content, xlsx_content}

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 4, 1, 13, 59, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 4, 1, 14, 0, tzinfo=UTC)) == [
            january,
            february,
        ]


def test_three_releases_preserve_all_six_versions_and_asof_views() -> None:
    releases = (date(2020, 3, 2), date(2020, 4, 1), date(2020, 5, 1))
    batches = [adapter(release_date=item).fetch() for item in releases]
    records = tuple(record for batch in batches for record in batch.records)
    assert len(records) == 6
    with TimeVault() as vault:
        receipt = vault.append(records)
        assert receipt.inserted_records == 6
        assert vault.records_as_of(datetime(2020, 3, 2, 14, 59, 59, tzinfo=UTC)) == []
        march_view = vault.records_as_of(datetime(2020, 3, 2, 15, 0, tzinfo=UTC))
        assert [record.payload["value_million_dollars"] for record in march_view] == [
            1_369_223
        ]
        april_view = vault.records_as_of(datetime(2020, 4, 1, 14, 0, tzinfo=UTC))
        assert [record.payload["value_million_dollars"] for record in april_view] == [
            1_384_486,
            1_366_697,
        ]
        may_view = vault.records_as_of(datetime(2020, 5, 1, 14, 0, tzinfo=UTC))
        assert [record.payload["value_million_dollars"] for record in may_view] == [
            1_382_963,
            1_348_386,
            1_360_512,
        ]
        assert may_view[-1].payload["release_snapshot_revision_delta_million_dollars"] == {
            "2020-01": -1_523,
            "2020-02": -18_311,
            "2020-03": None,
        }
        january_id = march_view[0].record_id
        assert [
            item.payload["value_million_dollars"] for item in vault.history(january_id)
        ] == [1_369_223, 1_384_486, 1_382_963]


@pytest.mark.parametrize(
    ("release_date", "record_count", "available_at", "values", "statuses"),
    [
        (
            date(2020, 3, 2),
            1,
            datetime(2020, 3, 2, 15, 0, tzinfo=UTC),
            [1_369_223],
            ["preliminary"],
        ),
        (
            date(2020, 4, 1),
            2,
            datetime(2020, 4, 1, 14, 0, tzinfo=UTC),
            [1_384_486, 1_366_697],
            ["revised", "preliminary"],
        ),
        (
            date(2020, 5, 1),
            3,
            datetime(2020, 5, 1, 14, 0, tzinfo=UTC),
            [1_382_963, 1_348_386, 1_360_512],
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
    assert [record.payload["value_million_dollars"] for record in records] == values
    assert [record.payload["estimate_status"] for record in records] == statuses


def test_verified_calendar_and_response_urls_are_closed() -> None:
    client = cast(SafeHttpClient, object())
    expected = {
        date(2020, 3, 2): "202001",
        date(2020, 4, 1): "202002",
        date(2020, 5, 1): "202003",
    }
    for release_date, suffix in expected.items():
        item = CensusC30ArchiveAdapter(client, release_date=release_date)
        assert item.pdf_endpoint.endswith(f"/pdf/pr{suffix}.pdf")
        assert item.xlsx_endpoint.endswith(f"/xls/pr{suffix}.xlsx")
    with pytest.raises(ValueError, match="verified Census C30 calendar"):
        CensusC30ArchiveAdapter(client, release_date=date(2020, 6, 1))
    item = CensusC30ArchiveAdapter(client, release_date=RELEASE_DATE)
    for invalid in (
        PDF_URL.replace("https://", "http://"),
        PDF_URL.replace("www.census.gov", "evil.example"),
        PDF_URL.replace("202002", "202003"),
        PDF_URL + "?download=1",
    ):
        with pytest.raises(SourceSchemaError, match="PDF response URL"):
            item._validate_response_url(invalid, kind="pdf")
    for invalid in (
        XLSX_URL.replace("https://", "http://"),
        XLSX_URL.replace("www.census.gov", "evil.example"),
        XLSX_URL.replace("202002", "202003"),
        XLSX_URL + "#download",
    ):
        with pytest.raises(SourceSchemaError, match="XLSX response URL"):
            item._validate_response_url(invalid, kind="xlsx")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"pdf_content_type": "text/html"}, "PDF content type"),
        ({"xlsx_content_type": "text/csv"}, "XLSX content type"),
        ({"pdf_content": b"not-pdf"}, "not a PDF"),
        ({"pdf_content": pdf_bytes(pages=5)}, "page count"),
        ({"pdf_content": pdf_bytes(blank_page=5)}, "blank text layer"),
        ({"pdf_content": pdf_bytes(width=613)}, "page geometry"),
        ({"pdf_content": pdf_bytes(rotation=90)}, "page geometry"),
        (
            {"pdf_content": pdf_bytes(replacements={"CB20-48": "CB20-49"})},
            "identity or headline",
        ),
        (
            {"pdf_content": pdf_bytes(replacements={"1,366,697": "1,366,698"})},
            "Table 1 values",
        ),
        (
            {
                "pdf_content": pdf_bytes(
                    replacements={
                        "subject to sampling variability as well as nonsampling error": "missing"
                    }
                )
            },
            "methodology markers",
        ),
        (
            {
                "pdf_content": pdf_bytes(
                    replacements={
                        "With the May 2020 release": "With a later release"
                    }
                )
            },
            "annual-revision notice",
        ),
    ],
)
def test_c30_rejects_pdf_http_and_schema_failures(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(**kwargs).fetch()  # type: ignore[arg-type]


_VALID_XLSX = xlsx_bytes()
_WORKBOOK_RELS = "xl/_rels/workbook.xml.rels"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-xlsx", "not an XLSX"),
        (mutate_xlsx(_VALID_XLSX, drop="xl/sharedStrings.xml"), "core files"),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path="[Content_Types].xml",
                old=b"spreadsheetml.sheet.main+xml",
                new=b"spreadsheetml.template.main+xml",
            ),
            "core content types",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path="_rels/.rels",
                old=b'Target="xl/workbook.xml"',
                new=b'Target="xl/other.xml"',
            ),
            "workbook package relationship",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path=_WORKBOOK_RELS,
                old=b'Target="worksheets/sheet1.xml"',
                new=b'Target="worksheets/sheet1.xml" TargetMode="External"',
            ),
            "external relationship",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path=_WORKBOOK_RELS,
                old=b"relationships/worksheet",
                new=b"relationships/chartsheet",
            ),
            "relationship type",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path=_WORKBOOK_RELS,
                old=b'Target="worksheets/sheet1.xml"',
                new=b'Target="../evil.xml"',
            ),
            "target is unsafe",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path="xl/workbook.xml",
                old=b'name="Table1"',
                new=b'name="Broken"',
            ),
            "sheet identity",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path="xl/worksheets/sheet1.xml",
                old=b'ref="A1:I76"',
                new=b'ref="A1:I75"',
            ),
            "dimensions",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path="xl/worksheets/sheet1.xml",
                old=b't="s"><v>0</v>',
                new=b't="s"><v>9999</v>',
            ),
            "shared-string index",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path="xl/worksheets/sheet1.xml",
                old=b't="s"><v>0</v>',
                new=b't="b"><v>0</v>',
            ),
            "cell type is unsupported",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path="xl/worksheets/sheet2.xml",
                old=b">8.1999999999999993<",
                new=b">8.14<",
            ),
            "unsupported precision",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path="xl/worksheets/sheet1.xml",
                old=b">1366697<",
                new=b">1366698<",
            ),
            "do not cross-check",
        ),
        (
            mutate_xlsx(
                _VALID_XLSX,
                path="xl/worksheets/sheet1.xml",
                old=b'<c r="B7" t="n"><v>1366697</v></c>',
                new=(
                    b'<c r="B7" t="n"><v>1366697</v></c>'
                    b'<c r="B7" t="n"><v>1366697</v></c>'
                ),
            ),
            "cell reference is duplicated",
        ),
        (
            mutate_xlsx(_VALID_XLSX, extra=("../escape.xml", b"unsafe")),
            "unsafe entry",
        ),
    ],
)
def test_c30_rejects_xlsx_schema_security_and_crosscheck_failures(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(xlsx_content=content).fetch()


def test_c30_accepts_the_three_documented_xlsx_content_types() -> None:
    for content_type in (
        "application/octet-stream",
        "application/zip",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ):
        assert len(adapter(xlsx_content_type=content_type).fetch().records) == 2
