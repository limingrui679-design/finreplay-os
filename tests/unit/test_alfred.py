from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from finreplay.adapters import ALFREDGDPVintageAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

VINTAGE = date(2023, 1, 26)
START = date(2022, 7, 1)
END = date(2022, 10, 1)


def csv_bytes(
    *,
    header: str = "observation_date,GDP_20230126",
    rows: tuple[str, ...] = (
        "2022-07-01,25723.941",
        "2022-10-01,26132.458",
    ),
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


def adapter(handler: Any) -> ALFREDGDPVintageAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return ALFREDGDPVintageAdapter(
        safe,
        vintage_date=VINTAGE,
        observation_start=START,
        observation_end=END,
    )


def test_native_vintage_gdp_is_knowledge_safe_and_content_addressed() -> None:
    content = csv_bytes()
    batch = adapter(lambda request: response(request, content)).fetch()

    assert len(batch.records) == 2
    first, second = batch.records
    assert first.source.temporal_coverage is TemporalCoverage.VINTAGE_NATIVE
    assert first.source.vintage_as_of == datetime(2023, 1, 26, tzinfo=UTC)
    assert first.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert first.interval.available_at == datetime(2023, 1, 28, tzinfo=UTC)
    assert first.interval.published_at == first.interval.available_at
    assert first.interval.availability_confidence == 1.0
    assert first.evidence_class is EvidenceClass.REPORTED
    assert first.payload["value"] == "25723.941"
    assert second.payload["value"] == "26132.458"
    assert batch.receipts[0].historical_replay_eligible is True
    assert batch.receipts[0].warnings
    assert batch.artifacts[0].content == content
    assert "vintage_date=2023-01-26" in str(batch.receipts[0].request_url)

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2023, 1, 27, tzinfo=UTC)) == []
        known = vault.records_as_of(datetime(2023, 1, 28, tzinfo=UTC))
        assert [item.record_id for item in known] == [first.record_id, second.record_id]


def test_constructor_rejects_reversed_observation_interval() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: response(request, b"")))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    with pytest.raises(ValueError, match="must not precede"):
        ALFREDGDPVintageAdapter(
            safe,
            vintage_date=VINTAGE,
            observation_start=END,
            observation_end=START,
        )


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (csv_bytes(header="date,GDP_20230126"), "header must exactly"),
        (csv_bytes(header="observation_date,GDP_20230125"), "header must exactly"),
        (csv_bytes(rows=()), "no observations"),
        (csv_bytes(rows=("2022-07-01",)), "two fields"),
        (csv_bytes(rows=("22-07-01,25723.941",)), "YYYY-MM-DD"),
        (csv_bytes(rows=("2022-02-30,25723.941",)), "not a valid date"),
        (csv_bytes(rows=("2022-04-01,25723.941",)), "outside the requested"),
        (
            csv_bytes(rows=("2022-10-01,26132.458", "2022-07-01,25723.941")),
            "unique and ascending",
        ),
        (
            csv_bytes(rows=("2022-07-01,25723.941", "2022-07-01,25723.941")),
            "unique and ascending",
        ),
        (csv_bytes(rows=("2022-07-01,.",)), "positive decimal"),
        (csv_bytes(rows=("2022-07-01,NaN",)), "positive decimal"),
        (csv_bytes(rows=("2022-07-01,-1",)), "positive decimal"),
        (b"\xff\xfe", "valid UTF-8"),
    ],
)
def test_csv_corruption_fails_closed(content: bytes, match: str) -> None:
    with pytest.raises(SourceSchemaError, match=match):
        adapter(lambda request: response(request, content)).fetch()


def test_content_type_and_response_url_corruption_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(lambda request: response(request, csv_bytes(), "text/html")).fetch()

    class WrongURLClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            content = csv_bytes()
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "application/csv"},
                    "request_url": (
                        "https://alfred.stlouisfed.org/graph/alfredgraph.csv?"
                        "id=GDP&cosd=2022-07-01&coed=2022-10-01&vintage_date=2023-01-25"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, content, datetime(2026, 1, 1, tzinfo=UTC)

    wrong = ALFREDGDPVintageAdapter(
        WrongURLClient(),  # type: ignore[arg-type]
        vintage_date=VINTAGE,
        observation_start=START,
        observation_end=END,
    )
    with pytest.raises(SourceSchemaError, match="requested vintage"):
        wrong.fetch()


def test_future_vintage_cannot_be_backdated_as_knowable() -> None:
    class EarlyClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            content = csv_bytes()
            request_url = (
                "https://alfred.stlouisfed.org/graph/alfredgraph.csv?"
                "id=GDP&cosd=2022-07-01&coed=2022-10-01&vintage_date=2023-01-26"
            )
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "application/csv"},
                    "request_url": request_url,
                    "status_code": 200,
                },
            )()
            return snapshot, content, datetime(2023, 1, 27, tzinfo=UTC)

    early = ALFREDGDPVintageAdapter(
        EarlyClient(),  # type: ignore[arg-type]
        vintage_date=VINTAGE,
        observation_start=START,
        observation_end=END,
    )
    with pytest.raises(SourceSchemaError, match="not yet conservatively knowable"):
        early.fetch()
