from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

import httpx
import pytest

from finreplay.adapters import FederalReserveFOMCStatementAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2023, 2, 1)


def html_bytes(
    *,
    page_date: str = "February 01, 2023",
    timezone: str = "EST",
    title: str = "Federal Reserve issues FOMC statement",
    lower: str = "4-1/2",
    upper: str = "4-3/4",
    update_date: str = "February 01, 2023",
    release_time: str = "2:00 p.m.",
    extra: str = "",
) -> bytes:
    return f"""
    <!doctype html><html><head><title>Federal Reserve Board - {title}</title></head><body>
      <main><div>{page_date}</div><h1>{title}</h1>
      <div>For release at {release_time} {timezone}</div>
      <p>The Committee decided to raise the target range for the federal funds rate to
      {lower} to {upper} percent.</p>
      <div>Last Update: {update_date}</div>{extra}</main>
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


def adapter(
    handler: Any,
    *,
    release_date: date = RELEASE_DATE,
) -> FederalReserveFOMCStatementAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return FederalReserveFOMCStatementAdapter(safe, release_date=release_date)


def test_archived_statement_uses_explicit_release_time_and_target_range() -> None:
    content = html_bytes()
    batch = adapter(lambda request: response(request, content)).fetch()

    assert len(batch.records) == 2
    lower, upper = batch.records
    assert lower.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert lower.source.vintage_as_of == datetime(2023, 2, 1, 19, 0, tzinfo=UTC)
    assert lower.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert lower.interval.valid_from == datetime(2023, 2, 1, 19, 0, tzinfo=UTC)
    assert lower.interval.published_at == datetime(2023, 2, 1, 19, 0, tzinfo=UTC)
    assert lower.interval.available_at == datetime(2023, 2, 1, 19, 0, tzinfo=UTC)
    assert lower.interval.availability_confidence == 1.0
    assert lower.evidence_class is EvidenceClass.REPORTED
    assert lower.payload["metric"] == "target_range_lower"
    assert lower.payload["value_basis_points"] == 450
    assert lower.payload["source_display_value_percent"] == "4-1/2"
    assert lower.payload["range_width_basis_points"] == 25
    assert lower.payload["release_timezone_abbreviation"] == "EST"
    assert upper.payload["metric"] == "target_range_upper"
    assert upper.payload["value_basis_points"] == 475
    assert upper.payload["source_display_value_percent"] == "4-3/4"
    assert batch.receipts[0].historical_replay_eligible is True
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2023, 2, 1, 18, 59, tzinfo=UTC)) == []
        known = vault.records_as_of(datetime(2023, 2, 1, 19, 0, tzinfo=UTC))
        assert {record.record_id for record in known} == {
            lower.record_id,
            upper.record_id,
        }


def test_daylight_time_is_validated_and_converted() -> None:
    content = html_bytes(
        page_date="March 22, 2023",
        update_date="March 22, 2023",
        timezone="EDT",
        lower="4-3/4",
        upper="5",
    )
    batch = adapter(
        lambda request: response(request, content),
        release_date=date(2023, 3, 22),
    ).fetch()
    assert batch.records[0].interval.available_at == datetime(2023, 3, 22, 18, tzinfo=UTC)
    assert [record.payload["value_basis_points"] for record in batch.records] == [475, 500]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"page_date": "January 31, 2023"}, "page dates"),
        ({"update_date": "January 31, 2023"}, "page dates"),
        ({"timezone": "EDT"}, "timezone abbreviation"),
        ({"title": "Federal Reserve issues policy statement"}, "release-time header"),
        ({"release_time": "3:00 p.m."}, "release-time header"),
        ({"lower": "4-3/4", "upper": "4-1/2"}, "increasing endpoints"),
        ({"lower": "4", "upper": "5-1/4"}, "supported increments"),
        ({"lower": "4-1/3"}, "target-range decision"),
    ],
)
def test_statement_schema_corruption_fails_closed(
    kwargs: dict[str, Any],
    match: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=match):
        adapter(lambda request: response(request, html_bytes(**kwargs))).fetch()


def test_duplicate_markers_fail_closed() -> None:
    duplicate_header = (
        "<div>February 01, 2023 Federal Reserve issues FOMC statement "
        "For release at 2:00 p.m. EST</div>"
    )
    with pytest.raises(SourceSchemaError, match="exactly one dated release-time header"):
        adapter(
            lambda request: response(request, html_bytes(extra=duplicate_header))
        ).fetch()

    duplicate_range = (
        "<p>The Committee decided to raise the target range for the federal funds rate to "
        "4-1/2 to 4-3/4 percent.</p>"
    )
    with pytest.raises(SourceSchemaError, match="exactly one target-range decision"):
        adapter(
            lambda request: response(request, html_bytes(extra=duplicate_range))
        ).fetch()

    duplicate_update = "<div>Last Update: February 01, 2023</div>"
    with pytest.raises(SourceSchemaError, match="exactly one Last Update"):
        adapter(
            lambda request: response(request, html_bytes(extra=duplicate_update))
        ).fetch()


def test_invalid_encoding_and_content_type_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="valid UTF-8"):
        adapter(lambda request: response(request, b"\xff\xfe")).fetch()

    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(lambda request: response(request, html_bytes(), "application/json")).fetch()


def test_response_url_and_future_statement_cannot_be_backdated() -> None:
    class WrongURLClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "text/html"},
                    "request_url": (
                        "https://www.federalreserve.gov/newsevents/pressreleases/"
                        "monetary20230322a.htm"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, html_bytes(), datetime(2026, 1, 1, tzinfo=UTC)

    wrong = FederalReserveFOMCStatementAdapter(
        cast(SafeHttpClient, WrongURLClient()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="requested release"):
        wrong.fetch()

    class EarlyClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "text/html"},
                    "request_url": (
                        "https://www.federalreserve.gov/newsevents/pressreleases/"
                        "monetary20230201a.htm"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, html_bytes(), datetime(2023, 2, 1, 18, 59, tzinfo=UTC)

    early = FederalReserveFOMCStatementAdapter(
        cast(SafeHttpClient, EarlyClient()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
