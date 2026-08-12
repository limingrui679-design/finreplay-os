from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from finreplay.adapters import (
    FISCAL_DATA_BY_SLUG,
    FISCAL_DATA_SPECS,
    FiscalDataAdapter,
    SourceSchemaError,
)
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import TemporalCoverage
from finreplay.engines import TimeVault

NOW = datetime(2026, 8, 12, 14, tzinfo=UTC)


def row(slug: str) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {
        "debt_to_penny": {
            "record_date": "2023-03-08",
            "debt_held_public_amt": "24600000000000.10",
            "intragov_hold_amt": "6800000000000.20",
            "tot_pub_debt_out_amt": "31400000000000.30",
            "src_line_nbr": "1",
        },
        "average_interest_rates": {
            "record_date": "2023-02-28",
            "security_type_desc": "Marketable",
            "security_desc": "Treasury Notes",
            "avg_interest_rate_amt": "2.875",
            "src_line_nbr": "2",
        },
        "operating_cash_balance": {
            "record_date": "2023-03-08",
            "account_type": "Treasury General Account (TGA) Closing Balance",
            "close_today_bal": "311982",
            "open_today_bal": "null",
            "open_month_bal": "null",
            "open_fiscal_year_bal": "null",
            "table_nbr": "I",
            "table_nm": "Operating Cash Balance",
            "sub_table_name": "Cash Balance Details",
            "src_line_nbr": "8",
        },
        "treasury_auctions": {
            # The row can describe a future issue while being announced now. The adapter must
            # preserve that distinction instead of using issue_date as knowledge time.
            "record_date": "2026-08-18",
            "cusip": "912797VL8",
            "security_type": "Bill",
            "security_term": "8-Week",
            "announcemt_date": "2026-08-11",
            "auction_date": "2026-08-13",
            "issue_date": "2026-08-18",
            "maturity_date": "2026-10-13",
        },
        "mspd_summary": {
            "record_date": "2023-02-28",
            "security_type_desc": "Marketable",
            "security_class_desc": "Bills",
            "debt_held_public_mil_amt": "200.1",
            "intragov_hold_mil_amt": "0.2",
            "total_mil_amt": "200.3",
            "src_line_nbr": "1",
        },
    }
    return rows[slug]


def payload(slug: str, rows: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    spec = FISCAL_DATA_BY_SLUG[slug]
    total_count = len(rows) if total is None else total
    page_size = max(len(rows), 1)
    pages = (total_count + page_size - 1) // page_size if total_count else 0
    schema = dict.fromkeys(spec.required_fields, "fixture")
    return {
        "data": rows,
        "meta": {
            "count": len(rows),
            "labels": schema,
            "dataTypes": schema,
            "dataFormats": schema,
            "total-count": total_count,
            "total-pages": pages,
        },
        "links": {"self": "&page%5Bnumber%5D=1&page%5Bsize%5D=100"},
    }


def response(request: httpx.Request, value: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json=value,
        headers={"Content-Type": "application/json"},
        request=request,
    )


def adapter(slug: str, handler: Any) -> FiscalDataAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return FiscalDataAdapter(safe, FISCAL_DATA_BY_SLUG[slug])


def test_five_unique_treasury_tables_have_source_specific_contracts() -> None:
    assert len(FISCAL_DATA_SPECS) == 5
    assert len(FISCAL_DATA_BY_SLUG) == 5
    assert len({spec.adapter_id for spec in FISCAL_DATA_SPECS}) == 5
    assert len({spec.semantic_kind for spec in FISCAL_DATA_SPECS}) == 5


@pytest.mark.parametrize("slug", tuple(FISCAL_DATA_BY_SLUG))
def test_each_treasury_table_parses_and_remains_latest_only(slug: str) -> None:
    value = payload(slug, [row(slug)])
    batch, total, pages = adapter(slug, lambda request: response(request, value)).fetch_page(
        page_size=1
    )
    assert total == 1
    assert pages == 1
    assert len(batch.records) == 1
    assert batch.records[0].source.temporal_coverage is TemporalCoverage.LATEST_ONLY
    assert batch.records[0].interval.available_at == batch.receipts[0].retrieved_at
    assert batch.receipts[0].historical_replay_eligible is False

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(NOW) == []


def test_announced_future_auction_uses_announcement_as_economic_time_not_knowledge_time() -> None:
    slug = "treasury_auctions"
    value = payload(slug, [row(slug)])
    batch, _, _ = adapter(slug, lambda request: response(request, value)).fetch_page(page_size=1)
    record = batch.records[0]
    assert record.interval.valid_from == datetime(2026, 8, 11, tzinfo=UTC)
    assert record.payload["issue_date"] == "2026-08-18"
    assert record.interval.available_at == batch.receipts[0].retrieved_at


@pytest.mark.parametrize(
    ("slug", "mutator", "match"),
    [
        (
            "debt_to_penny",
            lambda item: item.update(tot_pub_debt_out_amt="1"),
            "components",
        ),
        (
            "average_interest_rates",
            lambda item: item.update(avg_interest_rate_amt="101"),
            "outside",
        ),
        (
            "operating_cash_balance",
            lambda item: item.update(
                close_today_bal="null",
                open_today_bal="null",
                open_month_bal="null",
                open_fiscal_year_bal="null",
            ),
            "no reported balance",
        ),
        (
            "treasury_auctions",
            lambda item: item.update(auction_date="2026-08-20"),
            "chronologically",
        ),
        (
            "mspd_summary",
            lambda item: item.update(total_mil_amt="0"),
            "components",
        ),
    ],
)
def test_source_specific_semantic_corruption_fails_closed(
    slug: str, mutator: Any, match: str
) -> None:
    item = row(slug)
    mutator(item)
    value = payload(slug, [item])
    with pytest.raises(SourceSchemaError, match=match):
        adapter(slug, lambda request: response(request, value)).fetch_page(page_size=1)


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda value: value.update(data={}), "data must be a list"),
        (lambda value: value["meta"].update(count=2), "meta.count"),
        (lambda value: value["meta"].update(**{"total-count": 0}), "total-count"),
        (lambda value: value["meta"].update(**{"total-pages": 9}), "total-pages"),
        (lambda value: value["meta"]["labels"].pop("record_date"), "meta.labels"),
        (lambda value: value.update(links={}), "links.self"),
        (lambda value: value["data"][0].pop("record_date"), "missing fields"),
        (lambda value: value["data"][0].update(record_date="03/08/2023"), "YYYY-MM-DD"),
    ],
)
def test_generic_schema_drift_fails_closed(mutator: Any, match: str) -> None:
    slug = "debt_to_penny"
    value = payload(slug, [row(slug)])
    mutator(value)
    with pytest.raises(SourceSchemaError, match=match):
        adapter(slug, lambda request: response(request, value)).fetch_page(page_size=1)


def test_invalid_json_content_type_query_and_pagination_fail_closed() -> None:
    slug = "debt_to_penny"

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

    valid = payload(slug, [row(slug)])
    treasury = adapter(slug, lambda request: response(request, valid))
    for kwargs, match in (
        ({"page_number": 0}, "page_number"),
        ({"page_size": 0}, "page_size"),
        ({"sort": ""}, "sort"),
        ({"filters": "bad\nfilter"}, "filters"),
    ):
        with pytest.raises(ValueError, match=match):
            treasury.fetch_page(**kwargs)


def test_complete_pagination_reconciles_totals_and_rejects_duplicates() -> None:
    slug = "debt_to_penny"
    first = row(slug)
    second = {**row(slug), "record_date": "2023-03-09"}

    def pages(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params["page[number]"])
        value = payload(slug, [first if number == 1 else second], total=2)
        value["meta"]["total-pages"] = 2
        return response(request, value)

    batch = adapter(slug, pages).fetch_all(page_size=1, max_pages=2)
    assert len(batch.records) == 2
    assert len(batch.receipts) == 2

    def duplicate_pages(request: httpx.Request) -> httpx.Response:
        value = payload(slug, [first], total=2)
        value["meta"]["total-pages"] = 2
        return response(request, value)

    with pytest.raises(SourceSchemaError, match="duplicate identity"):
        adapter(slug, duplicate_pages).fetch_all(page_size=1, max_pages=2)

    with pytest.raises(SourceSchemaError, match="pagination incomplete"):
        adapter(slug, pages).fetch_all(page_size=1, max_pages=1)


def test_raw_response_hash_and_request_page_parameters_are_preserved() -> None:
    slug = "debt_to_penny"
    value = payload(slug, [row(slug)])
    batch, _, _ = adapter(slug, lambda request: response(request, value)).fetch_page(
        page_number=2,
        page_size=1,
        sort="record_date",
        filters="record_date:gte:2023-03-01",
    )
    expected = json.dumps(value, separators=(",", ":")).encode()
    assert batch.artifacts[0].content == expected
    url = str(batch.receipts[0].request_url)
    assert "page%5Bnumber%5D=2" in url
    assert "filter=record_date%3Agte%3A2023-03-01" in url
