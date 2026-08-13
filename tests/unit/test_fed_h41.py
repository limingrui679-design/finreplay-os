from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from finreplay.adapters import FederalReserveH41BTFPAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2023, 3, 23)


def html_bytes(
    *,
    page_date: str = "Thursday, March 23, 2023",
    daily_header: str = "Averages of daily figures",
    wednesday: str = "Wednesday Mar 22, 2023",
    week: str = "Week ended Mar 22, 2023",
    row: tuple[str, ...] = (
        "Bank Term Funding Program",
        "34,609",
        "+ 32,166",
        "+ 34,609",
        "53,669",
    ),
    extra_table: str = "",
) -> bytes:
    cells = "".join(f"<td>{value}</td>" for value in row)
    return f"""
    <!doctype html><html><head><title>H.4.1 Release</title></head><body>
      <div>Release Date: {page_date}</div>
      <table>
        <tr><td>Reserve Bank credit</td><td>{daily_header}</td><td>{wednesday}</td></tr>
        <tr><td>{week}</td><td>Change from week ended</td></tr>
        <tr><td>Mar 15, 2023</td><td>Mar 23, 2022</td></tr>
        <tr>{cells}</tr>
      </table>{extra_table}
    </body></html>
    """.encode()


def response(
    request: httpx.Request,
    content: bytes,
    content_type: str = "text/html; charset=utf-8",
) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"Content-Type": content_type},
        request=request,
    )


def adapter(handler: Any) -> FederalReserveH41BTFPAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return FederalReserveH41BTFPAdapter(safe, release_date=RELEASE_DATE)


def test_historical_btfp_release_is_versioned_and_knowledge_safe() -> None:
    content = html_bytes()
    batch = adapter(lambda request: response(request, content)).fetch()

    assert len(batch.records) == 2
    average, outstanding = batch.records
    assert average.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert average.source.vintage_as_of == datetime(2023, 3, 23, tzinfo=UTC)
    assert average.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert average.interval.available_at == datetime(2023, 3, 25, tzinfo=UTC)
    assert average.interval.valid_from == datetime(2023, 3, 22, tzinfo=UTC)
    assert average.evidence_class is EvidenceClass.REPORTED
    assert average.payload["metric"] == "weekly_average"
    assert average.payload["value_millions"] == 34_609
    assert average.payload["weekly_average_change_from_prior_millions"] == 32_166
    assert outstanding.payload["metric"] == "wednesday_outstanding"
    assert outstanding.payload["value_millions"] == 53_669
    assert batch.receipts[0].historical_replay_eligible is True
    assert batch.receipts[0].warnings
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2023, 3, 24, tzinfo=UTC)) == []
        known = vault.records_as_of(datetime(2023, 3, 25, tzinfo=UTC))
        assert [record.record_id for record in known] == sorted(
            (average.record_id, outstanding.record_id)
        )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"page_date": "Thursday, March 16, 2023"}, "release date"),
        ({"daily_header": "Point estimates"}, "daily-average semantics"),
        ({"wednesday": "As of Mar 22, 2023"}, "week-ending Wednesday"),
        ({"week": "Period ended Mar 22, 2023"}, "week-ending Wednesday"),
        ({"wednesday": "Wednesday Mar 21, 2023"}, "inconsistent"),
        (
            {"week": "Week ended Mar 24, 2023", "wednesday": "Wednesday Mar 24, 2023"},
            "inconsistent",
        ),
        (
            {
                "row": (
                    "Bank Term Funding Program",
                    "missing",
                    "+ 32,166",
                    "+ 34,609",
                    "53,669",
                )
            },
            "weekly average",
        ),
        (
            {
                "row": (
                    "Bank Term Funding Program",
                    "34,609",
                    "not-a-change",
                    "+ 34,609",
                    "53,669",
                )
            },
            "weekly change",
        ),
        (
            {
                "row": (
                    "Bank Term Funding Program",
                    "34,609",
                    "+ 32,166",
                    "year-change",
                    "53,669",
                )
            },
            "year-over-year change",
        ),
        (
            {
                "row": (
                    "Bank Term Funding Program",
                    "34,609",
                    "+ 32,166",
                    "+ 34,609",
                    "0",
                )
            },
            "Wednesday outstanding",
        ),
        ({"row": ("Bank Term Funding Program", "34,609")}, "five-cell"),
    ],
)
def test_release_schema_corruption_fails_closed(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(SourceSchemaError, match=match):
        adapter(lambda request: response(request, html_bytes(**kwargs))).fetch()


def test_duplicate_btfp_table_invalid_html_and_content_type_fail_closed() -> None:
    duplicate = (
        "<table><tr><td>A</td><td>Averages of daily figures</td>"
        "<td>Wednesday Mar 22, 2023</td></tr>"
        "<tr><td>Week ended Mar 22, 2023</td></tr>"
        "<tr><td>Bank Term Funding Program</td><td>1</td><td>+ 1</td>"
        "<td>+ 1</td><td>1</td></tr></table>"
    )
    with pytest.raises(SourceSchemaError, match="exactly one"):
        adapter(
            lambda request: response(request, html_bytes(extra_table=duplicate))
        ).fetch()

    with pytest.raises(SourceSchemaError, match="valid UTF-8"):
        adapter(lambda request: response(request, b"\xff\xfe")).fetch()

    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(lambda request: response(request, html_bytes(), "application/json")).fetch()


def test_response_url_and_future_release_cannot_be_backdated() -> None:
    class WrongURLClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            content = html_bytes()
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "text/html"},
                    "request_url": "https://www.federalreserve.gov/releases/h41/20230316/",
                    "status_code": 200,
                },
            )()
            return snapshot, content, datetime(2026, 1, 1, tzinfo=UTC)

    wrong = FederalReserveH41BTFPAdapter(WrongURLClient(), release_date=RELEASE_DATE)  # type: ignore[arg-type]
    with pytest.raises(SourceSchemaError, match="requested release"):
        wrong.fetch()

    class EarlyClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            content = html_bytes()
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "text/html"},
                    "request_url": "https://www.federalreserve.gov/releases/h41/20230323/",
                    "status_code": 200,
                },
            )()
            return snapshot, content, datetime(2023, 3, 24, tzinfo=UTC)

    early = FederalReserveH41BTFPAdapter(EarlyClient(), release_date=RELEASE_DATE)  # type: ignore[arg-type]
    with pytest.raises(SourceSchemaError, match="not yet conservatively knowable"):
        early.fetch()
