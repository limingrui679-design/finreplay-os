from __future__ import annotations

from typing import Any

import httpx
import pytest

from finreplay.adapters import (
    NYFED_DATASET_BY_SLUG,
    NYFED_DATASET_SPECS,
    NYFedMarketsAdapter,
    SourceSchemaError,
)
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import LicenseClass, TemporalCoverage


def row(slug: str) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {
        "ambs_operations": {
            "auctionStatus": "Results",
            "operationId": "OR 080426 1",
            "operationDate": "2026-08-04",
            "operationType": "Outright Specified Pool Sale",
            "settlementDate": "2026-08-20",
            "lastUpdated": "2026-08-04 14:50:56",
            "totalSubmittedOrigFace": "100",
            "totalAcceptedOrigFace": "40",
            "totalSubmittedCurrFace": "90",
            "totalAcceptedCurrFace": "35",
            "totalAmtSubmittedPar": "",
            "totalAmtAcceptedPar": "",
        },
        "central_bank_liquidity_swaps": {
            "operationType": "U.S. Dollar Liquidity Swap",
            "counterparty": "European Central Bank",
            "currency": "USD",
            "tradeDate": "2026-08-05",
            "settlementDate": "2026-08-06",
            "maturityDate": "2026-08-13",
            "termInDays": 7,
            "amount": 132_000_000,
            "interestRate": 3.88,
            "lastUpdated": "2026-08-06 16:00:00",
        },
        "primary_dealer_guidesheet": {
            "title": "FR 2004SI Guide Sheet",
            "reportWeeksFromDate": "2026-08-05",
            "reportWeeksToDate": "2026-08-12",
            "nextDistributionDate": "2026-08-13",
            "details": [
                {
                    "formLine": "1",
                    "secType": "2 YEAR NOTE",
                    "cusip": "91282CRB9",
                    "issueDate": "2026-07-31",
                    "maturityDate": "2028-07-31",
                },
                {
                    "formLine": "2",
                    "secType": "3 YEAR NOTE",
                    "cusip": "91282CQZ7",
                    "issueDate": "2026-07-15",
                    "maturityDate": "2029-07-15",
                },
            ],
        },
        "primary_dealer_asof_dates": {
            "asof": "2026-08-05",
            "seriesbreak": "SBP2018",
        },
        "reference_rates": {
            "effectiveDate": "2026-08-11",
            "type": "SOFR",
            "percentRate": 3.63,
            "volumeInBillions": 2_187,
            "revisionIndicator": "",
        },
        "repo_operations": {
            "operationId": "RP 081226 25",
            "operationDate": "2026-08-12",
            "settlementDate": "2026-08-12",
            "maturityDate": "2026-08-13",
            "operationType": "Repo",
            "totalAmtSubmitted": 100,
            "totalAmtAccepted": 40,
            "lastUpdated": "2026-08-12 08:30:29",
        },
        "securities_lending": {
            "operationId": "SL 081226 25",
            "operationDate": "2026-08-12",
            "settlementDate": "2026-08-12",
            "maturityDate": "2026-08-13",
            "totalParAmtSubmitted": 100,
            "totalParAmtAccepted": 90,
            "lastUpdated": "2026-08-12 12:15:58",
        },
        "soma_summary": {
            "asOfDate": "2026-08-05",
            "mbs": "10",
            "cmbs": "5",
            "tips": "10",
            "frn": "",
            "notesbonds": "25",
            "bills": "40",
            "agencies": "10",
            "total": "100.00",
        },
        "treasury_operations": {
            "operationId": "OR 081126 25",
            "operationDate": "2026-08-11",
            "settlementDate": "2026-08-12",
            "maturityRangeStart": "2026-09-10",
            "maturityRangeEnd": "2026-12-10",
            "totalParAmtSubmitted": "100",
            "totalParAmtAccepted": "20",
            "lastUpdated": "2026-08-11 09:21:24",
        },
    }
    return rows[slug]


def payload(slug: str, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    spec = NYFED_DATASET_BY_SLUG[slug]
    values = rows or [row(slug)]
    root: dict[str, Any] = {}
    current = root
    for component in spec.row_path[:-1]:
        nested: dict[str, Any] = {}
        current[component] = nested
        current = nested
    current[spec.row_path[-1]] = values[0] if spec.object_row else values
    return root


def response(request: httpx.Request, value: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json=value,
        headers={"Content-Type": "application/json;charset=utf-8"},
        request=request,
    )


def adapter(slug: str, handler: Any) -> NYFedMarketsAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return NYFedMarketsAdapter(safe, NYFED_DATASET_BY_SLUG[slug])


def test_nine_unique_markets_products_have_source_specific_contracts() -> None:
    assert len(NYFED_DATASET_SPECS) == 9
    assert len(NYFED_DATASET_BY_SLUG) == 9
    assert len({spec.adapter_id for spec in NYFED_DATASET_SPECS}) == 9
    assert len({spec.semantic_kind for spec in NYFED_DATASET_SPECS}) == 9
    assert all(spec.endpoint_path.endswith(".json") for spec in NYFED_DATASET_SPECS)


@pytest.mark.parametrize("slug", tuple(NYFED_DATASET_BY_SLUG))
def test_each_markets_product_parses_and_remains_latest_only(slug: str) -> None:
    value = payload(slug)
    batch = adapter(slug, lambda request: response(request, value)).fetch()
    assert len(batch.records) == 1
    assert batch.records[0].source.temporal_coverage is TemporalCoverage.LATEST_ONLY
    assert batch.records[0].source.license_class is LicenseClass.REVIEW_REQUIRED
    assert batch.records[0].interval.available_at == batch.receipts[0].retrieved_at
    assert batch.receipts[0].historical_replay_eligible is False
    assert batch.receipts[0].warnings
    assert batch.records[0].payload == row(slug)
    assert str(batch.receipts[0].request_url).endswith(
        NYFED_DATASET_BY_SLUG[slug].endpoint_path
    )


@pytest.mark.parametrize(
    ("slug", "mutator", "match"),
    [
        (
            "ambs_operations",
            lambda item: item.update(totalAcceptedOrigFace="101"),
            "accepted amount exceeds",
        ),
        (
            "central_bank_liquidity_swaps",
            lambda item: item.update(settlementDate="2026-08-04"),
            "not chronological",
        ),
        (
            "primary_dealer_guidesheet",
            lambda item: item["details"].append(dict(item["details"][0])),
            "duplicate guide-sheet CUSIP",
        ),
        (
            "primary_dealer_asof_dates",
            lambda item: item.update(seriesbreak="bad break"),
            "invalid seriesbreak",
        ),
        (
            "reference_rates",
            lambda item: item.pop("percentRate"),
            "no reference-rate measure",
        ),
        (
            "repo_operations",
            lambda item: item.update(totalAmtAccepted=101),
            "accepted amount exceeds",
        ),
        (
            "securities_lending",
            lambda item: item.update(totalParAmtSubmitted=-1),
            "must be non-negative",
        ),
        (
            "soma_summary",
            lambda item: item.update(total="90"),
            "does not equal reported components",
        ),
        (
            "treasury_operations",
            lambda item: item.update(maturityRangeEnd="2026-09-01"),
            "not chronological",
        ),
    ],
)
def test_source_specific_semantic_corruption_fails_closed(
    slug: str, mutator: Any, match: str
) -> None:
    item = row(slug)
    mutator(item)
    with pytest.raises(SourceSchemaError, match=match):
        adapter(slug, lambda request: response(request, payload(slug, [item]))).fetch()


def test_schema_drift_empty_rows_duplicates_and_dates_fail_closed() -> None:
    slug = "reference_rates"

    with pytest.raises(SourceSchemaError, match="path before"):
        adapter(
            "repo_operations", lambda request: response(request, {"repo": None})
        ).fetch()

    with pytest.raises(SourceSchemaError, match="row path must resolve to a list"):
        adapter(slug, lambda request: response(request, {"refRates": {}})).fetch()

    with pytest.raises(SourceSchemaError, match="returned no records"):
        adapter(slug, lambda request: response(request, {"refRates": []})).fetch()

    missing = row(slug)
    missing.pop("effectiveDate")
    with pytest.raises(SourceSchemaError, match="missing fields"):
        adapter(slug, lambda request: response(request, payload(slug, [missing]))).fetch()

    duplicate = row(slug)
    with pytest.raises(SourceSchemaError, match=r"duplicate .* identity"):
        adapter(
            slug,
            lambda request: response(request, payload(slug, [duplicate, dict(duplicate)])),
        ).fetch()

    bad_date = row(slug)
    bad_date["effectiveDate"] = "08/11/2026"
    with pytest.raises(SourceSchemaError, match="YYYY-MM-DD"):
        adapter(slug, lambda request: response(request, payload(slug, [bad_date]))).fetch()


def test_invalid_json_and_content_type_fail_closed() -> None:
    slug = "reference_rates"

    def invalid_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"refRates":[*]}',
            headers={"Content-Type": "application/json"},
            request=request,
        )

    with pytest.raises(SourceSchemaError, match="not valid JSON"):
        adapter(slug, invalid_json).fetch()

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="blocked",
            headers={"Content-Type": "text/html"},
            request=request,
        )

    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(slug, html).fetch()


def test_amount_numeric_edge_cases_and_partial_pairs_fail_closed() -> None:
    ambs = row("ambs_operations")
    for field in (
        "totalSubmittedOrigFace",
        "totalAcceptedOrigFace",
        "totalSubmittedCurrFace",
        "totalAcceptedCurrFace",
    ):
        ambs[field] = ""
    with pytest.raises(SourceSchemaError, match="no complete"):
        adapter(
            "ambs_operations",
            lambda request: response(request, payload("ambs_operations", [ambs])),
        ).fetch()

    partial = row("ambs_operations")
    partial["totalAcceptedOrigFace"] = ""
    with pytest.raises(SourceSchemaError, match="partial"):
        adapter(
            "ambs_operations",
            lambda request: response(request, payload("ambs_operations", [partial])),
        ).fetch()

    repo = row("repo_operations")
    repo["totalAmtSubmitted"] = "NaN"
    with pytest.raises(SourceSchemaError, match="finite"):
        adapter(
            "repo_operations",
            lambda request: response(request, payload("repo_operations", [repo])),
        ).fetch()


def test_soma_accepts_only_the_explainable_component_rounding_bound() -> None:
    slug = "soma_summary"
    within = row(slug)
    within["total"] = "97.60"
    assert len(
        adapter(slug, lambda request: response(request, payload(slug, [within]))).fetch().records
    ) == 1

    outside = row(slug)
    outside["total"] = "96.49"
    with pytest.raises(SourceSchemaError, match="reported components"):
        adapter(slug, lambda request: response(request, payload(slug, [outside]))).fetch()


def test_guide_sheet_nested_corruption_fails_closed() -> None:
    slug = "primary_dealer_guidesheet"
    invalid_cusip = row(slug)
    invalid_cusip["details"][0]["cusip"] = "bad"
    with pytest.raises(SourceSchemaError, match="invalid guide-sheet CUSIP"):
        adapter(slug, lambda request: response(request, payload(slug, [invalid_cusip]))).fetch()

    missing = row(slug)
    missing["details"][0].pop("maturityDate")
    with pytest.raises(SourceSchemaError, match="guide detail missing"):
        adapter(slug, lambda request: response(request, payload(slug, [missing]))).fetch()

    not_a_list = row(slug)
    not_a_list["details"] = {}
    with pytest.raises(SourceSchemaError, match="non-empty list"):
        adapter(slug, lambda request: response(request, payload(slug, [not_a_list]))).fetch()


def test_long_identity_is_hashed_and_reference_rate_terms_are_explicit() -> None:
    slug = "central_bank_liquidity_swaps"
    item = row(slug)
    item["counterparty"] = "X" * 400
    batch = adapter(slug, lambda request: response(request, payload(slug, [item]))).fetch()
    assert ":sha256:" in batch.records[0].record_id
    assert len(batch.records[0].record_id) <= 300

    rates = adapter(
        "reference_rates", lambda request: response(request, payload("reference_rates"))
    )
    other = adapter(
        "repo_operations", lambda request: response(request, payload("repo_operations"))
    )
    assert "Reference-rate reuse" in rates.metadata.redistribution_note
    assert "Reference-rate reuse" not in other.metadata.redistribution_note


def test_unknown_rate_type_and_invalid_date_value_fail_closed() -> None:
    rate = row("reference_rates")
    rate["type"] = "MADEUP"
    with pytest.raises(SourceSchemaError, match="unknown reference-rate type"):
        adapter(
            "reference_rates",
            lambda request: response(request, payload("reference_rates", [rate])),
        ).fetch()

    primary = row("primary_dealer_asof_dates")
    primary["asof"] = "2026-02-30"
    with pytest.raises(SourceSchemaError, match="not a valid date"):
        adapter(
            "primary_dealer_asof_dates",
            lambda request: response(request, payload("primary_dealer_asof_dates", [primary])),
        ).fetch()
