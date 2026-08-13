from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

import httpx
import pytest

from finreplay.adapters import NYFedSOFRHistoricalAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

EFFECTIVE_DATE = date(2019, 9, 16)


def json_bytes(
    *,
    effective_date: str = "2019-09-16",
    rate_type: str = "SOFR",
    rate: str = "2.43",
    p1: str = "2.38",
    p25: str = "2.42",
    p75: str = "2.55",
    p99: str = "4.60",
    revision: str = "",
    extra: str = "",
) -> bytes:
    return (
        '{"refRates":[{'
        f'"effectiveDate":"{effective_date}","type":"{rate_type}",'
        f'"percentRate":{rate},"percentPercentile1":{p1},'
        f'"percentPercentile25":{p25},"percentPercentile75":{p75},'
        f'"percentPercentile99":{p99},"revisionIndicator":"{revision}"{extra}'
        "}]}"
    ).encode()


def response(
    request: httpx.Request,
    content: bytes,
    content_type: str = "application/json",
) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"Content-Type": content_type},
        request=request,
    )


def adapter(handler: Any) -> NYFedSOFRHistoricalAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return NYFedSOFRHistoricalAdapter(safe, effective_date=EFFECTIVE_DATE)


def test_final_sofr_rate_is_immutable_basis_point_exact_and_knowledge_safe() -> None:
    content = json_bytes()
    batch = adapter(lambda request: response(request, content)).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("2019-09-16")
    assert record.entity_id == "nyfed_reference_rate:SOFR"
    assert record.source.temporal_coverage is TemporalCoverage.IMMUTABLE_EVENT
    assert record.source.vintage_as_of == datetime(2019, 9, 17, 19, tzinfo=UTC)
    assert record.source.license_class is LicenseClass.REVIEW_REQUIRED
    assert record.interval.valid_from == datetime(2019, 9, 16, tzinfo=UTC)
    assert record.interval.published_at == datetime(2019, 9, 17, 19, tzinfo=UTC)
    assert record.interval.available_at == record.interval.published_at
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["reported_value_percent"] == "2.43"
    assert record.payload["value_basis_points"] == 243
    assert record.payload["ancillary_statistics_normalized"] is False
    assert batch.receipts[0].historical_replay_eligible is True
    assert len(batch.receipts[0].warnings) == 4
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2019, 9, 17, 18, 59, tzinfo=UTC)) == []
        assert vault.records_as_of(datetime(2019, 9, 17, 19, tzinfo=UTC)) == [record]


def test_verified_calendar_handles_friday_to_monday_and_rejects_other_dates() -> None:
    client = cast(SafeHttpClient, object())
    friday = NYFedSOFRHistoricalAdapter(client, effective_date=date(2019, 9, 13))
    assert friday.publication_date == date(2019, 9, 16)
    with pytest.raises(ValueError, match="verified SOFR publication calendar"):
        NYFedSOFRHistoricalAdapter(client, effective_date=date(2019, 9, 12))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"[]", "must be a JSON object"),
        (b'{"refRates":{}}', "must contain only a refRates list"),
        (b'{"refRates":[]}', "exactly one"),
        (b'{"refRates":[{},{}]}', "exactly one"),
        (json_bytes(extra=',"extra":1'), "approved schema"),
        (json_bytes(effective_date="2019-09-17"), "identity"),
        (json_bytes(rate_type="EFFR"), "identity"),
        (json_bytes(revision="R"), "revision indicator"),
        (json_bytes(rate="2.425"), "whole basis points"),
        (json_bytes(rate="100.01"), "supported range"),
        (json_bytes(rate="true"), "finite percentage"),
        (json_bytes(rate="null"), "finite percentage"),
        (json_bytes(rate="2.43", p25="2.44"), "percentile order"),
        (b'{"refRates":[', "valid JSON"),
        (b"\xff\xfe", "valid UTF-8"),
    ],
)
def test_schema_identity_revision_numeric_and_order_corruption_fail_closed(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=message):
        adapter(lambda request: response(request, content)).fetch()


def test_content_type_response_url_and_early_finality_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(lambda request: response(request, json_bytes(), "text/html")).fetch()

    class WrongURLClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "application/json"},
                    "request_url": (
                        "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?"
                        "startDate=2019-09-16&endDate=2019-09-17&type=rate"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, json_bytes(), datetime(2026, 1, 1, tzinfo=UTC)

    wrong = NYFedSOFRHistoricalAdapter(
        cast(SafeHttpClient, WrongURLClient()),
        effective_date=EFFECTIVE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="requested rate"):
        wrong.fetch()

    class EarlyClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "application/json"},
                    "request_url": (
                        "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?"
                        "startDate=2019-09-16&endDate=2019-09-16&type=rate"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, json_bytes(), datetime(2019, 9, 17, 18, 59, tzinfo=UTC)

    early = NYFedSOFRHistoricalAdapter(
        cast(SafeHttpClient, EarlyClient()),
        effective_date=EFFECTIVE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet conservatively final"):
        early.fetch()
