from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from finreplay.adapters import (
    SECHistoricalSubmissionsAdapter,
    SECSubmissionsAdapter,
    SourceSchemaError,
)
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import TemporalCoverage
from finreplay.engines import TimeVault

CIK = 719_739
CIK_PADDED = "0000719739"


def filing_columns() -> dict[str, list[Any]]:
    return {
        "accessionNumber": ["0000719739-23-000021", "0001193125-23-064680"],
        "filingDate": ["2023-02-24", "2023-03-08"],
        "reportDate": ["2022-12-31", "2023-03-08"],
        "acceptanceDateTime": [
            "2023-02-24T16:43:08.000Z",
            "2023-03-08T17:16:05.000Z",
        ],
        "act": ["34", "34"],
        "form": ["10-K", "8-K"],
        "fileNumber": ["001-39116", "001-39116"],
        "filmNumber": ["23669356", "23717712"],
        "items": ["", "2.02,7.01,9.01"],
        "size": [10_000_000, 500_000],
        "isXBRL": [1, 1],
        "isInlineXBRL": [1, 1],
        "primaryDocument": ["sivb-20221231.htm", "d430920d8k.htm"],
        "primaryDocDescription": ["10-K", "8-K"],
    }


def main_payload() -> dict[str, Any]:
    return {
        "cik": CIK_PADDED,
        "name": "SVB FINANCIAL GROUP",
        "filings": {
            "recent": filing_columns(),
            "files": [
                {
                    "name": "CIK0000719739-submissions-001.json",
                    "filingCount": 1659,
                    "filingFrom": "1995-02-14",
                    "filingTo": "2016-03-06",
                }
            ],
        },
    }


def response(request: httpx.Request, payload: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        headers={"Content-Type": "application/json"},
        request=request,
    )


def adapters(handler: Any) -> tuple[SECSubmissionsAdapter, SECHistoricalSubmissionsAdapter]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="Mingrui Li FinReplay test", client=client)
    return SECSubmissionsAdapter(safe), SECHistoricalSubmissionsAdapter(safe)


def test_sec_submissions_uses_acceptance_time_as_knowledge_time() -> None:
    main, _ = adapters(lambda request: response(request, main_payload()))
    batch, historical = main.fetch(CIK)
    assert len(batch.records) == 2
    assert historical == ("CIK0000719739-submissions-001.json",)
    first = batch.records[0]
    assert first.record_id.endswith("0000719739-23-000021")
    assert first.interval.valid_from == datetime(2022, 12, 31, tzinfo=UTC)
    assert first.interval.available_at == datetime(2023, 2, 24, 16, 43, 8, tzinfo=UTC)
    assert first.source.temporal_coverage is TemporalCoverage.IMMUTABLE_EVENT
    assert batch.receipts[0].historical_replay_eligible is True
    assert batch.artifacts[0].sha256 == batch.receipts[0].response_sha256


def test_sec_submission_as_of_query_excludes_not_yet_accepted_filing() -> None:
    main, _ = adapters(lambda request: response(request, main_payload()))
    batch, _ = main.fetch(CIK)
    with TimeVault() as vault:
        vault.append(batch.records)
        march_1 = vault.records_as_of(datetime(2023, 3, 1, tzinfo=UTC))
        march_9 = vault.records_as_of(datetime(2023, 3, 9, tzinfo=UTC))
    assert [item.payload["form"] for item in march_1] == ["10-K"]
    assert {item.payload["form"] for item in march_9} == {"10-K", "8-K"}


def test_historical_submission_adapter_validates_declared_file_and_parses_events() -> None:
    _, historical = adapters(lambda request: response(request, filing_columns()))
    batch = historical.fetch(cik=CIK, file_name="CIK0000719739-submissions-001.json")
    assert len(batch.records) == 2
    assert batch.receipts[0].adapter_id == "sec.edgar.submissions_historical"

    with pytest.raises(ValueError, match="ten-digit CIK"):
        historical.fetch(cik=CIK, file_name="../CIK0000719739-submissions-001.json")
    with pytest.raises(ValueError, match="ten-digit CIK"):
        historical.fetch(cik=CIK, file_name="CIK0000000001-submissions-001.json")


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda value: value.update(cik="1"), "expected"),
        (
            lambda value: value["filings"]["recent"]["form"].pop(),
            "unequal lengths",
        ),
        (
            lambda value: value["filings"]["recent"]["accessionNumber"].__setitem__(
                0, "bad-accession"
            ),
            "invalid SEC accession",
        ),
        (
            lambda value: value["filings"]["recent"]["acceptanceDateTime"].__setitem__(
                0, "2023-02-24"
            ),
            "include a timezone",
        ),
        (
            lambda value: value["filings"]["files"][0].update(name="bad.json"),
            "historical submissions file name",
        ),
    ],
)
def test_sec_submissions_schema_drift_fails_closed(mutator: Any, match: str) -> None:
    value = main_payload()
    mutator(value)
    main, _ = adapters(lambda request: response(request, value))
    with pytest.raises(SourceSchemaError, match=match):
        main.fetch(CIK)


def test_sec_duplicate_accession_and_bad_report_date_fail_closed() -> None:
    value = main_payload()
    value["filings"]["recent"]["accessionNumber"][1] = value["filings"]["recent"][
        "accessionNumber"
    ][0]
    main, _ = adapters(lambda request: response(request, value))
    with pytest.raises(SourceSchemaError, match="duplicate SEC accession"):
        main.fetch(CIK)

    value = main_payload()
    value["filings"]["recent"]["reportDate"][0] = "12/31/2022"
    main, _ = adapters(lambda request: response(request, value))
    with pytest.raises(SourceSchemaError, match="YYYY-MM-DD"):
        main.fetch(CIK)


def test_sec_empty_report_date_falls_back_to_filing_date() -> None:
    value = main_payload()
    value["filings"]["recent"]["reportDate"][0] = ""
    main, _ = adapters(lambda request: response(request, value))
    batch, _ = main.fetch(CIK)
    assert batch.records[0].interval.valid_from == datetime(2023, 2, 24, tzinfo=UTC)


def test_sec_json_and_content_type_fail_closed() -> None:
    def invalid_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-json",
            headers={"Content-Type": "application/json"},
            request=request,
        )

    main, _ = adapters(invalid_json)
    with pytest.raises(SourceSchemaError, match="not valid JSON"):
        main.fetch(CIK)

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="blocked",
            headers={"Content-Type": "text/html"},
            request=request,
        )

    main, _ = adapters(html)
    with pytest.raises(SourceSchemaError, match="content type"):
        main.fetch(CIK)


@pytest.mark.parametrize("cik", [0, -1, 10_000_000_000])
def test_sec_rejects_invalid_cik(cik: int) -> None:
    main, _ = adapters(lambda request: response(request, main_payload()))
    with pytest.raises(ValueError, match="CIK"):
        main.fetch(cik)


def test_sec_live_shape_fixture_matches_real_svb_accessions() -> None:
    serialized = json.dumps(main_payload(), sort_keys=True)
    assert "0000719739-23-000021" in serialized
    assert "2023-02-24T16:43:08.000Z" in serialized
