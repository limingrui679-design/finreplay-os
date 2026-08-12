from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from finreplay.adapters import (
    AdapterError,
    FDICFinancialsAdapter,
    ResponseLimitError,
    SourceSchemaError,
)
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import TemporalCoverage
from finreplay.engines import TimeVault

FIELDS = ("CERT", "REPDTE", "ASSET", "DEP", "DEPUNINS")


def fdic_payload(rows: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    return {
        "meta": {
            "total": len(rows) if total is None else total,
            "parameters": {},
            "index": {
                "name": "risview_20260608210616",
                "createTimestamp": "2026-06-08T21:06:18Z",
            },
        },
        "data": [{"data": row, "score": 0} for row in rows],
        "totals": {"count": len(rows)},
    }


def row(report_date: str, asset: int = 209_026_000) -> dict[str, Any]:
    return {
        "CERT": 24735,
        "REPDTE": report_date,
        "ASSET": asset,
        "DEP": 175_378_000,
        "DEPUNINS": 151_592_000,
        "ID": f"24735_{report_date}",
    }


def make_adapter(handler: Any, *, max_bytes: int = 1_000_000) -> FDICFinancialsAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(
        user_agent="FinReplay-Tests test@example.invalid",
        max_response_bytes=max_bytes,
        client=client,
    )
    return FDICFinancialsAdapter(safe)


def json_response(request: httpx.Request, payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers={"Content-Type": "application/json"},
        request=request,
    )


def test_fdic_page_parses_source_receipt_and_latest_only_records() -> None:
    adapter = make_adapter(
        lambda request: json_response(
            request,
            fdic_payload([row("20221231"), row("20220930", 210_244_000)]),
        )
    )
    batch, total = adapter.fetch_page(cert=24735, fields=FIELDS)
    assert total == 2
    assert len(batch.records) == 2
    assert batch.records[0].record_id == "fdic.financials:24735:20221231"
    assert batch.records[0].source.temporal_coverage is TemporalCoverage.LATEST_ONLY
    assert batch.records[0].source.vintage_as_of is None
    assert batch.records[0].interval.valid_from == datetime(2022, 12, 31, tzinfo=UTC)
    assert batch.records[0].interval.available_at == batch.receipts[0].retrieved_at
    assert batch.receipts[0].historical_replay_eligible is False
    assert batch.receipts[0].response_bytes == len(batch.artifacts[0].content)


def test_fdic_latest_snapshot_cannot_leak_into_historical_query() -> None:
    adapter = make_adapter(
        lambda request: json_response(request, fdic_payload([row("20221231")]))
    )
    batch, _ = adapter.fetch_page(cert=24735, fields=FIELDS)
    with TimeVault() as vault:
        vault.append(batch.records)
        decision_time = batch.receipts[0].retrieved_at + timedelta(seconds=1)
        assert vault.records_as_of(decision_time) == []
        assert len(vault.records_as_of(decision_time, allow_latest_only=True)) == 1


def test_fdic_pagination_is_complete_and_deduplicated() -> None:
    pages = {
        0: [row("20221231"), row("20220930")],
        2: [row("20220630")],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        return json_response(request, fdic_payload(pages[offset], total=3))

    batch = make_adapter(handler).fetch_all(cert=24735, fields=FIELDS, page_size=2)
    assert len(batch.records) == 3
    assert len(batch.receipts) == 2
    assert {record.payload["REPDTE"] for record in batch.records} == {
        "20221231",
        "20220930",
        "20220630",
    }


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"meta": {}, "data": []}, "meta.index"),
        (fdic_payload([{"CERT": 24735, "REPDTE": "20221231"}]), "missing"),
        (fdic_payload([{**row("20221231"), "CERT": 1}]), "does not match"),
        (fdic_payload([{**row("20221231"), "REPDTE": "2022-12-31"}]), "REPDTE"),
        (
            {
                "meta": {
                    "index": {"name": "x", "createTimestamp": "x"},
                    "total": -1,
                },
                "data": [],
            },
            "non-negative",
        ),
    ],
)
def test_fdic_schema_drift_fails_closed(payload: Any, match: str) -> None:
    adapter = make_adapter(lambda request: json_response(request, payload))
    with pytest.raises(SourceSchemaError, match=match):
        adapter.fetch_page(cert=24735, fields=FIELDS)


def test_fdic_invalid_json_and_content_type_fail_closed() -> None:
    def invalid_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-json",
            headers={"Content-Type": "application/json"},
            request=request,
        )

    with pytest.raises(SourceSchemaError, match="valid JSON"):
        make_adapter(invalid_json).fetch_page(cert=24735, fields=FIELDS)

    def invalid_type(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html></html>",
            headers={"Content-Type": "text/html"},
            request=request,
        )

    with pytest.raises(SourceSchemaError, match="content type"):
        make_adapter(invalid_type).fetch_page(cert=24735, fields=FIELDS)


def test_safe_http_rejects_status_redirect_size_and_unapproved_transport() -> None:
    def status(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request)

    with pytest.raises(AdapterError, match="HTTP 429"):
        make_adapter(status).fetch_page(cert=24735, fields=FIELDS)

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.invalid"}, request=request)

    with pytest.raises(AdapterError, match="redirects"):
        make_adapter(redirect).fetch_page(cert=24735, fields=FIELDS)

    large = json.dumps(fdic_payload([row("20221231")])).encode()

    def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=large,
            headers={"Content-Type": "application/json"},
            request=request,
        )

    with pytest.raises(ResponseLimitError, match="exceed"):
        make_adapter(oversized, max_bytes=10).fetch_page(cert=24735, fields=FIELDS)

    safe = SafeHttpClient(user_agent="test", client=httpx.Client())
    with pytest.raises(AdapterError, match="HTTPS"):
        safe.get("http://api.fdic.gov/banks/financials", allowed_hosts=("api.fdic.gov",))
    with pytest.raises(AdapterError, match="allowlist"):
        safe.get("https://example.com/data", allowed_hosts=("api.fdic.gov",))
    with pytest.raises(AdapterError, match="credentials"):
        safe.get("https://user:pass@api.fdic.gov/data", allowed_hosts=("api.fdic.gov",))


def test_safe_http_decodes_gzip_once_without_reusing_encoding_header() -> None:
    payload = json.dumps(fdic_payload([row("20221231")])).encode()

    def compressed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=gzip.compress(payload),
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            request=request,
        )

    batch, total = make_adapter(compressed).fetch_page(cert=24735, fields=FIELDS)
    assert total == 1
    assert batch.artifacts[0].content == payload


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"cert": 0, "fields": FIELDS}, "positive"),
        ({"cert": 24735, "fields": ()}, "at least one"),
        ({"cert": 24735, "fields": ("CERT", "ASSET")}, "REPDTE"),
        ({"cert": 24735, "fields": (*FIELDS, "bad-field")}, "uppercase"),
        ({"cert": 24735, "fields": FIELDS, "limit": 0}, "limit"),
        ({"cert": 24735, "fields": FIELDS, "offset": -1}, "offset"),
        ({"cert": 24735, "fields": FIELDS, "sort_by": "NAME"}, "sort_by"),
        ({"cert": 24735, "fields": FIELDS, "sort_order": "DOWN"}, "sort_order"),
    ],
)
def test_fdic_query_validation(kwargs: dict[str, Any], match: str) -> None:
    adapter = make_adapter(lambda request: json_response(request, fdic_payload([])))
    with pytest.raises(ValueError, match=match):
        adapter.fetch_page(**kwargs)


def test_fdic_pagination_fails_on_changing_total_empty_page_and_max_pages() -> None:
    calls = 0

    def changing(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        current = 3 if calls == 1 else 4
        records = [row("20221231"), row("20220930")] if calls == 1 else [row("20220630")]
        return json_response(request, fdic_payload(records, total=current))

    with pytest.raises(SourceSchemaError, match="total changed"):
        make_adapter(changing).fetch_all(cert=24735, fields=FIELDS, page_size=2)

    def empty_second(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        records = [row("20221231")] if offset == 0 else []
        return json_response(request, fdic_payload(records, total=2))

    with pytest.raises(SourceSchemaError, match="empty page"):
        make_adapter(empty_second).fetch_all(cert=24735, fields=FIELDS, page_size=1)

    def incomplete(request: httpx.Request) -> httpx.Response:
        return json_response(request, fdic_payload([row("20221231")], total=2))

    with pytest.raises(SourceSchemaError, match="pagination incomplete"):
        make_adapter(incomplete).fetch_all(
            cert=24735,
            fields=FIELDS,
            page_size=1,
            max_pages=1,
        )


def test_fdic_fetch_all_rejects_bad_max_pages() -> None:
    adapter = make_adapter(lambda request: json_response(request, fdic_payload([])))
    with pytest.raises(ValueError, match="max_pages"):
        adapter.fetch_all(cert=24735, fields=FIELDS, max_pages=0)
