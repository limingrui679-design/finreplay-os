from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import CensusHUDNRSArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 3, 24)
COVID_MARKER = "determined estimates in this release meet publication standards"
PAGE_TITLES = (
    None,
    "EXPLANATORY NOTES",
    "New Privately-Owned Houses Sold and For Sale",
    "New Privately-Owned Houses Sold, by Sales Price",
    (
        "New Houses Sold and For Sale by Stage of Construction and Median Number of "
        "Months on Sales Market"
    ),
)
RELEASES = {
    date(2020, 2, 26): {
        "reference": "January",
        "prior": "December",
        "prior_year": "2019",
        "weekday": "WEDNESDAY",
        "timezone": "EST",
        "release_number": "CB20-28",
        "value": "764,000",
        "value_thousand": "764",
        "change": "7.9",
        "signed_change": "7.9",
        "margin": "17.8",
        "star": "*",
        "direction": "above",
        "yoy": "18.6",
        "yoy_margin": "19.2",
        "yoy_star": "*",
        "yoy_direction": "above",
        "yoy_value": "644,000",
        "prior_value": "708,000",
        "prior_thousand": "708",
        "for_sale": "324,000",
        "supply": "5.1",
        "median": "348,200",
        "average": "402,300",
        "rse": "9",
        "revision_average": "4.2",
        "covid": False,
    },
    date(2020, 3, 24): {
        "reference": "February",
        "prior": "January",
        "prior_year": "2020",
        "weekday": "TUESDAY",
        "timezone": "EDT",
        "release_number": "CB20-49",
        "value": "765,000",
        "value_thousand": "765",
        "change": "4.4",
        "signed_change": "-4.4",
        "margin": "14.8",
        "star": "*",
        "direction": "below",
        "yoy": "14.3",
        "yoy_margin": "17.5",
        "yoy_star": "*",
        "yoy_direction": "above",
        "yoy_value": "669,000",
        "prior_value": "800,000",
        "prior_thousand": "800",
        "for_sale": "319,000",
        "supply": "5.0",
        "median": "345,900",
        "average": "403,800",
        "rse": "8",
        "revision_average": "4.6",
        "covid": False,
    },
    date(2020, 4, 23): {
        "reference": "March",
        "prior": "February",
        "prior_year": "2020",
        "weekday": "THURSDAY",
        "timezone": "EDT",
        "release_number": "CB20-62",
        "value": "627,000",
        "value_thousand": "627",
        "change": "15.4",
        "signed_change": "-15.4",
        "margin": "14.8",
        "star": "",
        "direction": "below",
        "yoy": "9.5",
        "yoy_margin": "14.6",
        "yoy_star": "*",
        "yoy_direction": "below",
        "yoy_value": "693,000",
        "prior_value": "741,000",
        "prior_thousand": "741",
        "for_sale": "333,000",
        "supply": "6.4",
        "median": "321,400",
        "average": "375,300",
        "rse": "8",
        "revision_average": "4.6",
        "covid": True,
    },
}


def pdf_bytes(
    *,
    release_date: date = RELEASE_DATE,
    replacements: dict[str, str] | None = None,
    pages: int = 5,
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
            f"FOR RELEASE AT 10:00 AM {spec['timezone']}, {spec['weekday']}, "
            f"{release_date:%B %d, %Y}".upper()
        ),
        f"MONTHLY NEW RESIDENTIAL SALES, {spec['reference']} 2020".upper(),
        f"Release Number: {spec['release_number']}",
        f"New Houses Sold1: {spec['value']}",
        (
            f"Sales of new single-family houses in {spec['reference']} 2020 were at a "
            f"seasonally adjusted annual rate of {spec['value']}"
        ),
        (
            f"{spec['change']} percent (±{spec['margin']} percent){spec['star']} "
            f"{spec['direction']} the revised {spec['prior']} rate of {spec['prior_value']}"
        ),
        (
            f"{spec['yoy']} percent (±{spec['yoy_margin']} percent){spec['yoy_star']} "
            f"{spec['yoy_direction']} the {spec['reference']} 2019 estimate of "
            f"{spec['yoy_value']}"
        ),
        (
            f"The median sales price of new houses sold in {spec['reference']} 2020 was "
            f"${spec['median']}"
        ),
        f"The average sales price was ${spec['average']}",
        f"new houses for sale at the end of {spec['reference']} was {spec['for_sale']}",
        f"supply of {spec['supply']} months at the current sales rate",
    ]
    if spec["covid"]:
        first.append(COVID_MARKER)
    first.extend(extra_first)
    notes = [
        "EXPLANATORY NOTES",
        "These statistics are subject to sampling variability as well as nonsampling error.",
        "All ranges given for percent changes are 90-percent confidence intervals.",
        "The confidence intervals account only for sampling variability.",
        "It takes 4 months to establish a trend for new houses sold.",
        (
            'Since a "sale" is defined as a deposit taken or sales agreement signed, this '
            "can occur prior to a permit being issued."
        ),
        (
            "On average, the preliminary seasonally adjusted estimate of total sales is "
            f"revised about {spec['revision_average']} percent."
        ),
        (
            "The 90 percent confidence interval includes zero. In such cases, there is "
            "insufficient statistical evidence to conclude that the actual change is "
            "different from zero."
        ),
    ]
    table1 = [
        str(PAGE_TITLES[2]),
        f"{spec['prior']} (r) ..... {spec['prior_thousand']}",
        f"{spec['reference']} (p) ..... {spec['value_thousand']}",
        f"Average RSE (%) 3 ..... {spec['rse']}",
        (
            f"{month_abbreviation} 2020 from {prior_abbreviation} "
            f"{spec['prior_year']} ..... {spec['signed_change']} %"
        ),
        f"90 percent confidence interval 5 ..... ± {spec['margin']}",
    ]
    page_lines: list[list[str]] = [
        first,
        notes,
        table1,
        [str(PAGE_TITLES[3]), "official NRS Table 2"],
        [str(PAGE_TITLES[4]), "official NRS Table 3"],
    ]
    if replacements:
        page_lines = [[_replace_all(line, replacements) for line in lines] for lines in page_lines]

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
            line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("±", "\\261")
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
) -> CensusHUDNRSArchiveAdapter:
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
    return CensusHUDNRSArchiveAdapter(safe, release_date=release_date)


def test_nrs_snapshot_is_exact_versioned_and_knowledge_safe() -> None:
    content = pdf_bytes()
    batch = adapter(content=content).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("202002:new_single_family_houses_sold")
    assert record.entity_id == "census_hud_nrs:new_single_family_houses_sold_us"
    assert record.source.sha256 == hashlib.sha256(content).hexdigest()
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.vintage_as_of == datetime(2020, 3, 24, 14, 0, tzinfo=UTC)
    assert record.interval.valid_from == datetime(2020, 2, 1, tzinfo=UTC)
    assert record.interval.published_at == datetime(2020, 3, 24, 14, 0, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 3, 24, 14, 0, tzinfo=UTC)
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_units"] == 765_000
    assert record.payload["value_thousand_units"] == 765
    assert record.payload["prior_month_revised_value_units"] == 800_000
    assert record.payload["prior_month_value_in_previous_release_units"] == 764_000
    assert record.payload["prior_month_revision_delta_units"] == 36_000
    assert record.payload["reported_monthly_change_percent"] == "-4.4"
    assert record.payload["reported_monthly_margin_90_percent"] == "14.8"
    assert record.payload["reported_monthly_ci_includes_zero"] is True
    assert record.payload["reported_year_over_year_ci_includes_zero"] is True
    assert record.payload["reported_months_supply"] == "5.0"
    assert record.payload["pdf_table_snapshot_verified"] is True
    assert record.payload["release_pdf_pages"] == 5
    assert batch.receipts[0].historical_replay_eligible is True
    assert batch.receipts[0].record_count == 1
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 3, 24, 13, 59, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 3, 24, 14, 0, tzinfo=UTC)) == [record]


@pytest.mark.parametrize(
    (
        "release_date",
        "value",
        "revised_prior",
        "previous",
        "delta",
        "available_at",
        "monthly_ci_zero",
    ),
    [
        (
            date(2020, 2, 26),
            764_000,
            708_000,
            None,
            None,
            datetime(2020, 2, 26, 15, 0, tzinfo=UTC),
            True,
        ),
        (
            date(2020, 3, 24),
            765_000,
            800_000,
            764_000,
            36_000,
            datetime(2020, 3, 24, 14, 0, tzinfo=UTC),
            True,
        ),
        (
            date(2020, 4, 23),
            627_000,
            741_000,
            765_000,
            -24_000,
            datetime(2020, 4, 23, 14, 0, tzinfo=UTC),
            False,
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
    monthly_ci_zero: bool,
) -> None:
    record = adapter(release_date=release_date).fetch().records[0]
    assert record.payload["value_units"] == value
    assert record.payload["prior_month_revised_value_units"] == revised_prior
    assert record.payload["prior_month_value_in_previous_release_units"] == previous
    assert record.payload["prior_month_revision_delta_units"] == delta
    assert record.payload["reported_monthly_ci_includes_zero"] is monthly_ci_zero
    assert record.interval.available_at == available_at


def test_verified_calendar_and_response_url_are_closed() -> None:
    client = cast(SafeHttpClient, object())
    for release_date, filename in (
        (date(2020, 2, 26), "newressales_202001.pdf"),
        (date(2020, 3, 24), "newressales_202002.pdf"),
        (date(2020, 4, 23), "newressales_202003.pdf"),
    ):
        item = CensusHUDNRSArchiveAdapter(client, release_date=release_date)
        assert item.endpoint.endswith(filename)
    with pytest.raises(ValueError, match="verified Census/HUD NRS calendar"):
        CensusHUDNRSArchiveAdapter(client, release_date=date(2020, 5, 26))

    item = CensusHUDNRSArchiveAdapter(client, release_date=RELEASE_DATE)
    for invalid in (
        "http://www.census.gov/construction/nrs/pdf/newressales_202002.pdf",
        "https://evil.example/construction/nrs/pdf/newressales_202002.pdf",
        "https://www.census.gov/construction/nrs/pdf/other.pdf",
        "https://www.census.gov/construction/nrs/pdf/newressales_202002.pdf?q=1",
    ):
        with pytest.raises(SourceSchemaError, match="response URL"):
            item._validate_response_url(invalid)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (pdf_bytes(pages=4), "exactly five pages"),
        (pdf_bytes(blank_page=1), "blank text layer"),
        (pdf_bytes(width=611), "dimensions"),
        (
            pdf_bytes(replacements={"EXPLANATORY NOTES": "OTHER NOTES"}),
            "page identity",
        ),
        (
            pdf_bytes(replacements={"10:00 AM EDT": "10:00 AM EST"}),
            "headline or release identity",
        ),
        (
            pdf_bytes(replacements={"CB20-49": "CB20-99"}),
            "headline or release identity",
        ),
        (
            pdf_bytes(replacements={"New Houses Sold1: 765,000": "New Houses Sold1: 765,001"}),
            "headline or release identity",
        ),
        (
            pdf_bytes(replacements={"4 months": "5 months"}),
            "explanatory notes",
        ),
        (
            pdf_bytes(replacements={"February (p) ..... 765": "February (p) ..... 766"}),
            "Table 1a values",
        ),
        (
            pdf_bytes(replacements={"January (r) ..... 800": "January (r) ..... 799"}),
            "Table 1a values",
        ),
        (
            pdf_bytes(replacements={"Average RSE (%) 3 ..... 8": "Average RSE (%) 3 ..... 9"}),
            "Table 1a values",
        ),
        (
            pdf_bytes(replacements={"-4.4 %": "-4.3 %"}),
            "Table 1a values",
        ),
        (
            pdf_bytes(replacements={"± 14.8": "± 14.7"}),
            "Table 1a values",
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
        release_date=date(2020, 4, 23),
        replacements={COVID_MARKER: "no publication statement"},
    )
    with pytest.raises(SourceSchemaError, match="COVID-19 statement"):
        adapter(release_date=date(2020, 4, 23), content=content).fetch()


def test_content_type_and_prepublication_retrieval_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="unexpected Census/HUD NRS content type"):
        adapter(content_type="text/html").fetch()

    content = pdf_bytes()
    snapshot = HttpResponseSnapshot(
        status_code=200,
        headers={"Content-Type": "application/pdf"},
        request_url="https://www.census.gov/construction/nrs/pdf/newressales_202002.pdf",
        content=content,
    )

    class EarlyClient:
        def get(self, *_: Any, **__: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
            return snapshot, content, datetime(2020, 3, 24, 13, 59, 59, tzinfo=UTC)

    early = CensusHUDNRSArchiveAdapter(
        cast(SafeHttpClient, EarlyClient()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
