from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import CensusHUDNRCArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 3, 18)
COVID_MARKER = "determined estimates in this release meet publication standards"
PAGE_TITLES = (
    None,
    "EXPLANATORY NOTES",
    "New Privately-Owned Housing Units Authorized in Permit-Issuing Places",
    "New Privately-Owned Housing Units Authorized, but Not Started, at End of Period",
    "New Privately-Owned Housing Units Started",
    "New Privately-Owned Housing Units Under Construction at End of Period",
    "New Privately-Owned Housing Units Completed",
)
RELEASES = {
    date(2020, 2, 19): {
        "reference": "January",
        "reference_year": "2020",
        "prior": "December",
        "prior_year": "2019",
        "weekday": "WEDNESDAY",
        "timezone": "EST",
        "release_number": "CB20-26",
        "value": "1,567,000",
        "value_thousand": "1,567",
        "change": "3.6",
        "signed_change": "-3.6",
        "margin": "13.3",
        "star": "*",
        "yoy": "21.4",
        "yoy_margin": "12.2",
        "yoy_star": "",
        "prior_value": "1,626,000",
        "prior_thousand": "1,626",
        "single_family": "1,010,000",
        "five_plus": "547,000",
        "rse": "5",
        "revision_leq": "2.3",
        "covid": False,
    },
    date(2020, 3, 18): {
        "reference": "February",
        "reference_year": "2020",
        "prior": "January",
        "prior_year": "2020",
        "weekday": "WEDNESDAY",
        "timezone": "EDT",
        "release_number": "CB20-41",
        "value": "1,599,000",
        "value_thousand": "1,599",
        "change": "1.5",
        "signed_change": "-1.5",
        "margin": "12.4",
        "star": "*",
        "yoy": "39.2",
        "yoy_margin": "17.7",
        "yoy_star": "",
        "prior_value": "1,624,000",
        "prior_thousand": "1,624",
        "single_family": "1,072,000",
        "five_plus": "508,000",
        "rse": "5",
        "revision_leq": "2.1",
        "covid": False,
    },
    date(2020, 4, 16): {
        "reference": "March",
        "reference_year": "2020",
        "prior": "February",
        "prior_year": "2020",
        "weekday": "THURSDAY",
        "timezone": "EDT",
        "release_number": "CB20-61",
        "value": "1,216,000",
        "value_thousand": "1,216",
        "change": "22.3",
        "signed_change": "-22.3",
        "margin": "12.2",
        "star": "",
        "yoy": "1.4",
        "yoy_margin": "12.7",
        "yoy_star": "*",
        "prior_value": "1,564,000",
        "prior_thousand": "1,564",
        "single_family": "856,000",
        "five_plus": "347,000",
        "rse": "6",
        "revision_leq": "2.1",
        "covid": True,
    },
}


def pdf_bytes(
    *,
    release_date: date = RELEASE_DATE,
    replacements: dict[str, str] | None = None,
    pages: int = 7,
    width: int = 612,
    height: int = 792,
    blank_page: int | None = None,
    extra_first: tuple[str, ...] = (),
) -> bytes:
    spec = RELEASES[release_date]
    month_abbreviation = str(spec["reference"])[:3] + "."
    prior_abbreviation = str(spec["prior"])[:3] + "."
    first = [
        (
            f"FOR RELEASE AT 8:30 AM {spec['timezone']}, {spec['weekday']}, "
            f"{release_date:%B %d, %Y}".upper()
        ),
        (
            "MONTHLY NEW RESIDENTIAL CONSTRUCTION, "
            f"{spec['reference']} {spec['reference_year']}"
        ).upper(),
        f"Release Number: {spec['release_number']}",
        f"Housing Starts: {spec['value']}",
        (
            f"Privately-owned housing starts in {spec['reference']} were at a seasonally "
            f"adjusted annual rate of {spec['value']}."
        ),
        (
            f"This is {spec['change']} percent (±{spec['margin']} percent){spec['star']} "
            f"below the revised {spec['prior']} estimate of {spec['prior_value']}"
        ),
        (
            f"{spec['yoy']} percent (±{spec['yoy_margin']} percent){spec['yoy_star']} above "
            f"the {spec['reference']} 2019 rate"
        ),
        (
            f"Single-family housing starts in {spec['reference']} were at a rate of "
            f"{spec['single_family']}"
        ),
        (
            f"The {spec['reference']} rate for units in buildings with five units or more "
            f"was {spec['five_plus']}"
        ),
    ]
    if spec["covid"]:
        first.append(COVID_MARKER)
    first.extend(extra_first)
    notes = [
        "EXPLANATORY NOTES",
        "It may take six months for total starts to establish an underlying trend.",
        "The estimates are subject to sampling variability as well as nonsampling error.",
        "All ranges given for percentage changes are 90 percent confidence intervals.",
        "The confidence intervals account only for sampling variability.",
        (
            "The preliminary seasonally adjusted estimates of total building permits, "
            "housing starts and housing completions are revised "
            f"{spec['revision_leq']} percent or less."
        ),
        (
            "The 90 percent confidence interval includes zero. In such cases, there is "
            "insufficient statistical evidence to conclude that the actual change is "
            "different from zero."
        ),
    ]
    table3 = [
        str(PAGE_TITLES[4]),
        f"{spec['reference']} (p) ..... {spec['value_thousand']}",
        f"{spec['prior']} (r) ..... {spec['prior_thousand']}",
        f"Average RSE (%) 1 ..... {spec['rse']}",
        (
            f"{month_abbreviation} 2020 from {prior_abbreviation} "
            f"{spec['prior_year']} ..... {spec['signed_change']} %"
        ),
        f"90 percent confidence interval 3 ..... ±{spec['margin']}",
    ]
    page_lines: list[list[str]] = [first, notes]
    for index in range(2, 7):
        if index == 4:
            page_lines.append(table3)
        else:
            page_lines.append([str(PAGE_TITLES[index]), f"official NRC table page {index + 1}"])
    if replacements:
        page_lines = [
            [_replace_all(line, replacements) for line in lines]
            for lines in page_lines
        ]

    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=width, height=height)
        if index != blank_page:
            _write_page_text(
                writer,
                index,
                page_lines[index] if index < len(page_lines) else ["extra page"],
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


def adapter(
    *,
    release_date: date = RELEASE_DATE,
    content: bytes | None = None,
    content_type: str = "application/pdf",
) -> CensusHUDNRCArchiveAdapter:
    selected = content if content is not None else pdf_bytes(release_date=release_date)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=selected,
            headers={
                "Content-Type": content_type,
                "Last-Modified": "Mon, 02 Aug 2021 20:00:00 GMT",
            },
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return CensusHUDNRCArchiveAdapter(safe, release_date=release_date)


def test_nrc_snapshot_is_exact_versioned_and_knowledge_safe() -> None:
    content = pdf_bytes()
    batch = adapter(content=content).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("202002:total_housing_starts")
    assert record.entity_id == "census_hud_nrc:privately_owned_housing_starts_total"
    assert record.source.sha256 == hashlib.sha256(content).hexdigest()
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.vintage_as_of == datetime(2020, 3, 18, 12, 30, tzinfo=UTC)
    assert record.interval.valid_from == datetime(2020, 2, 1, tzinfo=UTC)
    assert record.interval.published_at == datetime(2020, 3, 18, 12, 30, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 3, 18, 12, 30, tzinfo=UTC)
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_units"] == 1_599_000
    assert record.payload["value_thousand_units"] == 1_599
    assert record.payload["prior_month_revised_value_units"] == 1_624_000
    assert record.payload["prior_month_value_in_previous_release_units"] == 1_567_000
    assert record.payload["prior_month_revision_delta_units"] == 57_000
    assert record.payload["reported_monthly_change_percent"] == "-1.5"
    assert record.payload["reported_monthly_margin_90_percent"] == "12.4"
    assert record.payload["reported_monthly_ci_includes_zero"] is True
    assert record.payload["pdf_table_snapshot_verified"] is True
    assert record.payload["release_pdf_pages"] == 7
    assert batch.receipts[0].historical_replay_eligible is True
    assert batch.receipts[0].record_count == 1
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 3, 18, 12, 29, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 3, 18, 12, 30, tzinfo=UTC)) == [record]


@pytest.mark.parametrize(
    ("release_date", "value", "revised_prior", "previous", "delta", "available_at"),
    [
        (
            date(2020, 2, 19),
            1_567_000,
            1_626_000,
            None,
            None,
            datetime(2020, 2, 19, 13, 30, tzinfo=UTC),
        ),
        (
            date(2020, 3, 18),
            1_599_000,
            1_624_000,
            1_567_000,
            57_000,
            datetime(2020, 3, 18, 12, 30, tzinfo=UTC),
        ),
        (
            date(2020, 4, 16),
            1_216_000,
            1_564_000,
            1_599_000,
            -35_000,
            datetime(2020, 4, 16, 12, 30, tzinfo=UTC),
        ),
    ],
)
def test_verified_calendar_preserves_each_preliminary_snapshot_and_later_revision(
    release_date: date,
    value: int,
    revised_prior: int,
    previous: int | None,
    delta: int | None,
    available_at: datetime,
) -> None:
    record = adapter(release_date=release_date).fetch().records[0]
    assert record.payload["value_units"] == value
    assert record.payload["prior_month_revised_value_units"] == revised_prior
    assert record.payload["prior_month_value_in_previous_release_units"] == previous
    assert record.payload["prior_month_revision_delta_units"] == delta
    assert record.interval.available_at == available_at


def test_verified_calendar_and_response_url_are_closed() -> None:
    client = cast(SafeHttpClient, object())
    for release_date, filename in (
        (date(2020, 2, 19), "newresconst_202001.pdf"),
        (date(2020, 3, 18), "newresconst_202002.pdf"),
        (date(2020, 4, 16), "newresconst_202003.pdf"),
    ):
        item = CensusHUDNRCArchiveAdapter(client, release_date=release_date)
        assert item.endpoint.endswith(filename)
    with pytest.raises(ValueError, match="verified Census/HUD NRC calendar"):
        CensusHUDNRCArchiveAdapter(client, release_date=date(2020, 5, 19))

    item = CensusHUDNRCArchiveAdapter(client, release_date=RELEASE_DATE)
    for invalid in (
        "http://www.census.gov/construction/nrc/pdf/newresconst_202002.pdf",
        "https://evil.example/construction/nrc/pdf/newresconst_202002.pdf",
        "https://www.census.gov/construction/nrc/pdf/other.pdf",
        "https://www.census.gov/construction/nrc/pdf/newresconst_202002.pdf?q=1",
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
        (pdf_bytes(width=611), "dimensions"),
        (
            pdf_bytes(replacements={"EXPLANATORY NOTES": "OTHER NOTES"}),
            "page identity",
        ),
        (
            pdf_bytes(replacements={"8:30 AM EDT": "8:30 AM EST"}),
            "headline or release identity",
        ),
        (
            pdf_bytes(replacements={"CB20-41": "CB20-99"}),
            "headline or release identity",
        ),
        (
            pdf_bytes(replacements={"Housing Starts: 1,599,000": "Housing Starts: 1,599,001"}),
            "headline or release identity",
        ),
        (
            pdf_bytes(replacements={"six months for total starts": "five months for total starts"}),
            "explanatory notes",
        ),
        (
            pdf_bytes(replacements={"February (p) ..... 1,599": "February (p) ..... 1,598"}),
            "Table 3a values",
        ),
        (
            pdf_bytes(replacements={"January (r) ..... 1,624": "January (r) ..... 1,623"}),
            "Table 3a values",
        ),
        (
            pdf_bytes(replacements={"Average RSE (%) 1 ..... 5": "Average RSE (%) 1 ..... 6"}),
            "Table 3a values",
        ),
        (
            pdf_bytes(replacements={"-1.5 %": "-1.4 %"}),
            "Table 3a values",
        ),
        (
            pdf_bytes(replacements={"±12.4": "±12.3"}),
            "headline or release identity",
        ),
        (
            pdf_bytes(extra_first=(COVID_MARKER,)),
            "COVID-19 statement",
        ),
    ],
)
def test_pdf_structure_identity_values_and_uncertainty_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(content=content).fetch()


def test_april_snapshot_requires_its_covid_publication_statement() -> None:
    content = pdf_bytes(
        release_date=date(2020, 4, 16),
        replacements={COVID_MARKER: "no publication statement"},
    )
    with pytest.raises(SourceSchemaError, match="COVID-19 statement"):
        adapter(release_date=date(2020, 4, 16), content=content).fetch()


def test_content_type_and_prepublication_retrieval_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="unexpected Census/HUD NRC content type"):
        adapter(content_type="text/html").fetch()

    content = pdf_bytes()
    snapshot = HttpResponseSnapshot(
        status_code=200,
        headers={"Content-Type": "application/pdf"},
        request_url=(
            "https://www.census.gov/construction/nrc/pdf/newresconst_202002.pdf"
        ),
        content=content,
    )

    class EarlyClient:
        def get(self, *_: Any, **__: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
            return snapshot, content, datetime(2020, 3, 18, 12, 29, 59, tzinfo=UTC)

    early = CensusHUDNRCArchiveAdapter(
        cast(SafeHttpClient, EarlyClient()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
