from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from finreplay.adapters import (
    FDIC_DATASET_BY_SLUG,
    FDIC_DATASET_SPECS,
    FDICDatasetAdapter,
    SourceSchemaError,
)
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import TemporalCoverage


def make_adapter(slug: str, handler: Any) -> FDICDatasetAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay test", client=client)
    return FDICDatasetAdapter(safe, FDIC_DATASET_BY_SLUG[slug])


def payload(row: dict[str, Any], *, total: int = 1) -> dict[str, Any]:
    return {
        "meta": {
            "total": total,
            "index": {"name": "fixture-index", "createTimestamp": "2026-08-12T00:00:00Z"},
        },
        "data": [{"data": row, "score": 0}],
        "totals": {"count": 1},
    }


def response(request: httpx.Request, value: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json=value,
        headers={"Content-Type": "application/json"},
        request=request,
    )


def make_row(slug: str) -> dict[str, Any]:
    spec = FDIC_DATASET_BY_SLUG[slug]
    row: dict[str, Any] = dict.fromkeys(spec.default_fields, 1)
    row.update(CERT=24735)
    dates: dict[str, str | int] = {
        "institutions": "10/04/1990",
        "locations": "08/07/2026",
        "history": "2013-06-30T00:00:00",
        "summary": "1934",
        "failures": "5/28/1934",
        "sod": 1994,
        "demographics": "19840331",
    }
    if spec.valid_time_field is not None:
        row[spec.valid_time_field] = dates[slug]
    if "ID" in spec.default_fields:
        row["ID"] = f"{slug}-fixture-id"
    if "UNINUM" in spec.identity_fields:
        row["UNINUM"] = 10
    return row


def test_catalog_has_seven_unique_official_data_products() -> None:
    assert len(FDIC_DATASET_SPECS) == 7
    assert len(FDIC_DATASET_BY_SLUG) == 7
    assert len({spec.adapter_id for spec in FDIC_DATASET_SPECS}) == 7


@pytest.mark.parametrize("slug", tuple(FDIC_DATASET_BY_SLUG))
def test_each_fdic_dataset_has_source_specific_parser_and_latest_only_boundary(slug: str) -> None:
    row = make_row(slug)
    adapter = make_adapter(slug, lambda request: response(request, payload(row)))
    batch, total = adapter.fetch_page()
    assert total == 1
    assert len(batch.records) == 1
    assert batch.records[0].source.source_id == f"fdic.bankfind.{slug}"
    assert batch.records[0].source.temporal_coverage is TemporalCoverage.LATEST_ONLY
    assert batch.records[0].interval.available_at == batch.receipts[0].retrieved_at
    assert batch.records[0].interval.valid_from.tzinfo is UTC
    assert batch.receipts[0].historical_replay_eligible is False


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("institutions", datetime(1990, 10, 4, tzinfo=UTC)),
        ("locations", datetime(2026, 8, 7, tzinfo=UTC)),
        ("history", datetime(2013, 6, 30, tzinfo=UTC)),
        ("summary", datetime(1934, 12, 31, tzinfo=UTC)),
        ("failures", datetime(1934, 5, 28, tzinfo=UTC)),
        ("sod", datetime(1994, 12, 31, tzinfo=UTC)),
        ("demographics", datetime(1984, 3, 31, tzinfo=UTC)),
    ],
)
def test_fdic_dataset_economic_dates_are_parsed_without_claiming_availability(
    slug: str, expected: datetime
) -> None:
    adapter = make_adapter(slug, lambda request: response(request, payload(make_row(slug))))
    batch, _ = adapter.fetch_page()
    assert batch.records[0].interval.valid_from == expected
    assert batch.records[0].interval.available_at > expected


def test_fdic_dataset_missing_identity_and_date_fail_closed() -> None:
    row = make_row("history")
    row["ID"] = ""
    adapter = make_adapter("history", lambda request: response(request, payload(row)))
    with pytest.raises(SourceSchemaError, match="identity"):
        adapter.fetch_page()

    row = make_row("failures")
    row["FAILDATE"] = "not-a-date"
    adapter = make_adapter("failures", lambda request: response(request, payload(row)))
    with pytest.raises(SourceSchemaError, match="invalid FDIC date"):
        adapter.fetch_page()


def test_fdic_dataset_normalizes_omitted_optional_field_to_null() -> None:
    row = make_row("history")
    row.pop("OUT_UNINUM")
    adapter = make_adapter("history", lambda request: response(request, payload(row)))
    batch, _ = adapter.fetch_page()
    assert batch.records[0].payload["OUT_UNINUM"] is None


def test_fdic_dataset_query_and_schema_guards() -> None:
    adapter = make_adapter(
        "institutions",
        lambda request: response(request, payload(make_row("institutions"))),
    )
    with pytest.raises(ValueError, match="limit"):
        adapter.fetch_page(limit=0)
    with pytest.raises(ValueError, match="offset"):
        adapter.fetch_page(offset=-1)
    with pytest.raises(ValueError, match="filters"):
        adapter.fetch_page(filters="CERT:1\nDROP")
    with pytest.raises(ValueError, match="sort_by"):
        adapter.fetch_page(sort_by="UNKNOWN")
    with pytest.raises(ValueError, match="sort_order"):
        adapter.fetch_page(sort_order="DOWN")
    with pytest.raises(ValueError, match="uppercase"):
        adapter.fetch_page(fields=("bad-field",))


@pytest.mark.parametrize(
    ("bad_payload", "match"),
    [
        ({"meta": {}, "data": []}, "meta.index"),
        (
            {
                "meta": {
                    "total": -1,
                    "index": {"name": "x", "createTimestamp": "x"},
                },
                "data": [],
            },
            "non-negative",
        ),
        (
            {
                "meta": {
                    "total": 0,
                    "index": {"name": "x", "createTimestamp": "x"},
                },
                "data": {},
            },
            "must be a list",
        ),
    ],
)
def test_fdic_dataset_metadata_schema_drift_fails_closed(
    bad_payload: dict[str, Any], match: str
) -> None:
    adapter = make_adapter("institutions", lambda request: response(request, bad_payload))
    with pytest.raises(SourceSchemaError, match=match):
        adapter.fetch_page()
