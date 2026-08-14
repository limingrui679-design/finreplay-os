from __future__ import annotations

import copy
import csv
import hashlib
import json
import zipfile
from datetime import UTC, datetime
from io import BytesIO, StringIO
from typing import Any, cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from finreplay.adapters import CFTCTFFScheduledReleaseAdapter, SourceSchemaError
from finreplay.adapters.base import HttpResponseSnapshot, SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

SCHEDULE_MARKERS = (
    "The Commitments of Traders reports are released at 3:30 p.m. Eastern time.",
    "The release usually includes data from the previous Tuesday.",
    "The following is a tentative schedule of releases through 2026.",
    "2026 Release Schedule",
    "July 06* 10 17 24 31",
)
POLICY_MARKERS = (
    "provide a breakdown of each Tuesday's open interest",
    "the actual trader category or classification is based on the predominant business purpose "
    "self-reported by traders on the CFTC Form 40",
    "CFTC staff does not know specific reasons for traders' positions",
    "Generally speaking, there are three ways that a change in reported positions in the COT "
    "Report can happen:",
    "A new trader has submitted a Form 40",
    "An existing trader has left the market",
    "There is not a list of historical release dates",
    "No, historical data is not updated once published.",
)
NOTES_MARKERS = (
    "TRADERS IN FINANCIAL FUTURES Explanatory Notes",
    "Dealer/Intermediary; Asset Manager/Institutional; Leveraged Funds; and Other Reportables.",
    '"Spreading" is a computed amount equal to offsetting long and short positions held by a '
    "trader.",
    "The sum of the numbers of traders in each separate category typically exceeds the total "
    "number of reportable traders.",
    "staff classifies traders, not their trading activity",
    "cannot know with certainty that all of that trader's activity is speculative",
)

ANNUAL_TO_API = {
    "Market_and_Exchange_Names": "market_and_exchange_names",
    "Report_Date_as_YYYY-MM-DD": "report_date_as_yyyy_mm_dd",
    "CFTC_Contract_Market_Code": "cftc_contract_market_code",
    "CFTC_Market_Code": "cftc_market_code",
    "Open_Interest_All": "open_interest_all",
    "Dealer_Positions_Long_All": "dealer_positions_long_all",
    "Dealer_Positions_Short_All": "dealer_positions_short_all",
    "Dealer_Positions_Spread_All": "dealer_positions_spread_all",
    "Asset_Mgr_Positions_Long_All": "asset_mgr_positions_long",
    "Asset_Mgr_Positions_Short_All": "asset_mgr_positions_short",
    "Asset_Mgr_Positions_Spread_All": "asset_mgr_positions_spread",
    "Lev_Money_Positions_Long_All": "lev_money_positions_long",
    "Lev_Money_Positions_Short_All": "lev_money_positions_short",
    "Lev_Money_Positions_Spread_All": "lev_money_positions_spread",
    "Other_Rept_Positions_Long_All": "other_rept_positions_long",
    "Other_Rept_Positions_Short_All": "other_rept_positions_short",
    "Other_Rept_Positions_Spread_All": "other_rept_positions_spread",
    "Tot_Rept_Positions_Long_All": "tot_rept_positions_long_all",
    "Tot_Rept_Positions_Short_All": "tot_rept_positions_short",
    "NonRept_Positions_Long_All": "nonrept_positions_long_all",
    "NonRept_Positions_Short_All": "nonrept_positions_short_all",
    "Change_in_Open_Interest_All": "change_in_open_interest_all",
    "Change_in_Asset_Mgr_Long_All": "change_in_asset_mgr_long",
    "Change_in_Asset_Mgr_Short_All": "change_in_asset_mgr_short",
    "Change_in_Asset_Mgr_Spread_All": "change_in_asset_mgr_spread",
    "Traders_Tot_All": "traders_tot_all",
    "Traders_Asset_Mgr_Long_All": "traders_asset_mgr_long_all",
    "Traders_Asset_Mgr_Short_All": "traders_asset_mgr_short_all",
    "Traders_Asset_Mgr_Spread_All": "traders_asset_mgr_spread",
    "Contract_Units": "contract_units",
    "FutOnly_or_Combined": "futonly_or_combined",
}


def _common_row(*, identity: str, report_date: str, report_week: str) -> dict[str, str]:
    return {
        "id": identity,
        "market_and_exchange_names": "UST 2Y NOTE - CHICAGO BOARD OF TRADE",
        "report_date_as_yyyy_mm_dd": f"{report_date}T00:00:00.000",
        "yyyy_report_week_ww": report_week,
        "contract_market_name": "UST 2Y NOTE",
        "cftc_contract_market_code": "042601",
        "cftc_market_code": "CBT",
        "commodity_name": "T-NOTES, 1-2 YEAR",
        "commodity": "T-NOTES, 1-2 YEAR",
        "commodity_subgroup_name": "Interest Rates - U.S. Treasury",
        "commodity_group_name": "FINANCIAL INSTRUMENTS",
        "contract_units": "(CONTRACTS OF $200,000 FACE VALUE)",
        "futonly_or_combined": "FutOnly",
    }


def api_rows() -> list[dict[str, str]]:
    first = {
        **_common_row(
            identity="260714042601F",
            report_date="2026-07-14",
            report_week="2026 Report Week 28",
        ),
        "open_interest_all": "4465199",
        "dealer_positions_long_all": "120633",
        "dealer_positions_short_all": "579515",
        "dealer_positions_spread_all": "30735",
        "asset_mgr_positions_long": "2448262",
        "asset_mgr_positions_short": "575447",
        "asset_mgr_positions_spread": "548339",
        "lev_money_positions_long": "389534",
        "lev_money_positions_short": "2061300",
        "lev_money_positions_spread": "305388",
        "other_rept_positions_long": "394485",
        "other_rept_positions_short": "209869",
        "other_rept_positions_spread": "2003",
        "tot_rept_positions_long_all": "4239379",
        "tot_rept_positions_short": "4312596",
        "nonrept_positions_long_all": "225820",
        "nonrept_positions_short_all": "152603",
        "change_in_open_interest_all": "4262",
        "change_in_asset_mgr_long": "-27675",
        "change_in_asset_mgr_short": "-8497",
        "change_in_asset_mgr_spread": "322",
        "traders_tot_all": "506",
        "traders_asset_mgr_long_all": "161",
        "traders_asset_mgr_short_all": "64",
        "traders_asset_mgr_spread": "108",
    }
    second = {
        **_common_row(
            identity="260721042601F",
            report_date="2026-07-21",
            report_week="2026 Report Week 29",
        ),
        "open_interest_all": "4335075",
        "dealer_positions_long_all": "116660",
        "dealer_positions_short_all": "580373",
        "dealer_positions_spread_all": "27723",
        "asset_mgr_positions_long": "2407459",
        "asset_mgr_positions_short": "586571",
        "asset_mgr_positions_spread": "546120",
        "lev_money_positions_long": "358297",
        "lev_money_positions_short": "1953425",
        "lev_money_positions_spread": "265129",
        "other_rept_positions_long": "390094",
        "other_rept_positions_short": "218932",
        "other_rept_positions_spread": "1985",
        "tot_rept_positions_long_all": "4113467",
        "tot_rept_positions_short": "4180258",
        "nonrept_positions_long_all": "221608",
        "nonrept_positions_short_all": "154817",
        "change_in_open_interest_all": "-130124",
        "change_in_asset_mgr_long": "-40803",
        "change_in_asset_mgr_short": "11124",
        "change_in_asset_mgr_spread": "-2219",
        "traders_tot_all": "499",
        "traders_asset_mgr_long_all": "162",
        "traders_asset_mgr_short_all": "58",
        "traders_asset_mgr_spread": "106",
    }
    third = {
        **_common_row(
            identity="260728042601F",
            report_date="2026-07-28",
            report_week="2026 Report Week 30",
        ),
        "open_interest_all": "4406588",
        "dealer_positions_long_all": "107074",
        "dealer_positions_short_all": "588944",
        "dealer_positions_spread_all": "28111",
        "asset_mgr_positions_long": "2425700",
        "asset_mgr_positions_short": "580688",
        "asset_mgr_positions_spread": "565815",
        "lev_money_positions_long": "456470",
        "lev_money_positions_short": "2020764",
        "lev_money_positions_spread": "234030",
        "other_rept_positions_long": "372079",
        "other_rept_positions_short": "219002",
        "other_rept_positions_spread": "2080",
        "tot_rept_positions_long_all": "4191359",
        "tot_rept_positions_short": "4239434",
        "nonrept_positions_long_all": "215229",
        "nonrept_positions_short_all": "167154",
        "change_in_open_interest_all": "71513",
        "change_in_asset_mgr_long": "18241",
        "change_in_asset_mgr_short": "-5883",
        "change_in_asset_mgr_spread": "19695",
        "traders_tot_all": "501",
        "traders_asset_mgr_long_all": "161",
        "traders_asset_mgr_short_all": "62",
        "traders_asset_mgr_spread": "107",
    }
    return [first, second, third]


def html_bytes(markers: tuple[str, ...]) -> bytes:
    body = "".join(f"<p>{marker}</p>" for marker in markers)
    return f"<!doctype html><html><body>{body}</body></html>".encode()


def notes_pdf(
    *,
    pages: int = 4,
    width: int = 612,
    height: int = 792,
    blank_page: int | None = None,
    replacements: dict[str, str] | None = None,
) -> bytes:
    lines = [[f"TFF explanatory content page {index + 1}"] for index in range(pages)]
    for index, marker in enumerate(NOTES_MARKERS):
        if index < 2:
            page_index = 0
        elif index < 4:
            page_index = 2
        else:
            page_index = 3
        if page_index < pages:
            lines[page_index].append(marker)
    if replacements:
        lines = [[_replace_all(line, replacements) for line in page_lines] for page_lines in lines]
    writer = PdfWriter()
    for index, page_lines in enumerate(lines):
        writer.add_blank_page(width=width, height=height)
        if index != blank_page:
            _write_page_text(writer, index, page_lines)
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
    commands = ["BT", "/F1 7 Tf", "20 770 Td", "10 TL"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.extend((f"({escaped}) Tj", "T*"))
    commands.append("ET")
    stream = StreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)


def annual_zip(
    rows: list[dict[str, str]] | None = None,
    *,
    member: str = "FinFutYY.txt",
    missing_header: str | None = None,
    extra_member: bool = False,
    raw_csv: bytes | None = None,
) -> bytes:
    selected = api_rows() if rows is None else rows
    if raw_csv is None:
        headers = [field for field in ANNUAL_TO_API if field != missing_header]
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        for source in selected:
            annual: dict[str, str] = {}
            for annual_field, api_field in ANNUAL_TO_API.items():
                if annual_field == missing_header:
                    continue
                value = source[api_field]
                if api_field == "report_date_as_yyyy_mm_dd":
                    value = value[:10]
                annual[annual_field] = value
            writer.writerow(annual)
        raw_csv = output.getvalue().encode()
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, raw_csv)
        if extra_member:
            archive.writestr("unexpected.txt", b"extra")
    return buffer.getvalue()


def adapter(
    *,
    rows: list[dict[str, str]] | None = None,
    annual: bytes | None = None,
    schedule: bytes | None = None,
    policy: bytes | None = None,
    notes: bytes | None = None,
    content_types: dict[str, str] | None = None,
) -> CFTCTFFScheduledReleaseAdapter:
    selected_rows = api_rows() if rows is None else rows
    payloads = {
        "api": json.dumps(selected_rows, separators=(",", ":")).encode(),
        "annual": annual if annual is not None else annual_zip(),
        "schedule": schedule if schedule is not None else html_bytes(SCHEDULE_MARKERS),
        "policy": policy if policy is not None else html_bytes(POLICY_MARKERS),
        "notes": notes if notes is not None else notes_pdf(),
    }
    types = {
        "api": "application/json;charset=utf-8",
        "annual": "application/zip",
        "schedule": "text/html;charset=utf-8",
        "policy": "text/html;charset=utf-8",
        "notes": "application/pdf",
    }
    if content_types:
        types.update(content_types)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/gpe5-46if.json"):
            label = "api"
        elif path.endswith("/fut_fin_txt_2026.zip"):
            label = "annual"
        elif "/ReleaseSchedule/" in path:
            label = "schedule"
        elif path.endswith("/CommitmentsofTraders/index.htm"):
            label = "policy"
        elif path.endswith("/tfmexplanatorynotes.pdf"):
            label = "notes"
        else:  # pragma: no cover - adapter URL regression guard
            raise AssertionError(request.url)
        return httpx.Response(
            200,
            content=payloads[label],
            headers={"Content-Type": types[label]},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return CFTCTFFScheduledReleaseAdapter(safe)


def test_exact_three_row_chain_is_crosschecked_and_knowledge_safe() -> None:
    source_rows = api_rows()
    source_annual = annual_zip()
    source_schedule = html_bytes(SCHEDULE_MARKERS)
    source_policy = html_bytes(POLICY_MARKERS)
    source_notes = notes_pdf()
    batch = adapter(
        rows=source_rows,
        annual=source_annual,
        schedule=source_schedule,
        policy=source_policy,
        notes=source_notes,
    ).fetch()

    assert len(batch.records) == 3
    assert len(batch.receipts) == 5
    assert len(batch.artifacts) == 5
    assert [receipt.record_count for receipt in batch.receipts] == [3, 0, 0, 0, 0]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert all(receipt.warnings for receipt in batch.receipts)
    assert {artifact.content for artifact in batch.artifacts} == {
        json.dumps(source_rows, separators=(",", ":")).encode(),
        source_annual,
        source_schedule,
        source_policy,
        source_notes,
    }

    expected = (
        ("2026-07-14", 4_465_199, datetime(2026, 7, 17, 19, 30, tzinfo=UTC)),
        ("2026-07-21", 4_335_075, datetime(2026, 7, 24, 19, 30, tzinfo=UTC)),
        ("2026-07-28", 4_406_588, datetime(2026, 7, 31, 19, 30, tzinfo=UTC)),
    )
    for record, (report_date, open_interest, release_at) in zip(
        batch.records, expected, strict=True
    ):
        assert record.entity_id == "cftc_contract:042601"
        assert record.interval.available_at == release_at
        assert record.interval.published_at == release_at
        assert record.interval.availability_confidence == 0.98
        assert record.source.vintage_as_of == release_at
        assert record.source.temporal_coverage is TemporalCoverage.IMMUTABLE_EVENT
        assert record.source.license_class is LicenseClass.REDISTRIBUTABLE
        assert record.source.sha256 == hashlib.sha256(batch.artifacts[0].content).hexdigest()
        assert record.evidence_class is EvidenceClass.REPORTED
        assert record.payload["report_date"] == report_date
        assert record.payload["open_interest_contracts"] == open_interest
        assert record.payload["api_annual_crosscheck_verified"] is True
        assert record.payload["actual_row_publication_log_available"] is False
        assert record.payload["schedule_self_describes_as_tentative"] is True
        assert record.payload["contract_face_value_notional_conversion_performed"] is False
        assert record.payload["unit"] == "Futures Contracts"

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2026, 7, 17, 19, 29, 59, tzinfo=UTC)) == []
        assert len(vault.records_as_of(datetime(2026, 7, 24, 19, 29, 59, tzinfo=UTC))) == 1
        assert len(vault.records_as_of(datetime(2026, 7, 24, 19, 30, tzinfo=UTC))) == 2
        assert len(vault.records_as_of(datetime(2026, 7, 31, 19, 30, tzinfo=UTC))) == 3


def test_boundary_values_and_classification_fields_are_preserved_without_notional() -> None:
    records = adapter().fetch().records
    assert [record.payload["reported_change_from_prior_week_contracts"] for record in records] == [
        4_262,
        -130_124,
        71_513,
    ]
    event = records[-1]
    positions = cast(dict[str, int], event.payload["position_breakdown_contracts"])
    assert positions["asset_mgr_positions_long"] == 2_425_700
    assert positions["lev_money_positions_short"] == 2_020_764
    assert positions["nonrept_positions_short_all"] == 167_154
    assert event.payload["contract_units_source_text"] == ("(CONTRACTS OF $200,000 FACE VALUE)")
    assert "notional" not in {key.lower() for key in event.payload if key == "notional"}


def test_dynamic_supporting_wrappers_do_not_mutate_identical_financial_facts() -> None:
    first = adapter().fetch()
    expanded_rows = api_rows()
    unrelated = copy.deepcopy(expanded_rows[0])
    unrelated["cftc_contract_market_code"] = "999999"
    expanded_rows.append(unrelated)
    second = adapter(
        annual=annual_zip(expanded_rows),
        schedule=html_bytes((*SCHEDULE_MARKERS, "dynamic schedule wrapper token")),
        policy=html_bytes((*POLICY_MARKERS, "dynamic policy wrapper token")),
    ).fetch()
    assert first.receipts[1].response_sha256 != second.receipts[1].response_sha256
    assert first.receipts[2].response_sha256 != second.receipts[2].response_sha256
    assert first.receipts[3].response_sha256 != second.receipts[3].response_sha256
    assert first.records[0].source.source_version == second.records[0].source.source_version

    with TimeVault() as vault:
        initial = vault.append(first.records)
        retry = vault.append(second.records)
    assert initial.inserted_records == 3
    assert retry.inserted_records == 0
    assert retry.idempotent_records == 3


@pytest.mark.parametrize(
    ("row_index", "field", "value", "message"),
    [
        (0, "id", "wrong", "source ID"),
        (1, "yyyy_report_week_ww", "wrong", "report-week"),
        (2, "cftc_contract_market_code", "999999", "field cftc_contract"),
        (0, "futonly_or_combined", "Combined", "mode is"),
        (1, "open_interest_all", "4335076", "long open-interest|changed"),
        (2, "change_in_open_interest_all", "71514", "changed"),
        (2, "traders_asset_mgr_long_all", "502", "changed|exceeds"),
    ],
)
def test_pinned_identity_mode_and_values_fail_closed(
    row_index: int,
    field: str,
    value: str,
    message: str,
) -> None:
    rows = api_rows()
    rows[row_index][field] = value
    with pytest.raises(SourceSchemaError, match=message):
        adapter(rows=rows).fetch()


def test_inherited_open_interest_and_classification_equations_fail_closed() -> None:
    rows = api_rows()
    rows[0]["dealer_positions_long_all"] = "120636"
    with pytest.raises(SourceSchemaError, match="long classification"):
        adapter(rows=rows).fetch()


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda rows: rows.pop(), "exactly three"),
        (lambda rows: rows[0].pop("contract_units"), "missing fields"),
        (
            lambda rows: rows[1].update(
                {"report_date_as_yyyy_mm_dd": rows[0]["report_date_as_yyyy_mm_dd"]}
            ),
            "report-date identity",
        ),
        (lambda rows: rows[0].update({"open_interest_all": "4.0"}), "integer"),
        (lambda rows: rows[0].update({"traders_tot_all": "-1"}), "non-negative"),
    ],
)
def test_api_shape_date_and_numeric_corruption_fail_closed(
    mutator: Any,
    message: str,
) -> None:
    rows = api_rows()
    mutator(rows)
    with pytest.raises(SourceSchemaError, match=message):
        adapter(rows=rows).fetch()


def test_api_and_annual_crosscheck_mismatch_fails_closed() -> None:
    annual_rows = api_rows()
    annual_rows[2]["asset_mgr_positions_long"] = "2425701"
    with pytest.raises(SourceSchemaError, match="API and annual compressed rows"):
        adapter(annual=annual_zip(annual_rows)).fetch()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-a-zip", "could not be parsed"),
        (annual_zip(member="wrong.txt"), "member identity"),
        (annual_zip(extra_member=True), "member identity"),
        (annual_zip(missing_header="Open_Interest_All"), "missing fields"),
        (annual_zip(raw_csv=b"\xff"), "not valid UTF-8"),
    ],
)
def test_annual_zip_container_schema_and_encoding_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(annual=content).fetch()


def test_annual_duplicate_selected_row_fails_closed() -> None:
    rows = api_rows()
    rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(SourceSchemaError, match="duplicate selected"):
        adapter(annual=annual_zip(rows)).fetch()


@pytest.mark.parametrize(
    ("kind", "content", "message"),
    [
        (
            "schedule",
            html_bytes(tuple(marker for marker in SCHEDULE_MARKERS if "July" not in marker)),
            "release schedule semantic markers",
        ),
        (
            "policy",
            html_bytes(tuple(marker for marker in POLICY_MARKERS if "not updated" not in marker)),
            "COT policy semantic markers",
        ),
        ("schedule", b"\xff", "not valid UTF-8"),
        ("policy", b"<script>only hidden</script>", "has no visible text"),
    ],
)
def test_schedule_and_policy_semantics_fail_closed(
    kind: str,
    content: bytes,
    message: str,
) -> None:
    instance = adapter(schedule=content) if kind == "schedule" else adapter(policy=content)
    with pytest.raises(SourceSchemaError, match=message):
        instance.fetch()


@pytest.mark.parametrize(
    ("pdf", "message"),
    [
        (b"not-pdf", "not a PDF"),
        (b"%PDF-invalid", "could not be parsed"),
        (notes_pdf(pages=3), "page count"),
        (notes_pdf(width=611), "geometry"),
        (notes_pdf(blank_page=1), "blank text page"),
        (
            notes_pdf(replacements={"staff classifies traders": "staff labels activity"}),
            "semantic markers",
        ),
    ],
)
def test_explanatory_pdf_structure_and_caveats_fail_closed(
    pdf: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(notes=pdf).fetch()


@pytest.mark.parametrize(
    ("kind", "content_type", "message"),
    [
        ("api", "text/html", "API JSON content type"),
        ("annual", "text/plain", "annual ZIP content type"),
        ("schedule", "application/json", "release-schedule HTML content type"),
        ("policy", "application/json", "policy HTML content type"),
        ("notes", "text/plain", "explanatory PDF content type"),
    ],
)
def test_all_response_content_types_fail_closed(
    kind: str,
    content_type: str,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(content_types={kind: content_type}).fetch()


def test_response_urls_and_api_query_are_exact() -> None:
    instance = CFTCTFFScheduledReleaseAdapter(cast(SafeHttpClient, object()))
    for invalid in (
        "http://www.cftc.gov/files/dea/history/fut_fin_txt_2026.zip",
        "https://evil.example/files/dea/history/fut_fin_txt_2026.zip",
        "https://www.cftc.gov/files/dea/history/fut_fin_txt_2025.zip",
        "https://www.cftc.gov/files/dea/history/fut_fin_txt_2026.zip?q=1",
    ):
        with pytest.raises(SourceSchemaError, match="supporting response URL"):
            instance._validate_plain_url(invalid, instance.annual_endpoint)

    correct = (
        "https://publicreporting.cftc.gov/resource/gpe5-46if.json?%24limit=3&"
        "%24where=cftc_contract_market_code%3D%22042601%22+AND+"
        "report_date_as_yyyy_mm_dd+in%28%222026-07-14T00%3A00%3A00.000%22%2C"
        "%222026-07-21T00%3A00%3A00.000%22%2C%222026-07-28T00%3A00%3A00.000%22%29&"
        "%24order=report_date_as_yyyy_mm_dd+ASC%2Ccftc_contract_market_code+ASC%2Cid+ASC"
    )
    instance._validate_api_url(correct)
    for invalid in (
        correct.replace("%24limit=3", "%24limit=4"),
        correct.replace("gpe5-46if", "wrong-view"),
        correct + "&extra=1",
    ):
        with pytest.raises(SourceSchemaError, match=r"API response (URL|query)"):
            instance._validate_api_url(invalid)


def test_detached_wrong_response_url_fails_before_parsing() -> None:
    valid_rows = api_rows()
    payloads = {
        "api": json.dumps(valid_rows, separators=(",", ":")).encode(),
        "annual": annual_zip(),
        "schedule": html_bytes(SCHEDULE_MARKERS),
        "policy": html_bytes(POLICY_MARKERS),
        "notes": notes_pdf(),
    }

    class WrongURLClient:
        call = 0

        def get(self, url: str, **_kwargs: Any) -> tuple[HttpResponseSnapshot, bytes, datetime]:
            labels = ("api", "annual", "schedule", "policy", "notes")
            label = labels[self.call]
            self.call += 1
            content = payloads[label]
            content_type = {
                "api": "application/json",
                "annual": "application/zip",
                "schedule": "text/html",
                "policy": "text/html",
                "notes": "application/pdf",
            }[label]
            params = _kwargs.get("params")
            request_url = str(httpx.URL(url, params=params)) if params else url
            if label == "annual":
                request_url = "https://www.cftc.gov/wrong.zip"
            snapshot = HttpResponseSnapshot(
                status_code=200,
                headers={"Content-Type": content_type},
                request_url=request_url,
                content=content,
            )
            return snapshot, content, datetime(2026, 8, 14, tzinfo=UTC)

    instance = CFTCTFFScheduledReleaseAdapter(cast(SafeHttpClient, WrongURLClient()))
    with pytest.raises(SourceSchemaError, match="supporting response URL"):
        instance.fetch()
