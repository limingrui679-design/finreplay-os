from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from finreplay.adapters import (
    CFTC_COT_BY_SLUG,
    CFTC_COT_SPECS,
    CFTCCOTAdapter,
    SourceSchemaError,
)
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault


def cot_row(slug: str, *, identity: str = "260804TEST") -> dict[str, Any]:
    common: dict[str, Any] = {
        "id": identity,
        "market_and_exchange_names": "TEST CONTRACT - TEST EXCHANGE",
        "report_date_as_yyyy_mm_dd": "2026-08-04T00:00:00.000",
        "yyyy_report_week_ww": "2026 Report Week 31",
        "contract_market_name": "TEST CONTRACT",
        "cftc_contract_market_code": "123456",
        "commodity_name": "TEST COMMODITY",
        "open_interest_all": "1000",
        "tot_rept_positions_long_all": "900",
        "tot_rept_positions_short": "890",
        "nonrept_positions_long_all": "100",
        "nonrept_positions_short_all": "110",
    }
    products: dict[str, dict[str, Any]] = {
        "legacy_futures_only": {
            "noncomm_positions_long_all": "250",
            "noncomm_positions_short_all": "240",
            "noncomm_postions_spread_all": "100",
            "comm_positions_long_all": "550",
            "comm_positions_short_all": "550",
            "futonly_or_combined": "FutOnly",
        },
        "disaggregated_futures_only": {
            "prod_merc_positions_long": "250",
            "prod_merc_positions_short": "250",
            "swap_positions_long_all": "100",
            "swap__positions_short_all": "90",
            "swap__positions_spread_all": "50",
            "m_money_positions_long_all": "150",
            "m_money_positions_short_all": "140",
            "m_money_positions_spread": "50",
            "other_rept_positions_long": "150",
            "other_rept_positions_short": "160",
            "other_rept_positions_spread": "150",
            "futonly_or_combined": "FutOnly",
        },
        "tff_futures_only": {
            "dealer_positions_long_all": "100",
            "dealer_positions_short_all": "100",
            "dealer_positions_spread_all": "50",
            "asset_mgr_positions_long": "150",
            "asset_mgr_positions_short": "140",
            "asset_mgr_positions_spread": "50",
            "lev_money_positions_long": "200",
            "lev_money_positions_short": "190",
            "lev_money_positions_spread": "50",
            "other_rept_positions_long": "150",
            "other_rept_positions_short": "160",
            "other_rept_positions_spread": "150",
            "futonly_or_combined": "FutOnly",
        },
        "supplemental_cit": {
            "ncomm_postions_long_all_nocit": "200",
            "ncomm_postions_short_all_nocit": "190",
            "ncomm_postions_spread_all_nocit": "100",
            "comm_positions_long_all_nocit": "400",
            "comm_positions_short_all_nocit": "400",
            "cit_positions_long_all": "200",
            "cit_positions_short_all": "200",
        },
    }
    return {**common, **products[slug]}


def response(request: httpx.Request, value: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json=value,
        headers={"Content-Type": "application/json;charset=utf-8"},
        request=request,
    )


def adapter(slug: str, handler: Any) -> CFTCCOTAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return CFTCCOTAdapter(safe, CFTC_COT_BY_SLUG[slug])


def test_four_unique_cot_products_have_independent_views_and_equations() -> None:
    assert len(CFTC_COT_SPECS) == 4
    assert len(CFTC_COT_BY_SLUG) == 4
    assert len({spec.adapter_id for spec in CFTC_COT_SPECS}) == 4
    assert len({spec.view_id for spec in CFTC_COT_SPECS}) == 4
    assert len({spec.upstream_dataset_id for spec in CFTC_COT_SPECS}) == 4
    assert len({spec.report_kind for spec in CFTC_COT_SPECS}) == 4
    assert all(spec.required_fields for spec in CFTC_COT_SPECS)


@pytest.mark.parametrize("slug", tuple(CFTC_COT_BY_SLUG))
def test_each_cot_product_parses_but_does_not_invent_release_time(slug: str) -> None:
    item = cot_row(slug)
    batch = adapter(slug, lambda request: response(request, [item])).fetch_page(limit=1)
    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.interval.valid_from == datetime(2026, 8, 4, tzinfo=UTC)
    assert record.interval.available_at == batch.receipts[0].retrieved_at
    assert record.source.temporal_coverage is TemporalCoverage.IMMUTABLE_EVENT
    assert record.source.vintage_as_of == batch.receipts[0].retrieved_at
    assert record.source.license_class is LicenseClass.REDISTRIBUTABLE
    assert batch.receipts[0].historical_replay_eligible is False
    assert batch.receipts[0].warnings
    assert CFTC_COT_BY_SLUG[slug].view_id in str(batch.receipts[0].request_url)
    assert batch.artifacts[0].content == json.dumps([item], separators=(",", ":")).encode()

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2026, 8, 5, tzinfo=UTC)) == []


def test_supplemental_mixed_case_source_fields_are_normalized_without_losing_values() -> None:
    slug = "supplemental_cit"
    item = cot_row(slug)
    item["NComm_Postions_Long_All_NoCIT"] = item.pop("ncomm_postions_long_all_nocit")
    item["Comm_Positions_Short_All_NoCIT"] = item.pop("comm_positions_short_all_nocit")
    record = adapter(slug, lambda request: response(request, [item])).fetch_page().records[0]
    assert record.payload["ncomm_postions_long_all_nocit"] == "200"
    assert record.payload["comm_positions_short_all_nocit"] == "400"
    assert "NComm_Postions_Long_All_NoCIT" not in record.payload


@pytest.mark.parametrize("slug", tuple(CFTC_COT_BY_SLUG))
def test_classification_corruption_fails_closed(slug: str) -> None:
    spec = CFTC_COT_BY_SLUG[slug]
    item = cot_row(slug)
    field = spec.long_components[0]
    item[field] = str(int(item[field]) + spec.balance_tolerance + 1)
    with pytest.raises(SourceSchemaError, match=r"long classification.*beyond tolerance"):
        adapter(slug, lambda request: response(request, [item])).fetch_page()


@pytest.mark.parametrize("slug", tuple(CFTC_COT_BY_SLUG))
def test_documented_small_balance_tolerance_is_bounded(slug: str) -> None:
    spec = CFTC_COT_BY_SLUG[slug]
    item = cot_row(slug)
    field = spec.short_components[0]
    item[field] = str(int(item[field]) + spec.balance_tolerance)
    assert len(adapter(slug, lambda request: response(request, [item])).fetch_page().records) == 1

    item[field] = str(int(item[field]) + 1)
    with pytest.raises(SourceSchemaError, match=r"short classification.*beyond tolerance"):
        adapter(slug, lambda request: response(request, [item])).fetch_page()


def test_open_interest_mode_and_numeric_corruption_fail_closed() -> None:
    slug = "legacy_futures_only"
    bad_total = cot_row(slug)
    bad_total["nonrept_positions_long_all"] = "120"
    with pytest.raises(SourceSchemaError, match="long open-interest"):
        adapter(slug, lambda request: response(request, [bad_total])).fetch_page()

    bad_mode = cot_row(slug)
    bad_mode["futonly_or_combined"] = "Combined"
    with pytest.raises(SourceSchemaError, match="mode is"):
        adapter(slug, lambda request: response(request, [bad_mode])).fetch_page()

    negative = cot_row(slug)
    negative["open_interest_all"] = "-1"
    with pytest.raises(SourceSchemaError, match="must be non-negative"):
        adapter(slug, lambda request: response(request, [negative])).fetch_page()

    decimal = cot_row(slug)
    decimal["open_interest_all"] = "1000.0"
    with pytest.raises(SourceSchemaError, match="must be an integer"):
        adapter(slug, lambda request: response(request, [decimal])).fetch_page()

    boolean = cot_row(slug)
    boolean["open_interest_all"] = True
    with pytest.raises(SourceSchemaError, match="must be an integer"):
        adapter(slug, lambda request: response(request, [boolean])).fetch_page()


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("2026-08-04", "midnight ISO"),
        ("2026-02-30T00:00:00.000", "valid calendar"),
        (7, "must be text"),
    ],
)
def test_report_date_corruption_fails_closed(value: Any, match: str) -> None:
    slug = "legacy_futures_only"
    item = cot_row(slug)
    item["report_date_as_yyyy_mm_dd"] = value
    with pytest.raises(SourceSchemaError, match=match):
        adapter(slug, lambda request: response(request, [item])).fetch_page()


def test_schema_identity_and_text_corruption_fail_closed() -> None:
    slug = "legacy_futures_only"
    missing = cot_row(slug)
    missing.pop("open_interest_all")
    with pytest.raises(SourceSchemaError, match="missing fields"):
        adapter(slug, lambda request: response(request, [missing])).fetch_page()

    empty_text = cot_row(slug)
    empty_text["commodity_name"] = "  "
    with pytest.raises(SourceSchemaError, match="non-empty text"):
        adapter(slug, lambda request: response(request, [empty_text])).fetch_page()

    nontext = cot_row(slug)
    nontext["id"] = 1
    with pytest.raises(SourceSchemaError, match="non-empty text"):
        adapter(slug, lambda request: response(request, [nontext])).fetch_page()

    duplicate = cot_row(slug)
    with pytest.raises(SourceSchemaError, match="duplicate CFTC response-local ID"):
        adapter(slug, lambda request: response(request, [duplicate, dict(duplicate)])).fetch_page()

    case_collision = cot_row(slug)
    case_collision["ID"] = "different"
    with pytest.raises(SourceSchemaError, match="case-colliding"):
        adapter(slug, lambda request: response(request, [case_collision])).fetch_page()

    with pytest.raises(SourceSchemaError, match=r"row\[0\].*object"):
        adapter(slug, lambda request: response(request, ["not-an-object"])).fetch_page()


def test_oversized_source_identity_is_content_hashed() -> None:
    slug = "legacy_futures_only"
    item = cot_row(slug, identity="X" * 400)
    record = adapter(slug, lambda request: response(request, [item])).fetch_page().records[0]
    assert ":sha256:" in record.record_id
    assert len(record.record_id) <= 300


def test_empty_page_is_a_valid_terminal_receipt() -> None:
    slug = "legacy_futures_only"
    batch = adapter(slug, lambda request: response(request, [])).fetch_page(limit=10, offset=20)
    assert batch.records == ()
    assert batch.receipts[0].record_count == 0
    assert "through:empty" in batch.receipts[0].source_version
    url = str(batch.receipts[0].request_url)
    assert "%24limit=10" in url
    assert "%24offset=20" in url
    assert "%24order=" in url


def test_invalid_json_content_type_and_root_fail_closed() -> None:
    slug = "legacy_futures_only"

    def invalid_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-json",
            headers={"Content-Type": "application/json"},
            request=request,
        )

    with pytest.raises(SourceSchemaError, match="not valid JSON"):
        adapter(slug, invalid_json).fetch_page()

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="blocked",
            headers={"Content-Type": "text/html"},
            request=request,
        )

    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(slug, html).fetch_page()

    with pytest.raises(SourceSchemaError, match="JSON list"):
        adapter(slug, lambda request: response(request, {"error": "bad"})).fetch_page()


def test_complete_pagination_stops_on_short_page_and_rejects_duplicates() -> None:
    slug = "legacy_futures_only"
    first = cot_row(slug, identity="260804FIRST")
    second = cot_row(slug, identity="260804SECOND")

    def pages(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["$offset"])
        rows = [first] if offset == 0 else [second] if offset == 1 else []
        return response(request, rows)

    batch = adapter(slug, pages).fetch_all(page_size=1, max_pages=3)
    assert len(batch.records) == 2
    assert len(batch.receipts) == 3

    def duplicate_pages(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["$offset"])
        return response(request, [first] if offset < 2 else [])

    with pytest.raises(SourceSchemaError, match="pagination produced duplicate"):
        adapter(slug, duplicate_pages).fetch_all(page_size=1, max_pages=3)

    with pytest.raises(SourceSchemaError, match="reached max_pages"):
        adapter(slug, pages).fetch_all(page_size=1, max_pages=2)


def test_query_bounds_fail_before_network() -> None:
    slug = "legacy_futures_only"
    cot = adapter(slug, lambda request: response(request, []))
    for kwargs, match in (
        ({"limit": 0}, "limit"),
        ({"limit": 50_001}, "limit"),
        ({"offset": -1}, "offset"),
    ):
        with pytest.raises(ValueError, match=match):
            cot.fetch_page(**kwargs)
    with pytest.raises(ValueError, match="max_pages"):
        cot.fetch_all(max_pages=0)
    with pytest.raises(ValueError, match="limit"):
        cot.fetch_all(page_size=0)
