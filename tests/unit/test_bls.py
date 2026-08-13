from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from finreplay.adapters import BLSCPIUAllItemsAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault


def observation(
    *,
    year: str = "2026",
    period: str = "M07",
    period_name: str = "July",
    value: Any = "333.918",
    footnotes: Any = None,
) -> dict[str, Any]:
    return {
        "year": year,
        "period": period,
        "periodName": period_name,
        "latest": "true",
        "value": value,
        "footnotes": [{}] if footnotes is None else footnotes,
    }


def payload(rows: list[Any] | None = None) -> dict[str, Any]:
    return {
        "status": "REQUEST_SUCCEEDED",
        "responseTime": 137,
        "message": [],
        "Results": {
            "series": [
                {
                    "seriesID": BLSCPIUAllItemsAdapter.series_id,
                    "data": rows
                    if rows is not None
                    else [
                        observation(),
                        observation(
                            year="2025",
                            period="M13",
                            period_name="Annual",
                            value="321.943",
                        ),
                        observation(
                            year="2025",
                            period="M10",
                            period_name="October",
                            value="-",
                            footnotes=[
                                {
                                    "code": "X",
                                    "text": "Data unavailable due to a lapse in appropriations",
                                }
                            ],
                        ),
                    ],
                }
            ]
        },
    }


def response(request: httpx.Request, value: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json=value,
        headers={"Content-Type": "application/json;charset=utf-8"},
        request=request,
    )


def adapter(handler: Any) -> BLSCPIUAllItemsAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return BLSCPIUAllItemsAdapter(safe)


def test_cpi_u_series_parses_unavailable_month_without_inventing_a_value() -> None:
    value = payload()
    batch = adapter(lambda request: response(request, value)).fetch()
    assert len(batch.records) == 2
    assert batch.records[0].source.temporal_coverage is TemporalCoverage.LATEST_ONLY
    assert batch.records[0].source.vintage_as_of is None
    assert batch.records[0].source.license_class is LicenseClass.REDISTRIBUTABLE
    assert batch.records[0].interval.valid_from == datetime(2026, 7, 1, tzinfo=UTC)
    assert batch.records[1].payload["value"] == "-"
    assert batch.records[1].payload["footnotes"][0]["code"] == "X"
    assert batch.receipts[0].historical_replay_eligible is False
    assert batch.receipts[0].warnings
    assert str(batch.receipts[0].request_url) == BLSCPIUAllItemsAdapter.endpoint
    expected = json.dumps(value, separators=(",", ":")).encode()
    assert batch.artifacts[0].content == expected

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2026, 7, 31, tzinfo=UTC)) == []


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda item: item.update(year="26"), "year must use YYYY"),
        (lambda item: item.update(year="0000"), "valid calendar month"),
        (lambda item: item.update(period="M14"), "M01-M12 or annual M13"),
        (lambda item: item.update(periodName="June"), "periodName"),
        (lambda item: item.update(footnotes={}), "footnotes must be a list"),
        (lambda item: item.update(value="-", footnotes=[{}]), "explanatory footnote"),
        (lambda item: item.update(value="not-a-number"), "numeric or"),
        (lambda item: item.update(value="NaN"), "finite and non-negative"),
        (lambda item: item.update(value="-1"), "finite and non-negative"),
        (lambda item: item.update(latest="yes"), "latest marker"),
        (lambda item: item.pop("value"), "missing fields"),
    ],
)
def test_observation_corruption_fails_closed(mutator: Any, match: str) -> None:
    item = observation()
    mutator(item)
    with pytest.raises(SourceSchemaError, match=match):
        adapter(lambda request: response(request, payload([item]))).fetch()


def test_footnote_and_calendar_schema_corruption_fails_closed() -> None:
    bad_object = observation(footnotes=["not-an-object"])
    with pytest.raises(SourceSchemaError, match=r"footnotes\[0\].*object"):
        adapter(lambda request: response(request, payload([bad_object]))).fetch()

    bad_type = observation(footnotes=[{"code": 7}])
    with pytest.raises(SourceSchemaError, match="footnote code must be text"):
        adapter(lambda request: response(request, payload([bad_type]))).fetch()

    bad_date = observation(year="2026", period="M02", period_name="February")
    assert adapter(lambda request: response(request, payload([bad_date]))).fetch().records[
        0
    ].interval.valid_from == datetime(2026, 2, 1, tzinfo=UTC)

    bad_annual = observation(period="M13", period_name="December")
    with pytest.raises(SourceSchemaError, match="periodName"):
        adapter(lambda request: response(request, payload([bad_annual]))).fetch()

    annual_only = observation(period="M13", period_name="Annual")
    with pytest.raises(SourceSchemaError, match="no monthly"):
        adapter(lambda request: response(request, payload([annual_only]))).fetch()


def test_duplicate_periods_fail_closed() -> None:
    item = observation()
    with pytest.raises(SourceSchemaError, match="duplicate BLS series period"):
        adapter(lambda request: response(request, payload([item, dict(item)]))).fetch()


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda value: value.update(status="REQUEST_FAILED"), "request status"),
        (lambda value: value.update(responseTime=True), "responseTime"),
        (lambda value: value.update(message={}), "message must be"),
        (lambda value: value.update(message=["Invalid series"]), "source messages"),
        (lambda value: value.update(Results={"series": []}), "exactly one series"),
        (
            lambda value: value["Results"]["series"][0].update(seriesID="WRONG"),
            "returned series",
        ),
        (lambda value: value["Results"]["series"][0].update(data=[]), "non-empty list"),
    ],
)
def test_response_contract_corruption_fails_closed(mutator: Any, match: str) -> None:
    value = payload()
    mutator(value)
    with pytest.raises(SourceSchemaError, match=match):
        adapter(lambda request: response(request, value)).fetch()


def test_invalid_json_and_content_type_fail_closed() -> None:
    def invalid_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-json",
            headers={"Content-Type": "application/json"},
            request=request,
        )

    with pytest.raises(SourceSchemaError, match="not valid JSON"):
        adapter(invalid_json).fetch()

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="blocked",
            headers={"Content-Type": "text/html"},
            request=request,
        )

    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(html).fetch()
