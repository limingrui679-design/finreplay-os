from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import DOLWeeklyClaimsArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 3, 19)
LAST_MODIFIED = "Thu, 19 Mar 2020 12:29:55 GMT"
ANNUAL_MARKER = (
    "This week's release reflects the annual revision to the weekly unemployment claims "
    "seasonal adjustment factors."
)
TECHNICAL_MARKERS = (
    "TECHNICAL NOTES",
    "This news release presents the weekly unemployment insurance (UI) claims reported by "
    "each state's unemployment insurance program offices.",
    "These data come from ETA 538, Advance Weekly Initial and Continued Claims Report.",
    "The following week initial claims and continued claims are revised based on a second "
    "reporting by states.",
    "U.S. Department of Labor Employment and Training Administration",
)


def pdf_bytes(
    *,
    release_date: str = "March 19, 2020",
    weekday: str = "Thursday",
    week_ending: str = "March 14",
    value: str = "281,000",
    direction: str = "increase",
    change: str = "70,000",
    prior_status: str = "unrevised",
    inline_prior: str | None = "211,000",
    revision_sentence: str | None = None,
    release_number: str = "USDL 20-480-NAT",
    annual_marker: bool = True,
    duplicate_embargo: bool = False,
    duplicate_headline: bool = False,
    duplicate_number: bool = False,
    pages: int = 9,
    first_identity: str = "UNEMPLOYMENT INSURANCE WEEKLY CLAIMS",
    technical_marker: str = "ETA 538, Advance Weekly Initial and Continued Claims Report",
    blank_first: bool = False,
    blank_final: bool = False,
) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    prior = f" of {inline_prior}" if inline_prior is not None else ""
    embargo = (
        "TRANSMISSION OF MATERIALS IN THIS RELEASE IS EMBARGOED UNTIL "
        f"8:30 A.M. (Eastern) {weekday}, {release_date}"
    )
    headline = (
        f"In the week ending {week_ending}, the advance figure for seasonally adjusted "
        f"initial claims was {value}, an {direction} of {change} from the previous week's "
        f"{prior_status} level{prior}."
    )
    if direction == "decrease":
        headline = headline.replace(", an decrease", ", a decrease")
    first_lines = ["News Release", embargo, first_identity, "SEASONALLY ADJUSTED DATA"]
    if annual_marker:
        first_lines.append(ANNUAL_MARKER)
    first_lines.append(headline)
    if revision_sentence is not None:
        first_lines.append(revision_sentence)
    first_lines.append("The 4-week moving average was 232,250.")
    if duplicate_embargo:
        first_lines.append(embargo)
    if duplicate_headline:
        first_lines.append(headline)
    final_lines = [
        TECHNICAL_MARKERS[0],
        TECHNICAL_MARKERS[1],
        f"These data come from {technical_marker}.",
        TECHNICAL_MARKERS[3],
        TECHNICAL_MARKERS[4],
        f"Release Number: {release_number}",
    ]
    if duplicate_number:
        final_lines.append(f"Release Number: {release_number}")
    if pages > 0 and not blank_first:
        _write_page_text(writer, 0, first_lines)
    if pages > 8 and not blank_final:
        _write_page_text(writer, 8, final_lines)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _write_page_text(writer: PdfWriter, page_number: int, lines: list[str]) -> None:
    page = writer.pages[page_number]
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
    commands = ["BT", "/F1 8 Tf", "36 750 Td", "11 TL"]
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
    content_type: str = "application/pdf",
    last_modified: str | None = LAST_MODIFIED,
) -> httpx.Response:
    headers = {"Content-Type": content_type}
    if last_modified is not None:
        headers["Last-Modified"] = last_modified
    return httpx.Response(200, content=content, headers=headers, request=request)


def adapter(
    *,
    content: bytes | None = None,
    release_date: date = RELEASE_DATE,
    content_type: str = "application/pdf",
    last_modified: str | None = LAST_MODIFIED,
) -> DOLWeeklyClaimsArchiveAdapter:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: response(
                request,
                content if content is not None else pdf_bytes(),
                content_type=content_type,
                last_modified=last_modified,
            )
        )
    )
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return DOLWeeklyClaimsArchiveAdapter(safe, release_date=release_date)


def test_archived_claims_release_is_exact_versioned_and_knowledge_safe() -> None:
    content = pdf_bytes()
    batch = adapter(content=content).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("20200319:seasonally_adjusted_initial_claims")
    assert record.entity_id == "dol_ui_claims:united_states"
    assert record.source.sha256 == hashlib.sha256(content).hexdigest()
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.vintage_as_of == datetime(2020, 3, 19, 12, 29, 55, tzinfo=UTC)
    assert record.interval.valid_from == datetime(2020, 3, 14, tzinfo=UTC)
    assert record.interval.published_at == datetime(2020, 3, 19, 12, 30, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 3, 19, 12, 30, tzinfo=UTC)
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_persons"] == 281_000
    assert record.payload["prior_level_persons"] == 211_000
    assert record.payload["reported_change_persons"] == 70_000
    assert record.payload["prior_level_status"] == "unrevised"
    assert record.payload["annual_revision_release"] is True
    assert record.payload["arithmetic_verified"] is True
    assert batch.receipts[0].historical_replay_eligible is True
    assert batch.receipts[0].record_count == 1
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2020, 3, 19, 12, 29, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2020, 3, 19, 12, 30, tzinfo=UTC)) == [record]


@pytest.mark.parametrize(
    ("release_date", "value", "prior", "change", "available_at", "revision"),
    [
        (
            date(2020, 3, 12),
            211_000,
            215_000,
            -4_000,
            datetime(2020, 3, 12, 12, 30, 10, tzinfo=UTC),
            -1_000,
        ),
        (
            date(2020, 3, 19),
            281_000,
            211_000,
            70_000,
            datetime(2020, 3, 19, 12, 30, tzinfo=UTC),
            None,
        ),
        (
            date(2020, 3, 26),
            3_283_000,
            282_000,
            3_001_000,
            datetime(2020, 3, 26, 12, 46, 21, tzinfo=UTC),
            1_000,
        ),
    ],
)
def test_verified_release_calendar_parses_each_snapshot_without_overwriting_prior(
    release_date: date,
    value: int,
    prior: int,
    change: int,
    available_at: datetime,
    revision: int | None,
) -> None:
    if release_date == date(2020, 3, 12):
        content = pdf_bytes(
            release_date="March 12, 2020",
            week_ending="March 7",
            value="211,000",
            direction="decrease",
            change="4,000",
            prior_status="revised",
            inline_prior=None,
            revision_sentence=(
                "The previous week's level was revised down by 1,000 from 216,000 to 215,000."
            ),
            release_number="USDL 20-432-NAT",
            annual_marker=False,
        )
        last_modified = "Thu, 12 Mar 2020 12:30:10 GMT"
    elif release_date == date(2020, 3, 26):
        content = pdf_bytes(
            release_date="March 26, 2020",
            week_ending="March 21",
            value="3,283,000",
            change="3,001,000",
            prior_status="revised",
            inline_prior=None,
            revision_sentence=(
                "The previous week's level was revised up by 1,000 from 281,000 to 282,000."
            ),
            release_number="USDL 20-510-NAT",
            annual_marker=False,
        )
        last_modified = "Thu, 26 Mar 2020 12:46:21 GMT"
    else:
        content = pdf_bytes()
        last_modified = LAST_MODIFIED
    record = adapter(
        content=content,
        release_date=release_date,
        last_modified=last_modified,
    ).fetch().records[0]
    assert record.payload["value_persons"] == value
    assert record.payload["prior_level_persons"] == prior
    assert record.payload["reported_change_persons"] == change
    assert record.payload["prior_level_revision_delta_persons"] == revision
    assert record.interval.available_at == available_at


def test_verified_calendar_and_response_url_are_closed() -> None:
    client = cast(SafeHttpClient, object())
    for release_date, filename in (
        (date(2020, 3, 12), "eta20200432.pdf"),
        (date(2020, 3, 19), "20200480.pdf"),
        (date(2020, 3, 26), "20200510.pdf"),
    ):
        item = DOLWeeklyClaimsArchiveAdapter(client, release_date=release_date)
        assert item.endpoint.endswith(filename)
    with pytest.raises(ValueError, match="verified DOL weekly-claims calendar"):
        DOLWeeklyClaimsArchiveAdapter(client, release_date=date(2020, 4, 2))
    item = DOLWeeklyClaimsArchiveAdapter(client, release_date=RELEASE_DATE)
    for invalid in (
        "http://www.dol.gov/sites/dolgov/files/OPA/newsreleases/ui-claims/20200480.pdf",
        "https://evil.example/sites/dolgov/files/OPA/newsreleases/ui-claims/20200480.pdf",
        "https://www.dol.gov/sites/dolgov/files/OPA/newsreleases/ui-claims/other.pdf",
        "https://www.dol.gov/sites/dolgov/files/OPA/newsreleases/ui-claims/20200480.pdf?q=1",
    ):
        with pytest.raises(SourceSchemaError, match="response URL"):
            item._validate_response_url(invalid)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (pdf_bytes(pages=8), "exactly nine pages"),
        (pdf_bytes(blank_first=True), "first page has no extractable text"),
        (pdf_bytes(blank_final=True), "technical-notes page has no text"),
        (pdf_bytes(first_identity="OTHER RELEASE"), "first-page identity"),
        (pdf_bytes(technical_marker="OTHER REPORT"), "technical notes"),
        (pdf_bytes(duplicate_embargo=True), "exactly one embargo"),
        (pdf_bytes(duplicate_headline=True), "exactly one initial-claims headline"),
        (pdf_bytes(duplicate_number=True), "exactly one release number"),
        (pdf_bytes(release_date="March 18, 2020"), "release date does not match"),
        (pdf_bytes(weekday="Wednesday"), "weekday does not match"),
        (pdf_bytes(release_number="USDL 20-999-NAT"), "release number"),
        (pdf_bytes(week_ending="March 7"), "week ending"),
        (pdf_bytes(annual_marker=False), "annual-revision marker"),
        (pdf_bytes(value="281,001"), "arithmetic does not reconcile"),
        (pdf_bytes(value="0"), "headline value must be a positive integer"),
    ],
)
def test_pdf_identity_timing_arithmetic_and_revision_semantics_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(content=content).fetch()


@pytest.mark.parametrize(
    ("revision_sentence", "message"),
    [
        (None, "exactly one revised prior claims level"),
        (
            "The previous week's level was revised up by 2,000 from 281,000 to 282,000.",
            "revision does not reconcile",
        ),
        (
            "The previous week's level was revised up by 1,000 from 281,000 to 282,000. "
            "The previous week's level was revised up by 1,000 from 281,000 to 282,000.",
            "exactly one revised prior claims level",
        ),
    ],
)
def test_prior_week_revision_evidence_must_be_unique_and_reconcile(
    revision_sentence: str | None,
    message: str,
) -> None:
    content = pdf_bytes(
        release_date="March 26, 2020",
        week_ending="March 21",
        value="3,283,000",
        change="3,001,000",
        prior_status="revised",
        inline_prior=None,
        revision_sentence=revision_sentence,
        release_number="USDL 20-510-NAT",
        annual_marker=False,
    )
    with pytest.raises(SourceSchemaError, match=message):
        adapter(
            content=content,
            release_date=date(2020, 3, 26),
            last_modified="Thu, 26 Mar 2020 12:46:21 GMT",
        ).fetch()


@pytest.mark.parametrize(
    ("last_modified", "message"),
    [
        (None, "lacks Last-Modified"),
        ("not-a-date", "is invalid"),
        ("Thu, 19 Mar 2020 12:29:55", "lacks a timezone"),
        ("Wed, 18 Mar 2020 12:29:55 GMT", "outside the verified release date"),
    ],
)
def test_last_modified_must_be_official_aware_and_same_day(
    last_modified: str | None,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(last_modified=last_modified).fetch()


def test_content_type_and_future_retrieval_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="unexpected DOL weekly-claims content type"):
        adapter(content_type="text/html").fetch()

    content = pdf_bytes()
    snapshot = HttpResponseSnapshot(
        status_code=200,
        headers={"Content-Type": "application/pdf", "Last-Modified": LAST_MODIFIED},
        request_url=(
            "https://www.dol.gov/sites/dolgov/files/OPA/newsreleases/ui-claims/20200480.pdf"
        ),
        content=content,
    )

    class EarlyClient:
        def get(self, *_: Any, **__: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
            return snapshot, content, datetime(2020, 3, 19, 12, 29, 59, tzinfo=UTC)

    early = DOLWeeklyClaimsArchiveAdapter(
        cast(SafeHttpClient, EarlyClient()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
