from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

import finreplay.adapters.eia_wngsr as module
from finreplay.adapters import EIAWNGSRWorkingGasHistoryAdapter, SourceSchemaError
from finreplay.adapters.base import AdapterError, HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

OLE = bytes.fromhex("d0cf11e0a1b11ae1")
REVISIONS = OLE + b"REVISIONS"
HISTORY = OLE + b"HISTORY"
RETRIEVED = datetime(2026, 8, 14, 8, tzinfo=UTC)
LAST_MODIFIED = "Thu, 13 Aug 2026 13:37:22 GMT"
RELEASE_DATES = (date(2020, 3, 12), date(2020, 3, 19), date(2020, 3, 26))
ROWS = (
    (43896.0, "EIA-912", 426.0, 529.0, 97.0, 200.0, 791.0, 235.0, 556.0, 2043.0),
    (43903.0, "EIA-912", 412.0, 512.0, 96.0, 199.0, 814.0, 247.0, 568.0, 2034.0),
    (43910.0, "EIA-912", 398.0, 492.0, 92.0, 194.0, 829.0, 258.0, 571.0, 2005.0),
)


class FakeSheet:
    def __init__(self, rows: list[list[Any]], ncols: int) -> None:
        self._rows = rows
        self.nrows = len(rows)
        self.ncols = ncols

    def cell_value(self, row: int, column: int) -> Any:
        return self._rows[row][column]

    def row_values(self, row: int, start: int, end: int) -> list[Any]:
        return self._rows[row][start:end]


class FakeBook:
    datemode = 0

    def __init__(self, sheets: dict[str, FakeSheet]) -> None:
        self._sheets = sheets

    def sheet_names(self) -> list[str]:
        return list(self._sheets)

    def sheet_by_name(self, name: str) -> FakeSheet:
        return self._sheets[name]


def fake_books(
    *,
    revision_note: str = "",
    history_delta: int = 0,
    bad_source: bool = False,
    duplicate_revision_week: bool = False,
) -> tuple[FakeBook, FakeBook]:
    revision_rows = [[""] * 11 for _ in range(253)]
    revision_rows[0][0] = (
        "1. Beginning in November 2015, the file will be updated with the original estimate "
        "prior to the revision or reclassification and the accompanying note."
    )
    revision_rows[1] = [
        "Week ending",
        "Source",
        "East Region",
        "Midwest Region",
        "Mountain Region",
        "Pacific Region",
        "South Central Region",
        "Salt",
        "NonSalt",
        "Total Lower 48",
        "Explanation",
    ]
    for position, raw in enumerate(ROWS, start=2):
        revision_rows[position] = [*raw, revision_note if position == 3 else ""]
    if bad_source:
        revision_rows[2][1] = "OTHER"
    if duplicate_revision_week:
        revision_rows[5] = [*ROWS[0], ""]

    history_rows = [[""] * 10 for _ in range(542)]
    history_rows[6] = [
        "Week ending",
        "Source",
        "East Region",
        "Midwest Region",
        "Mountain Region",
        "Pacific Region",
        "South Central Region",
        "Salt",
        "NonSalt",
        "Total Lower 48",
    ]
    for position, raw in enumerate(ROWS, start=7):
        history_rows[position] = list(raw)
    history_rows[8][9] += history_delta
    net_rows = [[""] * 10 for _ in range(542)]
    return (
        FakeBook({"original_data": FakeSheet(revision_rows, 11)}),
        FakeBook(
            {
                "html_report_history": FakeSheet(history_rows, 10),
                "weekly_net_changes": FakeSheet(net_rows, 10),
            }
        ),
    )


def install_fake_xlrd(
    monkeypatch: pytest.MonkeyPatch,
    *,
    revision_note: str = "",
    history_delta: int = 0,
    bad_source: bool = False,
    duplicate_revision_week: bool = False,
) -> None:
    revision_book, history_book = fake_books(
        revision_note=revision_note,
        history_delta=history_delta,
        bad_source=bad_source,
        duplicate_revision_week=duplicate_revision_week,
    )

    def open_workbook(*, file_contents: bytes, on_demand: bool) -> FakeBook:
        assert on_demand is True
        if file_contents == REVISIONS:
            return revision_book
        if file_contents == HISTORY:
            return history_book
        raise AssertionError("unexpected workbook")

    monkeypatch.setattr(module.xlrd, "open_workbook", open_workbook)


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
    commands = ["BT", "/F1 7 Tf", "30 750 Td", "10 TL"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.extend((f"({escaped}) Tj", "T*"))
    commands.append("ET")
    stream = StreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)


def evaluation_pdf(
    *,
    pages: int = 24,
    schedule: str = (
        "EIA releases the WNGSR each Thursday at 10:30 a.m. ET. From 2020 to 2022, EIA "
        "released the WNGSR every week according to the established schedule."
    ),
    remote: str = (
        "The remote telework posture did not disrupt WNGSR collection and publication. "
        "March 19, 2020. The procedures have not resulted in any delayed releases or data "
        "breaches of WNGSR."
    ),
    march13_cv: str = "0.5%",
    blank_page: int | None = None,
) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if pages >= 8 and blank_page != 8:
        _write_page_text(writer, 7, [remote])
    if pages >= 13 and blank_page != 13:
        _write_page_text(writer, 12, [schedule])
    if pages >= 15 and blank_page != 15:
        _write_page_text(
            writer,
            14,
            [
                "Table A4. Estimated coefficients of variation",
                "06-Mar-20 1.7% 0.7% 1.2% 0.0% 0.9% 1.9% 0.9% 0.5%",
                f"13-Mar-20 1.7% 0.8% 1.3% 0.0% 0.8% 1.8% 0.9% {march13_cv}",
                "20-Mar-20 1.8% 0.8% 1.5% 0.0% 0.8% 1.7% 0.9% 0.5%",
            ],
        )
    if pages >= 19 and blank_page != 19:
        _write_page_text(
            writer,
            18,
            [
                "Table A8. Estimated standard errors",
                "06-Mar-20 0.2 0.5 0.1 0.0 0.3 0.2 0.2 0.6",
                "13-Mar-20 0.2 0.6 0.1 0.0 0.5 0.3 0.4 0.8",
                "20-Mar-20 0.2 0.4 0.1 0.0 0.7 0.2 0.6 0.8",
            ],
        )
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class TripletClient:
    def __init__(
        self,
        *,
        revisions: bytes = REVISIONS,
        history: bytes = HISTORY,
        evaluation: bytes | None = None,
        content_types: tuple[str, str, str] = (
            "application/vnd.ms-excel",
            "application/vnd.ms-excel",
            "application/pdf",
        ),
        last_modified: str | None = LAST_MODIFIED,
        url_suffix: str = "",
    ) -> None:
        self.contents = (revisions, history, evaluation or evaluation_pdf())
        self.content_types = content_types
        self.last_modified = last_modified
        self.url_suffix = url_suffix
        self.calls = 0

    def get_same_host_signed_redirect(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
        expected_redirect_path: str,
    ) -> tuple[HttpResponseSnapshot, bytes, datetime]:
        assert allowed_hosts == ("ir.eia.gov",)
        assert expected_redirect_path.startswith("/secure/ngs/")
        return self._response(url)

    def get(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
    ) -> tuple[HttpResponseSnapshot, bytes, datetime]:
        assert allowed_hosts == ("ir.eia.gov",)
        return self._response(url)

    def _response(self, url: str) -> tuple[HttpResponseSnapshot, bytes, datetime]:
        position = self.calls
        self.calls += 1
        content = self.contents[position]
        headers = {"Content-Type": self.content_types[position]}
        if self.last_modified is not None:
            headers["Last-Modified"] = self.last_modified
        snapshot = HttpResponseSnapshot(
            status_code=200,
            headers=headers,
            request_url=url + (self.url_suffix if position == 0 else ""),
            content=content,
        )
        return snapshot, content, RETRIEVED


def adapter(client: TripletClient | None = None) -> EIAWNGSRWorkingGasHistoryAdapter:
    return EIAWNGSRWorkingGasHistoryAdapter(
        cast(SafeHttpClient, client or TripletClient())
    )


def test_live_contract_recovers_original_values_and_exact_release_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_xlrd(monkeypatch)
    batch = adapter().fetch()

    assert len(batch.records) == 3
    assert len(batch.receipts) == 3
    assert len(batch.artifacts) == 3
    assert [record.payload["value_bcf"] for record in batch.records] == [2043, 2034, 2005]
    assert [record.payload["reported_net_change_bcf"] for record in batch.records] == [
        -48,
        -9,
        -29,
    ]
    assert [record.payload["five_region_rounding_difference_bcf"] for record in batch.records] == [
        0,
        -1,
        0,
    ]
    assert [record.interval.available_at for record in batch.records] == [
        datetime(2020, 3, 12, 14, 30, tzinfo=UTC),
        datetime(2020, 3, 19, 14, 30, tzinfo=UTC),
        datetime(2020, 3, 26, 14, 30, tzinfo=UTC),
    ]
    assert all(
        record.source.temporal_coverage is TemporalCoverage.VINTAGE_NATIVE
        and record.source.vintage_as_of == record.interval.available_at
        and record.source.license_class is LicenseClass.DOWNLOAD_ONLY
        and record.evidence_class is EvidenceClass.REPORTED
        and record.payload["current_history_matches_original_estimate"] is True
        and record.payload["statistical_measures_define_finreplay_range"] is False
        for record in batch.records
    )
    assert [receipt.record_count for receipt in batch.receipts] == [3, 0, 0]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert {receipt.response_sha256 for receipt in batch.receipts} == {
        hashlib.sha256(REVISIONS).hexdigest(),
        hashlib.sha256(HISTORY).hexdigest(),
        hashlib.sha256(batch.artifacts[2].content).hexdigest(),
    }

    with TimeVault() as vault:
        vault.append(batch.records)
        assert len(vault.records_as_of(datetime(2020, 3, 19, 14, 29, tzinfo=UTC))) == 1
        assert len(vault.records_as_of(datetime(2020, 3, 19, 14, 30, tzinfo=UTC))) == 2


def test_release_date_allowlist_is_strict() -> None:
    client = cast(SafeHttpClient, object())
    with pytest.raises(ValueError, match="unique and chronological"):
        EIAWNGSRWorkingGasHistoryAdapter(
            client,
            release_dates=(date(2020, 3, 19), date(2020, 3, 12)),
        )
    with pytest.raises(ValueError, match="verified EIA WNGSR calendar"):
        EIAWNGSRWorkingGasHistoryAdapter(client, release_dates=(date(2020, 4, 2),))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"revision_note": "published revision"}, "published revision note"),
        ({"history_delta": 1}, "selected working-gas values changed"),
        ({"bad_source": True}, "source identity"),
        ({"duplicate_revision_week": True}, "duplicates a selected week"),
    ],
)
def test_workbook_semantics_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, Any],
    message: str,
) -> None:
    install_fake_xlrd(monkeypatch, **kwargs)
    with pytest.raises(SourceSchemaError, match=message):
        adapter().fetch()


@pytest.mark.parametrize(
    ("evaluation", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (evaluation_pdf(pages=23), "must have 24 pages"),
        (evaluation_pdf(blank_page=13), "required evaluation page has no text"),
        (evaluation_pdf(schedule="Other schedule"), "release-schedule evidence"),
        (evaluation_pdf(remote="Other remote process"), "remote-publication evidence"),
        (evaluation_pdf(march13_cv="0.6%"), "coefficient of variation changed"),
    ],
)
def test_evaluation_identity_schedule_and_statistics_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    evaluation: bytes,
    message: str,
) -> None:
    install_fake_xlrd(monkeypatch)
    with pytest.raises(SourceSchemaError, match=message):
        adapter(TripletClient(evaluation=evaluation)).fetch()


def test_response_metadata_and_urls_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_xlrd(monkeypatch)
    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(
            TripletClient(
                content_types=("text/html", "application/vnd.ms-excel", "application/pdf")
            )
        ).fetch()
    with pytest.raises(SourceSchemaError, match="lacks Last-Modified"):
        adapter(TripletClient(last_modified=None)).fetch()
    with pytest.raises(SourceSchemaError, match="in the future"):
        adapter(TripletClient(last_modified="Sat, 15 Aug 2026 13:37:22 GMT")).fetch()
    with pytest.raises(SourceSchemaError, match="response URL"):
        adapter(TripletClient(url_suffix="?unexpected=1")).fetch()


def test_real_xls_guard_rejects_non_ole_and_malformed_ole() -> None:
    with pytest.raises(SourceSchemaError, match="not an XLS"):
        module.EIAWNGSRWorkingGasHistoryAdapter._open_workbook(b"bad", kind="history")
    with pytest.raises(SourceSchemaError, match="could not be parsed"):
        module.EIAWNGSRWorkingGasHistoryAdapter._open_workbook(
            OLE + b"not-a-workbook",
            kind="history",
        )


def test_same_host_signed_redirect_is_narrow_and_preserves_canonical_url() -> None:
    canonical = "https://ir.eia.gov/ngs/revisions.xls"
    signed = (
        "https://ir.eia.gov/secure/ngs/revisions.xls?"
        "Policy=p&Signature=s&Key-Pair-Id=k"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ngs/revisions.xls":
            return httpx.Response(302, headers={"Location": signed}, request=request)
        return httpx.Response(
            200,
            content=REVISIONS,
            headers={"Content-Type": "application/vnd.ms-excel"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    safe = SafeHttpClient(user_agent="test@example.invalid", client=client)
    snapshot, content, _retrieved = safe.get_same_host_signed_redirect(
        canonical,
        allowed_hosts=("ir.eia.gov",),
        expected_redirect_path="/secure/ngs/revisions.xls",
    )
    assert snapshot.request_url == canonical
    assert content == REVISIONS


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.invalid/secure/ngs/revisions.xls?Policy=p&Signature=s&Key-Pair-Id=k",
        "https://ir.eia.gov/secure/ngs/other.xls?Policy=p&Signature=s&Key-Pair-Id=k",
        "https://ir.eia.gov/secure/ngs/revisions.xls?Policy=p&Signature=s",
        (
            "https://ir.eia.gov/secure/ngs/revisions.xls?"
            "Policy=p&Signature=s&Key-Pair-Id=k&extra=1"
        ),
        "https://ir.eia.gov/secure/ngs/revisions.xls?Policy=p&Signature=&Key-Pair-Id=k",
    ],
)
def test_same_host_signed_redirect_rejects_unapproved_targets(location: str) -> None:
    canonical = "https://ir.eia.gov/ngs/revisions.xls"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": location}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    safe = SafeHttpClient(user_agent="test@example.invalid", client=client)
    with pytest.raises(AdapterError):
        safe.get_same_host_signed_redirect(
            canonical,
            allowed_hosts=("ir.eia.gov",),
            expected_redirect_path="/secure/ngs/revisions.xls",
        )
