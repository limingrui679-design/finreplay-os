from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from finreplay.adapters import SECCompanyFactsAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient

CIK = 719_739


def companyfacts_payload() -> dict[str, Any]:
    return {
        "cik": CIK,
        "entityName": "SVB Financial Group",
        "facts": {
            "us-gaap": {
                "Assets": {
                    "label": "Assets",
                    "description": "Total assets reported by the filer.",
                    "units": {
                        "USD": [
                            {
                                "end": "2022-12-31",
                                "val": 211_793_000_000,
                                "accn": "0000719739-23-000021",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2023-02-24",
                                "frame": "CY2022Q4I",
                            }
                        ]
                    },
                },
                "InterestExpenseNonOperating": {
                    "label": "Interest expense",
                    "description": "Non-operating interest expense.",
                    "units": {
                        "USD": [
                            {
                                "start": "2022-01-01",
                                "end": "2022-12-31",
                                "val": 1_234_000_000,
                                "accn": "0000719739-23-000021",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2023-02-24",
                            }
                        ]
                    },
                },
            }
        },
    }


def make_adapter(payload: Any) -> SECCompanyFactsAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=payload,
            headers={"Content-Type": "application/json"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SECCompanyFactsAdapter(
        SafeHttpClient(user_agent="Mingrui Li FinReplay test", client=client)
    )


def test_companyfacts_uses_exact_acceptance_time_when_joined() -> None:
    accepted = datetime(2023, 2, 24, 16, 43, 8, tzinfo=UTC)
    batch = make_adapter(companyfacts_payload()).fetch(
        CIK,
        acceptance_times={"0000719739-23-000021": accepted},
    )
    assert len(batch.records) == 2
    assert all(record.interval.available_at == accepted for record in batch.records)
    assert all(record.interval.availability_confidence == 1.0 for record in batch.records)
    assert all(
        record.payload["knowledge_time_method"] == "acceptance_exact"
        for record in batch.records
    )
    assert "exact EDGAR acceptance for 2 facts" in batch.receipts[0].warnings[1]


def test_companyfacts_falls_back_to_conservative_next_day_bound() -> None:
    batch = make_adapter(companyfacts_payload()).fetch(CIK)
    assert len(batch.records) == 2
    expected = datetime(2023, 2, 25, tzinfo=UTC)
    assert all(record.interval.available_at == expected for record in batch.records)
    filing_day = datetime(2023, 2, 24, tzinfo=UTC)
    assert all(record.interval.published_at == filing_day for record in batch.records)
    assert all(record.interval.availability_confidence == 0.9 for record in batch.records)
    assert "next-day filing bounds for 2 facts" in batch.receipts[0].warnings[1]


def test_companyfacts_duration_and_instant_validity_are_distinct() -> None:
    batch = make_adapter(companyfacts_payload()).fetch(CIK)
    by_concept = {record.payload["concept"]: record for record in batch.records}
    assets = by_concept["Assets"]
    interest = by_concept["InterestExpenseNonOperating"]
    assert assets.interval.valid_from == datetime(2022, 12, 31, tzinfo=UTC)
    assert assets.interval.valid_to is None
    assert interest.interval.valid_from == datetime(2022, 1, 1, tzinfo=UTC)
    assert interest.interval.valid_to == datetime(2023, 1, 1, tzinfo=UTC)
    assert len({record.record_id for record in batch.records}) == 2


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update(cik=1), "expected"),
        (lambda value: value.update(entityName=""), "entityName"),
        (lambda value: value.update(facts={}), "contains no facts"),
        (
            lambda value: value["facts"]["us-gaap"]["Assets"].update(label=123),
            "label must be text",
        ),
        (
            lambda value: value["facts"]["us-gaap"]["Assets"]["units"]["USD"][0].pop(
                "val"
            ),
            "missing val",
        ),
        (
            lambda value: value["facts"]["us-gaap"]["Assets"]["units"]["USD"][0].update(
                val="not-numeric"
            ),
            "must be numeric",
        ),
        (
            lambda value: value["facts"]["us-gaap"]["Assets"]["units"]["USD"][0].update(
                accn="bad"
            ),
            "invalid SEC fact accession",
        ),
        (
            lambda value: value["facts"]["us-gaap"]["Assets"]["units"]["USD"][0].update(
                end="12/31/2022"
            ),
            "YYYY-MM-DD",
        ),
    ],
)
def test_companyfacts_schema_drift_fails_closed(mutation: Any, match: str) -> None:
    value = companyfacts_payload()
    mutation(value)
    with pytest.raises(SourceSchemaError, match=match):
        make_adapter(value).fetch(CIK)


def test_companyfacts_rejects_bad_period_and_acceptance_mapping() -> None:
    value = companyfacts_payload()
    fact = value["facts"]["us-gaap"]["InterestExpenseNonOperating"]["units"]["USD"][0]
    fact["start"] = "2023-01-01"
    with pytest.raises(SourceSchemaError, match="start must not follow end"):
        make_adapter(value).fetch(CIK)

    with pytest.raises(ValueError, match="timezone-aware"):
        make_adapter(companyfacts_payload()).fetch(
            CIK,
            acceptance_times={
                "0000719739-23-000021": datetime(
                    2023, 2, 24, 16, 43, 8, tzinfo=UTC
                ).replace(tzinfo=None)
            },
        )

    conflict = make_adapter(companyfacts_payload()).fetch(
        CIK,
        acceptance_times={
            "0000719739-23-000021": datetime(2023, 2, 23, 16, 43, 8, tzinfo=UTC)
        },
    )
    assert all(
        record.payload["knowledge_time_method"]
        == "acceptance_conflict_filed_next_day_bound"
        for record in conflict.records
    )
    assert all(
        record.interval.available_at == datetime(2023, 2, 25, tzinfo=UTC)
        for record in conflict.records
    )
    assert "2 facts had an acceptance/filed date conflict" in conflict.receipts[0].warnings[1]


def test_companyfacts_preserves_null_presentation_metadata_with_visible_fallback() -> None:
    value = companyfacts_payload()
    concept = value["facts"]["us-gaap"]["Assets"]
    concept["label"] = None
    concept["description"] = None
    batch = make_adapter(value).fetch(CIK)
    assets = next(record for record in batch.records if record.payload["concept"] == "Assets")
    assert assets.payload["label"] == "us-gaap:Assets"
    assert assets.payload["description"] == ""
    assert assets.payload["source_label"] is None
    assert assets.payload["source_description"] is None
    assert "Generated 2 display fallbacks" in batch.receipts[0].warnings[2]


def test_companyfacts_rejects_invalid_cik() -> None:
    with pytest.raises(ValueError, match="CIK"):
        make_adapter(companyfacts_payload()).fetch(0)
