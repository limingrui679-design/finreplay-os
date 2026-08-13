from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

import httpx
import pytest

from finreplay.adapters import ALFREDTreasuryYieldVintageAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

VINTAGE = date(2023, 3, 9)
OBSERVATION = date(2023, 3, 8)


def csv_bytes(
    *,
    header: str = "observation_date,DGS2_20230309",
    rows: tuple[str, ...] = ("2023-03-08,5.05",),
) -> bytes:
    return ("\n".join((header, *rows)) + "\n").encode()


def response(
    request: httpx.Request,
    content: bytes,
    content_type: str = "application/csv",
) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"Content-Type": content_type},
        request=request,
    )


def adapter(
    handler: Any,
    *,
    series_id: str = "DGS2",
    vintage_date: date = VINTAGE,
    observation_date: date = OBSERVATION,
) -> ALFREDTreasuryYieldVintageAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return ALFREDTreasuryYieldVintageAdapter(
        safe,
        series_id=series_id,
        vintage_date=vintage_date,
        observation_date=observation_date,
    )


def test_native_vintage_yield_is_knowledge_safe_and_basis_point_exact() -> None:
    content = csv_bytes()
    batch = adapter(lambda request: response(request, content)).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("DGS2:20230309:2023-03-08")
    assert record.source.temporal_coverage is TemporalCoverage.VINTAGE_NATIVE
    assert record.source.vintage_as_of == datetime(2023, 3, 9, tzinfo=UTC)
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.interval.valid_from == datetime(2023, 3, 8, tzinfo=UTC)
    assert record.interval.available_at == datetime(2023, 3, 11, tzinfo=UTC)
    assert record.interval.published_at == record.interval.available_at
    assert record.interval.availability_confidence == 1.0
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["series_id"] == "DGS2"
    assert record.payload["maturity_years"] == 2
    assert record.payload["reported_value_percent"] == "5.05"
    assert record.payload["value_basis_points"] == 505
    assert record.payload["unit"] == "Basis Points"
    assert batch.receipts[0].historical_replay_eligible is True
    assert len(batch.receipts[0].warnings) == 3
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2023, 3, 10, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2023, 3, 11, tzinfo=UTC)) == [record]


def test_ten_year_series_metadata_and_integer_percent_are_supported() -> None:
    content = csv_bytes(
        header="observation_date,DGS10_20230309",
        rows=("2023-03-08,4",),
    )
    batch = adapter(
        lambda request: response(request, content),
        series_id="DGS10",
    ).fetch()
    record = batch.records[0]
    assert record.payload["maturity_years"] == 10
    assert record.payload["reported_value_percent"] == "4"
    assert record.payload["value_basis_points"] == 400


def test_constructor_rejects_unknown_series_and_impossible_vintage_order() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: response(request, b"")))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    with pytest.raises(ValueError, match="DGS2 or DGS10"):
        ALFREDTreasuryYieldVintageAdapter(
            safe,
            series_id="DGS30",
            vintage_date=VINTAGE,
            observation_date=OBSERVATION,
        )
    with pytest.raises(ValueError, match="cannot precede"):
        ALFREDTreasuryYieldVintageAdapter(
            safe,
            series_id="DGS2",
            vintage_date=date(2023, 3, 7),
            observation_date=OBSERVATION,
        )


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (csv_bytes(header="date,DGS2_20230309"), "header must exactly"),
        (csv_bytes(header="observation_date,DGS2_20230310"), "header must exactly"),
        (csv_bytes(rows=()), "exactly one"),
        (csv_bytes(rows=("2023-03-08",)), "exactly one"),
        (
            csv_bytes(rows=("2023-03-08,5.05", "2023-03-09,5.01")),
            "exactly one",
        ),
        (csv_bytes(rows=("2023-03-07,5.05",)), "differs from the requested"),
        (csv_bytes(rows=("2023-03-08,5.005",)), "at most two places"),
        (csv_bytes(rows=("2023-03-08,.",)), "at most two places"),
        (csv_bytes(rows=("2023-03-08,NaN",)), "at most two places"),
        (csv_bytes(rows=("2023-03-08,100.01",)), "supported range"),
        (csv_bytes(rows=("2023-03-08,-10.01",)), "supported range"),
        (b"\xff\xfe", "valid UTF-8"),
    ],
)
def test_csv_corruption_fails_closed(content: bytes, match: str) -> None:
    with pytest.raises(SourceSchemaError, match=match):
        adapter(lambda request: response(request, content)).fetch()


def test_content_type_response_url_and_early_retrieval_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(lambda request: response(request, csv_bytes(), "text/html")).fetch()

    class WrongURLClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "application/csv"},
                    "request_url": (
                        "https://alfred.stlouisfed.org/graph/alfredgraph.csv?"
                        "id=DGS2&cosd=2023-03-08&coed=2023-03-08&"
                        "vintage_date=2023-03-10"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, csv_bytes(), datetime(2026, 1, 1, tzinfo=UTC)

    wrong = ALFREDTreasuryYieldVintageAdapter(
        cast(SafeHttpClient, WrongURLClient()),
        series_id="DGS2",
        vintage_date=VINTAGE,
        observation_date=OBSERVATION,
    )
    with pytest.raises(SourceSchemaError, match="requested vintage"):
        wrong.fetch()

    class EarlyClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "application/csv"},
                    "request_url": (
                        "https://alfred.stlouisfed.org/graph/alfredgraph.csv?"
                        "id=DGS2&cosd=2023-03-08&coed=2023-03-08&"
                        "vintage_date=2023-03-09"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, csv_bytes(), datetime(2023, 3, 10, tzinfo=UTC)

    early = ALFREDTreasuryYieldVintageAdapter(
        cast(SafeHttpClient, EarlyClient()),
        series_id="DGS2",
        vintage_date=VINTAGE,
        observation_date=OBSERVATION,
    )
    with pytest.raises(SourceSchemaError, match="not yet conservatively knowable"):
        early.fetch()
