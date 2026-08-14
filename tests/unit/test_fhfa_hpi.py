from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import FHFAHPIArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.adapters.fhfa_hpi import _VERIFIED_RELEASES
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 4, 22)
SCHEDULE_URL = (
    "https://www.fhfa.gov/news/news-release/"
    "fhfa-announces-2020-release-dates-for-house-price-index"
)


def schedule_bytes(*, replacements: dict[str, str] | None = None, noise: str = "") -> bytes:
    text = """
    <html><head><title>FHFA Announces 2020 Release Dates for House Price Index</title>
    <script>ignored analytics text Wednesday, March 25</script></head><body>
    <h1>FHFA Announces 2020 Release Dates for House Price Index</h1>
    <time>08/20/2019</time>
    <p>The Federal Housing Finance Agency today announced the FHFA House Price Index HPI
    will be released at 9 a.m. ET on the following dates in 2020:</p>
    <table>
    <tr><td>Wednesday, March 25</td><td>Monthly Index</td></tr>
    <tr><td>Wednesday, April 22</td><td>Monthly Index</td></tr>
    <tr><td>Tuesday, May 26</td><td>Quarterly and Monthly Index</td></tr>
    </table>
    <p>Release dates for 2020 and the remainder of 2019 are available at the HPI page.</p>
    """ + noise + "</body></html>"
    for old, new in (replacements or {}).items():
        text = text.replace(old, new)
    return text.encode()


def pdf_bytes(
    *,
    release_date: date = RELEASE_DATE,
    replacements: dict[str, str] | None = None,
    pages: int | None = None,
    width: int = 612,
    height: int = 792,
    blank_page: int | None = None,
    rotation_override: tuple[int, ...] | None = None,
    metadata_override: dict[str, str] | None = None,
) -> bytes:
    spec = _VERIFIED_RELEASES[release_date]
    page_count = spec.report_pages if pages is None else pages
    lines: list[list[str]] = [
        [f"official FHFA report page {index + 1}"] for index in range(page_count)
    ]
    if page_count:
        lines[0] = list(spec.cover_markers)
    if page_count > max(spec.press_page_indexes):
        lines[spec.press_page_indexes[0]] = [
            *spec.headline_markers,
            spec.next_release_marker,
            spec.covid_timing_marker,
            "weighted, repeat-sales statistical technique",
            "seasonally adjusted, purchase-only data",
        ]
    if page_count > spec.table_page_index:
        reference = spec.reference_month
        prior_month = 12 if reference.month == 1 else reference.month - 1
        prior_year = reference.year - 1 if reference.month == 1 else reference.year
        current_label = (
            f"{date(prior_year, prior_month, 1):%b %y} - {reference:%b %y}"
        )
        year_label = f"{reference:%b} 19 - {reference:%b} 20"
        table = [
            "Monthly Price Change Estimates for U.S. and Census Divisions",
            "Purchase-Only Index (Seasonally Adjusted)",
            _percent_row(current_label, spec.current_change_basis_points),
            _percent_row(year_label, spec.year_over_year_basis_points),
            (
                f"{reference:%B}-20 "
                + " ".join(spec.current_index_values)
            ),
            "Monthly Index Values for Latest 18 Months: U.S. and Census Divisions",
            "January 1991 = 100",
            "Source: FHFA",
        ]
        for revision in spec.revision_rows:
            table.extend(
                (
                    _percent_row(revision.label, revision.current_basis_points),
                    "(Previous Estimate) "
                    + " ".join(_format_percent(item) for item in revision.previous_basis_points),
                )
            )
        lines[spec.table_page_index] = table
    if page_count > spec.overview_page_index:
        lines[spec.overview_page_index] = [
            "An overview of the FHFA HPI",
            "The FHFA HPI is a broad economic measure of the movement of single-family "
            "house prices in the United States.",
            "Today, indexes cover all 50 states and over 400 American cities.",
            "Purchase-Only",
            "Tracks changes in transaction prices for conforming, conventional mortgages",
        ]
    if page_count > spec.schedule_page_index:
        lines[spec.schedule_page_index] = [
            "FHFA HPI Release Dates for 2020",
            f"Public releases occur at {spec.footer_time_label}",
            "Wednesday, March 25 Monthly Index January 2020",
            "Wednesday, April 22 Monthly Index February 2020",
            "Tuesday, May 26 Quarterly Index (with Monthly Tables) March 2020 and 2020Q1",
        ]
    if replacements:
        lines = [
            [_replace_all(line, replacements) for line in page_lines]
            for page_lines in lines
        ]

    writer = PdfWriter()
    rotations = rotation_override or spec.page_rotations[:page_count]
    for index in range(page_count):
        writer.add_blank_page(width=width, height=height)
        if index < len(rotations) and rotations[index]:
            writer.pages[index].rotate(rotations[index])
        if index != blank_page:
            _write_page_text(writer, index, lines[index])
    metadata = {
        "/Author": "Federal Housing Finance Agency",
        "/Title": spec.pdf_title,
        "/Subject": spec.pdf_subject,
        "/Keywords": "FHFA, house price index, HPI",
        "/CreationDate": spec.pdf_creation_date,
        "/ModDate": spec.pdf_modification_date,
    }
    metadata.update(metadata_override or {})
    writer.add_metadata(metadata)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _replace_all(value: str, replacements: dict[str, str]) -> str:
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def _format_percent(value_basis_points: int) -> str:
    return f"{value_basis_points / 100:.1f}%"


def _percent_row(label: str, values: tuple[int, ...]) -> str:
    return f"{label} " + " ".join(_format_percent(item) for item in values)


def _write_page_text(writer: PdfWriter, page_index: int, lines: list[str]) -> None:
    page = writer.pages[page_index]
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    page[NameObject("/Resources")] = resources
    commands = ["BT", "/F1 6 Tf", "25 760 Td", "8 TL"]
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
    report_content: bytes | None = None,
    schedule_content: bytes | None = None,
    report_content_type: str = "application/pdf",
    schedule_content_type: str = "text/html; charset=utf-8",
) -> FHFAHPIArchiveAdapter:
    spec = _VERIFIED_RELEASES[release_date]
    report = report_content or pdf_bytes(release_date=release_date)
    schedule = schedule_content or schedule_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == SCHEDULE_URL:
            return httpx.Response(
                200,
                content=schedule,
                headers={"Content-Type": schedule_content_type},
                request=request,
            )
        if str(request.url) == spec.report_url:
            return httpx.Response(
                200,
                content=report,
                headers={"Content-Type": report_content_type},
                request=request,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return FHFAHPIArchiveAdapter(safe, release_date=release_date)


def test_fhfa_snapshot_is_versioned_scheduled_and_knowledge_safe() -> None:
    report = pdf_bytes()
    schedule = schedule_bytes()
    batch = adapter(report_content=report, schedule_content=schedule).fetch()

    assert len(batch.records) == 1
    assert len(batch.receipts) == 2
    assert len(batch.artifacts) == 2
    record = batch.records[0]
    assert record.record_id.endswith("202002:us_purchase_only_hpi_monthly_change")
    assert record.entity_id == "fhfa_hpi:us_purchase_only_seasonally_adjusted"
    assert record.source.sha256 == hashlib.sha256(report).hexdigest()
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.vintage_as_of == datetime(2020, 4, 22, 13, 0, tzinfo=UTC)
    assert record.interval.valid_from == datetime(2020, 2, 1, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 4, 22, 13, 0, tzinfo=UTC)
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_basis_points"] == 70
    assert record.payload["value_percent"] == "0.7"
    assert record.payload["reported_year_over_year_change_basis_points"] == 570
    assert record.payload["reported_current_index_by_geography"]["U.S."] == "287.0"
    assert record.payload["release_snapshot_monthly_change_basis_points"] == {
        "2020-01": 50,
        "2020-02": 70,
    }
    assert record.payload["release_snapshot_revision_delta_basis_points"] == {
        "2020-01": 20,
        "2020-02": None,
    }
    assert record.payload["report_footer_time_label_differs_from_schedule_wording"] is False
    assert batch.receipts[0].record_count == 0
    assert batch.receipts[1].record_count == 1
    assert all(item.historical_replay_eligible for item in batch.receipts)
    assert batch.artifacts[0].content == schedule
    assert batch.artifacts[1].content == report

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 4, 22, 12, 59, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 4, 22, 13, 0, tzinfo=UTC)) == [record]


@pytest.mark.parametrize(
    (
        "release_date",
        "value",
        "snapshots",
        "revisions",
        "available_at",
        "footer_differs",
        "modified_after_release",
    ),
    [
        (
            date(2020, 3, 25),
            30,
            {"2020-01": 30},
            {"2020-01": None},
            datetime(2020, 3, 25, 13, 0, tzinfo=UTC),
            True,
            False,
        ),
        (
            date(2020, 4, 22),
            70,
            {"2020-01": 50, "2020-02": 70},
            {"2020-01": 20, "2020-02": None},
            datetime(2020, 4, 22, 13, 0, tzinfo=UTC),
            False,
            False,
        ),
        (
            date(2020, 5, 26),
            10,
            {"2020-01": 50, "2020-02": 80, "2020-03": 10},
            {"2020-01": 0, "2020-02": 10, "2020-03": None},
            datetime(2020, 5, 26, 13, 0, tzinfo=UTC),
            False,
            True,
        ),
    ],
)
def test_verified_release_calendar_preserves_initial_values_and_later_revisions(
    release_date: date,
    value: int,
    snapshots: dict[str, int],
    revisions: dict[str, int | None],
    available_at: datetime,
    footer_differs: bool,
    modified_after_release: bool,
) -> None:
    record = adapter(release_date=release_date).fetch().records[0]
    assert record.payload["value_basis_points"] == value
    assert record.payload["release_snapshot_monthly_change_basis_points"] == snapshots
    assert record.payload["release_snapshot_revision_delta_basis_points"] == revisions
    assert record.interval.available_at == available_at
    assert (
        record.payload["report_footer_time_label_differs_from_schedule_wording"]
        is footer_differs
    )
    assert record.payload["report_pdf_metadata_modified_after_release"] is modified_after_release


def test_schedule_semantic_hash_is_stable_while_raw_hash_remains_visible() -> None:
    first = adapter(schedule_content=schedule_bytes(noise="<p>first wrapper</p>")).fetch()
    second = adapter(schedule_content=schedule_bytes(noise="<p>second wrapper</p>")).fetch()

    assert first.records[0].source.source_version == second.records[0].source.source_version
    assert (
        first.records[0].payload["official_schedule_semantic_sha256"]
        == second.records[0].payload["official_schedule_semantic_sha256"]
    )
    assert first.receipts[0].response_sha256 != second.receipts[0].response_sha256


def test_verified_calendar_and_response_urls_are_closed() -> None:
    client = cast(SafeHttpClient, object())
    for release_date, suffix in (
        (date(2020, 3, 25), "house-price-index-report-january-2020"),
        (date(2020, 4, 22), "house-price-index-report-february-2020"),
        (date(2020, 5, 26), "fhfa-house-price-index-report-2020q1"),
    ):
        item = FHFAHPIArchiveAdapter(client, release_date=release_date)
        assert item.endpoint.endswith(suffix)
    with pytest.raises(ValueError, match="verified FHFA HPI calendar"):
        FHFAHPIArchiveAdapter(client, release_date=date(2020, 6, 24))

    item = FHFAHPIArchiveAdapter(client, release_date=RELEASE_DATE)
    for invalid in (
        "http://www.fhfa.gov/document/d/hpi/house-price-index-report-february-2020",
        "https://evil.example/document/d/hpi/house-price-index-report-february-2020",
        "https://www.fhfa.gov/document/d/hpi/other",
        "https://www.fhfa.gov/document/d/hpi/house-price-index-report-february-2020?q=1",
    ):
        with pytest.raises(SourceSchemaError, match="report response URL"):
            item._validate_report_url(invalid)
    for invalid in (
        "http://www.fhfa.gov/news/news-release/"
        "fhfa-announces-2020-release-dates-for-house-price-index",
        "https://www.fhfa.gov/news/news-release/other",
        f"{SCHEDULE_URL}#fragment",
    ):
        with pytest.raises(SourceSchemaError, match="schedule response URL"):
            item._validate_schedule_url(invalid)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (pdf_bytes(pages=11), "exactly 12 pages"),
        (pdf_bytes(blank_page=1), "blank text layer"),
        (pdf_bytes(width=611), "dimensions"),
        (
            pdf_bytes(rotation_override=(0,) * 12),
            "rotations",
        ),
        (
            pdf_bytes(metadata_override={"/Author": "Other agency"}),
            "metadata",
        ),
        (
            pdf_bytes(replacements={"Data thru February 2020": "Data thru January 2020"}),
            "cover identity",
        ),
        (
            pdf_bytes(replacements={"up 0.7 percent": "up 0.6 percent"}),
            "press-release facts",
        ),
        (
            pdf_bytes(replacements={"weighted, repeat-sales": "unweighted, single-sale"}),
            "press-release facts",
        ),
        (
            pdf_bytes(replacements={"Jan 20 - Feb 20 0.7%": "Jan 20 - Feb 20 0.6%"}),
            "monthly table values",
        ),
        (
            pdf_bytes(replacements={"February-20 287.0": "February-20 286.9"}),
            "monthly table values",
        ),
        (
            pdf_bytes(replacements={"(Previous Estimate) 0.3%": "(Previous Estimate) 0.2%"}),
            "revision rows",
        ),
        (
            pdf_bytes(replacements={"An overview of the FHFA HPI": "Other overview"}),
            "methodology overview",
        ),
        (
            pdf_bytes(replacements={"Public releases occur at 9AM ET": "occur at noon"}),
            "schedule footer",
        ),
    ],
)
def test_pdf_structure_identity_values_revisions_and_method_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(report_content=content).fetch()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xff\xfe", "not valid UTF-8"),
        (
            schedule_bytes(replacements={"08/20/2019": "08/21/2019"}),
            "schedule facts",
        ),
        (
            schedule_bytes(replacements={"9 a.m. ET": "10 a.m. ET"}),
            "schedule facts",
        ),
        (
            schedule_bytes(
                noise=(
                    "<p>will be released at 9 a.m. ET on the following dates in 2020</p>"
                )
            ),
            "not unique",
        ),
    ],
)
def test_schedule_encoding_facts_and_uniqueness_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(schedule_content=content).fetch()


def test_content_types_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="schedule content type"):
        adapter(schedule_content_type="application/json").fetch()
    with pytest.raises(SourceSchemaError, match="report content type"):
        adapter(report_content_type="text/html").fetch()


def test_prepublication_retrieval_fails_closed() -> None:
    report = pdf_bytes()
    schedule = schedule_bytes()
    spec = _VERIFIED_RELEASES[RELEASE_DATE]
    snapshots = {
        SCHEDULE_URL: HttpResponseSnapshot(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            request_url=SCHEDULE_URL,
            content=schedule,
        ),
        spec.report_url: HttpResponseSnapshot(
            status_code=200,
            headers={"Content-Type": "application/pdf"},
            request_url=spec.report_url,
            content=report,
        ),
    }

    class EarlyClient:
        def get(
            self,
            url: str,
            **__: Any,
        ) -> tuple[HttpResponseSnapshot, bytes, datetime]:
            snapshot = snapshots[url]
            return snapshot, snapshot.content, datetime(2020, 4, 22, 12, 59, 59, tzinfo=UTC)

    early = FHFAHPIArchiveAdapter(
        cast(SafeHttpClient, EarlyClient()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
