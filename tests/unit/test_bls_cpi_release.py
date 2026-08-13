from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

import httpx
import pytest

from finreplay.adapters import BLSCPIArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2023, 1, 12)


def html_bytes(
    *,
    release_date: str = "January 12, 2023",
    weekday: str = "Thursday",
    time_label: str = "8:30 a.m. (ET)",
    release_number: str = "USDL-23-0017",
    title: str = "CONSUMER PRICE INDEX - DECEMBER 2022",
    direction: str = "declined",
    monthly: str = "0.1",
    headline_month: str = "December",
    prior_direction: str = "increasing",
    prior: str = "0.1",
    prior_month: str = "November",
    year_over_year: str = "6.5",
    extra: str = "",
) -> bytes:
    return f"""
    <!doctype html><html><head><title>Consumer Price Index</title></head><body>
      <div>Transmission of material in this release is embargoed until
      {time_label} {weekday}, {release_date} {release_number}</div>
      <h1>{title}</h1>
      <p>The Consumer Price Index for All Urban Consumers (CPI-U) {direction} {monthly}
      percent in {headline_month} on a seasonally adjusted basis, after {prior_direction}
      {prior} percent in {prior_month}, the U.S. Bureau of Labor Statistics reported today.
      Over the last 12 months, the all items index increased {year_over_year} percent before
      seasonal adjustment.</p>{extra}
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
) -> BLSCPIArchiveAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return BLSCPIArchiveAdapter(safe, release_date=release_date)


def test_archived_cpi_release_uses_explicit_embargo_and_snapshot_values() -> None:
    content = html_bytes()
    batch = adapter(lambda request: response(request, content)).fetch()

    assert len(batch.records) == 2
    monthly, year_over_year = batch.records
    assert monthly.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert monthly.source.vintage_as_of == datetime(2023, 1, 12, 13, 30, tzinfo=UTC)
    assert monthly.source.license_class is LicenseClass.REDISTRIBUTABLE
    assert monthly.interval.available_at == datetime(2023, 1, 12, 13, 30, tzinfo=UTC)
    assert monthly.interval.valid_from == datetime(2022, 12, 1, tzinfo=UTC)
    assert monthly.evidence_class is EvidenceClass.REPORTED
    assert monthly.payload["metric"] == "all_items_monthly_change_seasonally_adjusted"
    assert monthly.payload["value_tenths_percent"] == -1
    assert monthly.payload["prior_month_change_tenths_percent"] == 1
    assert monthly.payload["adjustment"] == "Seasonally Adjusted"
    assert year_over_year.payload["metric"] == (
        "all_items_12_month_change_not_seasonally_adjusted"
    )
    assert year_over_year.payload["value_tenths_percent"] == 65
    assert year_over_year.payload["adjustment"] == "Not Seasonally Adjusted"
    assert batch.receipts[0].historical_replay_eligible is True
    assert len(batch.receipts[0].warnings) == 2
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2023, 1, 12, 13, 29, tzinfo=UTC)) == []
        assert len(vault.records_as_of(datetime(2023, 1, 12, 13, 30, tzinfo=UTC))) == 2


def test_daylight_time_and_positive_monthly_change_are_preserved() -> None:
    content = html_bytes(
        release_date="March 14, 2023",
        weekday="Tuesday",
        release_number="USDL-23-0484",
        title="CONSUMER PRICE INDEX - FEBRUARY 2023",
        direction="rose",
        monthly="0.4",
        headline_month="February",
        prior="0.5",
        prior_month="January",
        year_over_year="6.0",
    )
    batch = adapter(
        lambda request: response(request, content),
        release_date=date(2023, 3, 14),
    ).fetch()
    assert batch.records[0].interval.available_at == datetime(2023, 3, 14, 12, 30, tzinfo=UTC)
    assert [record.payload["value_tenths_percent"] for record in batch.records] == [4, 60]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"release_date": "January 11, 2023"}, "release date"),
        ({"weekday": "Wednesday"}, "weekday"),
        ({"time_label": "9:30 a.m. (ET)"}, "embargo statement"),
        ({"release_number": "BLS-23-0017"}, "embargo statement"),
        ({"title": "CONSUMER PRICE INDEX - JANUARY 2023"}, "headline month"),
        (
            {
                "title": "CONSUMER PRICE INDEX - MARCH 2023",
                "headline_month": "March",
            },
            "precede",
        ),
        ({"prior_month": "October"}, "preceding calendar month"),
        ({"monthly": "0.12"}, "headline fact block"),
        ({"monthly": "100.1"}, "supported range"),
        ({"headline_month": "Notamonth"}, "headline month is invalid"),
    ],
)
def test_release_schema_corruption_fails_closed(
    kwargs: dict[str, Any],
    match: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=match):
        adapter(lambda request: response(request, html_bytes(**kwargs))).fetch()


def test_duplicate_markers_fail_closed() -> None:
    duplicate_embargo = (
        "<div>Transmission of material in this release is embargoed until 8:30 a.m. (ET) "
        "Thursday, January 12, 2023 USDL-23-0017</div>"
    )
    with pytest.raises(SourceSchemaError, match="exactly one embargo"):
        adapter(
            lambda request: response(request, html_bytes(extra=duplicate_embargo))
        ).fetch()

    duplicate_title = "<div>CONSUMER PRICE INDEX - DECEMBER 2022</div>"
    with pytest.raises(SourceSchemaError, match="exactly one report-period title"):
        adapter(
            lambda request: response(request, html_bytes(extra=duplicate_title))
        ).fetch()

    duplicate_headline = (
        "<p>The Consumer Price Index for All Urban Consumers (CPI-U) declined 0.1 percent "
        "in December on a seasonally adjusted basis, after increasing 0.1 percent in November, "
        "the U.S. Bureau of Labor Statistics reported today. Over the last 12 months, the all "
        "items index increased 6.5 percent before seasonal adjustment.</p>"
    )
    with pytest.raises(SourceSchemaError, match="exactly one headline fact block"):
        adapter(
            lambda request: response(request, html_bytes(extra=duplicate_headline))
        ).fetch()


def test_invalid_encoding_and_content_type_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="neither valid UTF-8 nor Windows-1252"):
        adapter(lambda request: response(request, b"\x81")).fetch()

    with pytest.raises(SourceSchemaError, match="content type"):
        adapter(lambda request: response(request, html_bytes(), "application/json")).fetch()


def test_response_url_and_future_release_cannot_be_backdated() -> None:
    class WrongURLClient:
        def get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "text/html"},
                    "request_url": "https://www.bls.gov/news.release/archives/cpi_02142023.htm",
                    "status_code": 200,
                },
            )()
            return snapshot, html_bytes(), datetime(2026, 1, 1, tzinfo=UTC)

    wrong = BLSCPIArchiveAdapter(
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
                    "request_url": "https://www.bls.gov/news.release/archives/cpi_01122023.htm",
                    "status_code": 200,
                },
            )()
            return snapshot, html_bytes(), datetime(2023, 1, 12, 13, 29, tzinfo=UTC)

    early = BLSCPIArchiveAdapter(
        cast(SafeHttpClient, EarlyClient()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
