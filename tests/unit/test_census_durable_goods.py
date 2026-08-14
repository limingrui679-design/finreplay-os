from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import CensusDurableGoodsArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 3, 25)
SAMPLING_MARKER = (
    "The Manufacturers' Shipments, Inventories, and Orders estimates are not based on a "
    "probability sample, so the sampling error of these estimates cannot be measured nor can "
    "the confidence intervals be computed."
)
COVID_MARKER = "determined estimates in this release meet publication standards"
RELEASES: dict[date, dict[str, Any]] = {
    date(2020, 2, 27): {
        "reference": "January",
        "reference_key": "2020-01",
        "prior": "December",
        "weekday": "THURSDAY",
        "timezone": "EST",
        "release_number": "CB 20-31",
        "release_code": "M3-1 (20)-01",
        "direction": "decreased",
        "delta_billion": "0.4",
        "value_billion": "246.2",
        "value_million": 246_199,
        "change": "-0.2",
        "change_basis_points": -20,
        "prior_change": "2.9",
        "prior_change_basis_points": 290,
        "prior_value_million": 246_634,
        "older_change": "-3.1",
        "older_value_million": 239_718,
        "exclude_transport_direction": "increased",
        "exclude_transport_change": "0.9",
        "exclude_defense_direction": "increased",
        "exclude_defense_change": "3.6",
        "shipments_value": 250_098,
        "shipments_change": "-0.2",
        "unfilled_value": 1_157_012,
        "unfilled_change": "0.0",
        "inventories_value": 435_379,
        "inventories_change": "0.0",
        "next_reference": "February",
        "next_release": date(2020, 3, 25),
        "next_timezone": "EST",
        "full_release": date(2020, 3, 5),
        "full_timezone": "EST",
        "creation": "D:20200226105721-05'00'",
        "modification": "D:20200324112228-04'00'",
        "dimensions": (
            *((612.0, 792.0),) * 5,
            (1492.68, 1931.71),
            (1423.26, 1841.86),
        ),
        "snapshots": {"2020-01": -20},
        "previous": {"2020-01": None},
        "revisions": {"2020-01": None},
        "level_snapshots": {"2020-01": 246_199},
        "level_previous": {"2020-01": None},
        "level_revisions": {"2020-01": None},
        "covid": False,
    },
    date(2020, 3, 25): {
        "reference": "February",
        "reference_key": "2020-02",
        "prior": "January",
        "weekday": "WEDNESDAY",
        "timezone": "EDT",
        "release_number": "CB 20-47",
        "release_code": "M3-1 (20)-02",
        "direction": "increased",
        "delta_billion": "2.9",
        "value_billion": "249.4",
        "value_million": 249_409,
        "change": "1.2",
        "change_basis_points": 120,
        "prior_change": "0.1",
        "prior_change_basis_points": 10,
        "prior_value_million": 246_541,
        "older_change": "2.8",
        "older_value_million": 246_375,
        "exclude_transport_direction": "decreased",
        "exclude_transport_change": "0.6",
        "exclude_defense_direction": "increased",
        "exclude_defense_change": "0.1",
        "shipments_value": 252_329,
        "shipments_change": "0.8",
        "unfilled_value": 1_158_641,
        "unfilled_change": "0.1",
        "inventories_value": 434_881,
        "inventories_change": "0.0",
        "next_reference": "March",
        "next_release": date(2020, 4, 24),
        "next_timezone": "EDT",
        "full_release": date(2020, 4, 2),
        "full_timezone": "EDT",
        "creation": "D:20200324110955-04'00'",
        "modification": "D:20200423094958-04'00'",
        "dimensions": ((612.0, 792.0),) * 7,
        "snapshots": {"2020-01": 10, "2020-02": 120},
        "previous": {"2020-01": -20, "2020-02": None},
        "revisions": {"2020-01": 30, "2020-02": None},
        "level_snapshots": {"2020-01": 246_541, "2020-02": 249_409},
        "level_previous": {"2020-01": 246_199, "2020-02": None},
        "level_revisions": {"2020-01": 342, "2020-02": None},
        "covid": False,
    },
    date(2020, 4, 24): {
        "reference": "March",
        "reference_key": "2020-03",
        "prior": "February",
        "weekday": "FRIDAY",
        "timezone": "EDT",
        "release_number": "CB 20-54",
        "release_code": "M3-1 (20)-03",
        "direction": "decreased",
        "delta_billion": "36.0",
        "value_billion": "213.2",
        "value_million": 213_184,
        "change": "-14.4",
        "change_basis_points": -1_440,
        "prior_change": "1.1",
        "prior_change_basis_points": 110,
        "prior_value_million": 249_167,
        "older_change": "0.1",
        "older_value_million": 246_558,
        "exclude_transport_direction": "decreased",
        "exclude_transport_change": "0.2",
        "exclude_defense_direction": "decreased",
        "exclude_defense_change": "15.8",
        "shipments_value": 240_715,
        "shipments_change": "-4.5",
        "unfilled_value": 1_135_165,
        "unfilled_change": "-2.0",
        "inventories_value": 437_420,
        "inventories_change": "0.6",
        "next_reference": "April",
        "next_release": date(2020, 5, 28),
        "next_timezone": "EDT",
        "full_release": date(2020, 5, 4),
        "full_timezone": "EDT",
        "creation": "D:20200423152159-04'00'",
        "modification": "D:20200527104843-04'00'",
        "dimensions": ((612.0, 792.0),) * 7,
        "snapshots": {"2020-01": 10, "2020-02": 110, "2020-03": -1_440},
        "previous": {"2020-01": 10, "2020-02": 120, "2020-03": None},
        "revisions": {"2020-01": 0, "2020-02": -10, "2020-03": None},
        "level_snapshots": {
            "2020-01": 246_558,
            "2020-02": 249_167,
            "2020-03": 213_184,
        },
        "level_previous": {
            "2020-01": 246_541,
            "2020-02": 249_409,
            "2020-03": None,
        },
        "level_revisions": {"2020-01": 17, "2020-02": -242, "2020-03": None},
        "covid": True,
    },
}


def pdf_bytes(
    *,
    release_date: date = RELEASE_DATE,
    replacements: dict[str, str] | None = None,
    pages: int = 7,
    blank_page: int | None = None,
    wrong_dimensions: bool = False,
    rotate_page: int | None = None,
    metadata: bool = True,
    metadata_overrides: dict[str, str] | None = None,
    duplicate_identity: bool = False,
) -> bytes:
    spec = RELEASES[release_date]
    full_release = cast(date, spec["full_release"])
    next_release = cast(date, spec["next_release"])
    first = [
        (
            f"FOR RELEASE AT 8:30 AM {spec['timezone']}, {spec['weekday']}, "
            f"{release_date:%B %d, %Y}".upper()
        ),
        (
            "MONTHLY ADVANCE REPORT ON DURABLE GOODS MANUFACTURERS' SHIPMENTS, "
            "INVENTORIES AND ORDERS"
        ),
        str(spec["reference"]).upper() + " 2020",
        f"Release Number: {spec['release_number']} {spec['release_code']}",
        (
            f"New orders for manufactured durable goods in {spec['reference']} "
            f"{spec['direction']} ${spec['delta_billion']} billion or "
            f"{str(spec['change']).removeprefix('-')} percent to "
            f"${spec['value_billion']} billion"
        ),
        (f"followed a {spec['prior_change']} percent {spec['prior']} increase"),
        (
            f"Excluding transportation, new orders {spec['exclude_transport_direction']} "
            f"{spec['exclude_transport_change']} percent"
        ),
        (
            f"Excluding defense, new orders {spec['exclude_defense_direction']} "
            f"{spec['exclude_defense_change']} percent"
        ),
        SAMPLING_MARKER,
    ]
    if spec["covid"]:
        first.append(COVID_MARKER)
    if duplicate_identity:
        first.append(str(first[0]))
    schedule = [
        (
            "Revised and more detailed estimates, plus nondurable goods data, will be "
            f"published on {full_release:%B} {full_release.day}, {full_release:%Y}, at "
            f"10:00 a.m. {spec['full_timezone']}."
        ),
        (
            f"The Advance Report on durable goods for {spec['next_reference']} is scheduled "
            f"for release on {next_release:%B} {next_release.day}, {next_release:%Y} at "
            f"8:30 a.m. {spec['next_timezone']}."
        ),
    ]
    methodology = [
        "EXPLANATORY NOTES",
        "Figures in text are adjusted for seasonality, but not for inflation.",
        "Figures on new and unfilled orders exclude data for semiconductor manufacturing.",
        "The M3 panel is not based on a probability sample; therefore, the sampling errors",
        "that are normally provided with sample surveys cannot be measured.",
    ]
    benchmark = [
        (
            "Corrections received after the full report will be released in the next month's "
            "advance report."
        ),
        (
            "Any revisions made later than two months will be reflected in the annual "
            "benchmark publication."
        ),
        "BENCHMARK NOTICE",
        (
            "Revised historical data from the Manufacturers' Shipments, Inventories, and "
            "Orders (M3) Survey will be issued."
        ),
    ]
    table1 = [
        "Table 1. Durable Goods Manufacturers' Shipments and New Orders",
        (
            f"DURABLE GOODS Total: Shipments ..... {spec['shipments_value']:,} 1 2 "
            f"{spec['shipments_change']} 0.0 0.0"
        ),
        (
            f"New Orders4 ..... {spec['value_million']:,} "
            f"{spec['prior_value_million']:,} {spec['older_value_million']:,} "
            f"{spec['change']} {spec['prior_change']} {spec['older_change']}"
        ),
    ]
    table2 = [
        "Table 2. Durable Goods Manufacturers' Unfilled Orders and Total Inventories",
        (
            f"DURABLE GOODS Total: Unfilled Orders4 ..... {spec['unfilled_value']:,} 1 2 "
            f"{spec['unfilled_change']} 0.0 0.0"
        ),
        (
            f"Total Inventories ..... {spec['inventories_value']:,} 1 2 "
            f"{spec['inventories_change']} 0.0 0.0"
        ),
    ]
    page_lines = [first, schedule, methodology, benchmark, ["official resources"], table1, table2]
    if release_date == date(2020, 4, 24):
        page_lines[1], page_lines[2] = [["official continuation"], methodology + schedule]
    if replacements:
        page_lines = [[_replace_all(line, replacements) for line in lines] for lines in page_lines]

    writer = PdfWriter()
    dimensions = cast(tuple[tuple[float, float], ...], spec["dimensions"])
    for index in range(pages):
        width, height = dimensions[index] if index < len(dimensions) else (612.0, 792.0)
        if wrong_dimensions and index == 0:
            width = 611.0
        writer.add_blank_page(width=width, height=height)
        if index == rotate_page:
            writer.pages[index].rotate(90)
        if index != blank_page:
            _write_page_text(
                writer,
                index,
                page_lines[index] if index < len(page_lines) else ["extra page"],
            )
    if metadata:
        metadata_values = {
            "/Author": "Nathan R Scarlett (CENSUS/EID FED)",
            "/Company": "U.S. Department of Commerce",
            "/Creator": "Acrobat PDFMaker 17 for Word",
            "/Producer": "Adobe PDF Library 15.0",
            "/CreationDate": str(spec["creation"]),
            "/ModDate": str(spec["modification"]),
            "/Title": "",
        }
        if metadata_overrides:
            metadata_values.update(metadata_overrides)
        writer.add_metadata(metadata_values)
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
    commands = ["BT", "/F1 7 Tf", "30 760 Td", "10 TL"]
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
) -> CensusDurableGoodsArchiveAdapter:
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
    return CensusDurableGoodsArchiveAdapter(safe, release_date=release_date)


def test_durable_goods_snapshot_is_exact_versioned_and_knowledge_safe() -> None:
    content = pdf_bytes()
    batch = adapter(content=content).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("202002:total_durable_goods_new_orders_monthly_change")
    assert record.entity_id == "census_m3:total_durable_goods_new_orders"
    assert record.source.sha256 == hashlib.sha256(content).hexdigest()
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.vintage_as_of == datetime(2020, 3, 25, 12, 30, tzinfo=UTC)
    assert record.interval.valid_from == datetime(2020, 2, 1, tzinfo=UTC)
    assert record.interval.published_at == datetime(2020, 3, 25, 12, 30, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 3, 25, 12, 30, tzinfo=UTC)
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_basis_points"] == 120
    assert record.payload["value_percent"] == "1.2"
    assert record.payload["value_million_dollars"] == 249_409
    assert record.payload["prior_month_revised_change_basis_points"] == 10
    assert record.payload["release_snapshot_revision_delta_basis_points"] == {
        "2020-01": 30,
        "2020-02": None,
    }
    assert record.payload["release_snapshot_level_revision_delta_million_dollars"] == {
        "2020-01": 342,
        "2020-02": None,
    }
    assert record.payload["probability_sample"] is False
    assert record.payload["confidence_intervals_computable"] is False
    assert record.payload["current_pdf_byte_identity_at_release_claimed"] is False
    assert record.payload["report_pdf_metadata_modified_after_release"] is True
    assert record.payload["pdf_table_snapshot_verified"] is True
    assert batch.receipts[0].historical_replay_eligible is True
    assert batch.receipts[0].record_count == 1
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 3, 25, 12, 29, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 3, 25, 12, 30, tzinfo=UTC)) == [record]


@pytest.mark.parametrize(
    ("release_date", "change", "level", "snapshots", "revisions", "available_at"),
    [
        (
            date(2020, 2, 27),
            -20,
            246_199,
            {"2020-01": -20},
            {"2020-01": None},
            datetime(2020, 2, 27, 13, 30, tzinfo=UTC),
        ),
        (
            date(2020, 3, 25),
            120,
            249_409,
            {"2020-01": 10, "2020-02": 120},
            {"2020-01": 30, "2020-02": None},
            datetime(2020, 3, 25, 12, 30, tzinfo=UTC),
        ),
        (
            date(2020, 4, 24),
            -1_440,
            213_184,
            {"2020-01": 10, "2020-02": 110, "2020-03": -1_440},
            {"2020-01": 0, "2020-02": -10, "2020-03": None},
            datetime(2020, 4, 24, 12, 30, tzinfo=UTC),
        ),
    ],
)
def test_verified_calendar_preserves_first_reports_and_later_revisions(
    release_date: date,
    change: int,
    level: int,
    snapshots: dict[str, int],
    revisions: dict[str, int | None],
    available_at: datetime,
) -> None:
    record = adapter(release_date=release_date).fetch().records[0]
    assert record.payload["value_basis_points"] == change
    assert record.payload["value_million_dollars"] == level
    assert record.payload["release_snapshot_change_basis_points"] == snapshots
    assert record.payload["release_snapshot_revision_delta_basis_points"] == revisions
    assert record.interval.available_at == available_at
    assert record.payload["covid_publication_standard_statement_present"] is (
        release_date == date(2020, 4, 24)
    )


def test_verified_calendar_and_response_url_are_closed() -> None:
    client = cast(SafeHttpClient, object())
    for release_date, filename in (
        (date(2020, 2, 27), "jan20adv.pdf"),
        (date(2020, 3, 25), "feb20adv.pdf"),
        (date(2020, 4, 24), "mar20adv.pdf"),
    ):
        item = CensusDurableGoodsArchiveAdapter(client, release_date=release_date)
        assert item.endpoint.endswith(filename)
    with pytest.raises(ValueError, match="verified Census M3 durable-goods calendar"):
        CensusDurableGoodsArchiveAdapter(client, release_date=date(2020, 5, 28))

    item = CensusDurableGoodsArchiveAdapter(client, release_date=RELEASE_DATE)
    for invalid in (
        "http://www.census.gov/manufacturing/m3/historical_data/pressreleases/adv/2020/feb20adv.pdf",
        "https://evil.example/manufacturing/m3/historical_data/pressreleases/adv/2020/feb20adv.pdf",
        "https://www.census.gov/manufacturing/m3/historical_data/pressreleases/adv/2020/other.pdf",
        (
            "https://www.census.gov/manufacturing/m3/historical_data/pressreleases/"
            "adv/2020/feb20adv.pdf?q=1"
        ),
    ):
        with pytest.raises(SourceSchemaError, match="response URL"):
            item._validate_response_url(invalid)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (pdf_bytes(pages=6), "exactly seven pages"),
        (pdf_bytes(blank_page=1), "blank text layer"),
        (pdf_bytes(wrong_dimensions=True), "dimensions"),
        (pdf_bytes(rotate_page=1), "rotations"),
        (pdf_bytes(metadata=False), "metadata"),
        (
            pdf_bytes(metadata_overrides={"/Author": "Other"}),
            "metadata",
        ),
        (
            pdf_bytes(replacements={"EXPLANATORY NOTES": "OTHER NOTES"}),
            "explanatory page",
        ),
        (
            pdf_bytes(replacements={"BENCHMARK NOTICE": "OTHER NOTICE"}),
            "benchmark page",
        ),
        (
            pdf_bytes(replacements={"Table 1. Durable Goods": "Table 1. Other Goods"}),
            "Table 1 page",
        ),
        (
            pdf_bytes(replacements={"Table 2. Durable Goods": "Table 2. Other Goods"}),
            "Table 2 page",
        ),
        (
            pdf_bytes(replacements={"8:30 AM EDT": "8:30 AM EST"}),
            "headline or release identity",
        ),
        (
            pdf_bytes(replacements={"CB 20-47": "CB 20-99"}),
            "headline or release identity",
        ),
        (
            pdf_bytes(replacements={"249.4": "249.5"}),
            "headline or release identity",
        ),
        (
            pdf_bytes(duplicate_identity=True),
            "identity is not unique",
        ),
        (
            pdf_bytes(replacements={"April 24, 2020": "April 23, 2020"}),
            "release schedule",
        ),
        (
            pdf_bytes(replacements={"not for inflation": "adjusted for inflation"}),
            "methodology",
        ),
        (
            pdf_bytes(replacements={"249,409": "249,410"}),
            "Table 1 values",
        ),
        (
            pdf_bytes(replacements={"1,158,641": "1,158,642"}),
            "Table 2 values",
        ),
        (
            pdf_bytes(replacements={SAMPLING_MARKER: SAMPLING_MARKER + " " + COVID_MARKER}),
            "COVID-19 statement",
        ),
    ],
)
def test_pdf_structure_identity_values_and_boundaries_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(content=content).fetch()


def test_march_snapshot_requires_its_covid_publication_statement() -> None:
    content = pdf_bytes(
        release_date=date(2020, 4, 24),
        replacements={COVID_MARKER: "no publication statement"},
    )
    with pytest.raises(SourceSchemaError, match="COVID-19 statement"):
        adapter(release_date=date(2020, 4, 24), content=content).fetch()


def test_content_type_prepublication_timezone_and_calendar_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="unexpected Census M3 durable-goods content type"):
        adapter(content_type="text/html").fetch()

    content = pdf_bytes()
    snapshot = HttpResponseSnapshot(
        status_code=200,
        headers={"Content-Type": "application/pdf"},
        request_url=(
            "https://www.census.gov/manufacturing/m3/historical_data/"
            "pressreleases/adv/2020/feb20adv.pdf"
        ),
        content=content,
    )

    class EarlyClient:
        def get(self, *_: Any, **__: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
            return snapshot, content, datetime(2020, 3, 25, 12, 29, 59, tzinfo=UTC)

    early = CensusDurableGoodsArchiveAdapter(
        cast(SafeHttpClient, EarlyClient()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()

    wrong_timezone = adapter(content=pdf_bytes(replacements={"8:30 AM EDT": "8:30 AM EST"}))
    wrong_timezone.spec = replace(wrong_timezone.spec, timezone_abbreviation="EST")
    with pytest.raises(SourceSchemaError, match="timezone does not match"):
        wrong_timezone.fetch()

    wrong_calendar = adapter()
    wrong_calendar.spec = replace(
        wrong_calendar.spec,
        next_reference_month=date(2020, 4, 1),
    )
    with pytest.raises(SourceSchemaError, match="next-release calendar"):
        wrong_calendar.fetch()
