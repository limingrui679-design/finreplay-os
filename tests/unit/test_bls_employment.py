from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

import httpx
import pytest

from finreplay.adapters import BLSEmploymentSituationArchiveAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2023, 2, 3)


def html_bytes(
    *,
    release_date: str = "February 3, 2023",
    weekday: str = "Friday",
    time_label: str = "8:30 a.m. (ET)",
    release_number: str = "USDL-23-0151",
    title: str = "THE EMPLOYMENT SITUATION -- JANUARY 2023",
    payroll: str = "517,000",
    headline_month: str = "January",
    verb: str = "rose",
    rate_phrase: str = "changed little at",
    rate: str = "3.4",
    extra: str = "",
) -> bytes:
    return f"""
    <!doctype html><html><head><title>Employment Situation</title></head><body>
      <div>Transmission of material in this news release is embargoed until
      {release_number} {time_label} {weekday}, {release_date}</div>
      <h1>{title}</h1>
      <p>Total nonfarm payroll employment {verb} by {payroll} in {headline_month},
      and the unemployment rate {rate_phrase} {rate} percent, the U.S. Bureau of
      Labor Statistics reported today.</p>
      {extra}
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


def adapter(handler: Any) -> BLSEmploymentSituationArchiveAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return BLSEmploymentSituationArchiveAdapter(safe, release_date=RELEASE_DATE)


def test_archived_release_uses_explicit_embargo_time_and_snapshot_values() -> None:
    content = html_bytes()
    batch = adapter(lambda request: response(request, content)).fetch()

    assert len(batch.records) == 2
    payroll, unemployment = batch.records
    assert payroll.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert payroll.source.vintage_as_of == datetime(2023, 2, 3, 13, 30, tzinfo=UTC)
    assert payroll.source.license_class is LicenseClass.REDISTRIBUTABLE
    assert payroll.interval.published_at == datetime(2023, 2, 3, 13, 30, tzinfo=UTC)
    assert payroll.interval.available_at == datetime(2023, 2, 3, 13, 30, tzinfo=UTC)
    assert payroll.interval.valid_from == datetime(2023, 1, 1, tzinfo=UTC)
    assert payroll.interval.availability_confidence == 1.0
    assert payroll.evidence_class is EvidenceClass.REPORTED
    assert payroll.payload["release_number"] == "USDL-23-0151"
    assert payroll.payload["report_period"] == "2023-01"
    assert payroll.payload["metric"] == "nonfarm_payroll_change"
    assert payroll.payload["value_thousands"] == 517
    assert payroll.payload["unit"] == "Thousands of Persons"
    assert unemployment.payload["metric"] == "unemployment_rate"
    assert unemployment.payload["value_percent"] == 3.4
    assert unemployment.payload["unit"] == "Percent"
    assert batch.receipts[0].historical_replay_eligible is True
    assert len(batch.receipts[0].warnings) == 2
    assert batch.artifacts[0].content == content

    with TimeVault() as vault:
        vault.append(batch.records)
        assert vault.records_as_of(datetime(2023, 2, 3, 13, 29, tzinfo=UTC)) == []
        known = vault.records_as_of(datetime(2023, 2, 3, 13, 30, tzinfo=UTC))
        assert {record.record_id for record in known} == {
            payroll.record_id,
            unemployment.record_id,
        }


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"release_date": "January 6, 2023"}, "release date"),
        ({"weekday": "Thursday"}, "weekday"),
        ({"time_label": "9:30 a.m. (ET)"}, "embargo time"),
        ({"release_number": "BLS-23-0151"}, "embargo time"),
        ({"title": "THE EMPLOYMENT SITUATION -- FEBRUARY 2023"}, "headline month"),
        ({"title": "THE EMPLOYMENT SITUATION -- MARCH 2023", "headline_month": "March"}, "precede"),
        ({"payroll": "0"}, "positive integer"),
        ({"rate": "100.1"}, "between zero and 100"),
        ({"rate": "3.45"}, "headline fact sentence"),
        ({"headline_month": "Notamonth"}, "headline month is invalid"),
    ],
)
def test_release_schema_corruption_fails_closed(
    kwargs: dict[str, Any],
    match: str,
) -> None:
    with pytest.raises(SourceSchemaError, match=match):
        adapter(lambda request: response(request, html_bytes(**kwargs))).fetch()


def test_duplicate_release_markers_fail_closed() -> None:
    duplicate_embargo = (
        "<div>Transmission of material in this news release is embargoed until "
        "USDL-23-0151 8:30 a.m. (ET) Friday, February 3, 2023</div>"
    )
    with pytest.raises(SourceSchemaError, match="exactly one explicit embargo"):
        adapter(
            lambda request: response(
                request,
                html_bytes(extra=duplicate_embargo),
            )
        ).fetch()

    duplicate_title = "<div>THE EMPLOYMENT SITUATION -- JANUARY 2023</div>"
    with pytest.raises(SourceSchemaError, match="exactly one report-period title"):
        adapter(
            lambda request: response(request, html_bytes(extra=duplicate_title))
        ).fetch()

    duplicate_headline = (
        "<p>Total nonfarm payroll employment rose by 517,000 in January, and the "
        "unemployment rate changed little at 3.4 percent, the U.S. Bureau of Labor "
        "Statistics reported today.</p>"
    )
    with pytest.raises(SourceSchemaError, match="exactly one headline fact sentence"):
        adapter(
            lambda request: response(request, html_bytes(extra=duplicate_headline))
        ).fetch()


def test_invalid_html_encoding_and_content_type_fail_closed() -> None:
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
                    "request_url": (
                        "https://www.bls.gov/news.release/archives/empsit_01062023.htm"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, html_bytes(), datetime(2026, 1, 1, tzinfo=UTC)

    wrong = BLSEmploymentSituationArchiveAdapter(
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
                        "https://www.bls.gov/news.release/archives/empsit_02032023.htm"
                    ),
                    "status_code": 200,
                },
            )()
            return snapshot, html_bytes(), datetime(2023, 2, 3, 13, 29, tzinfo=UTC)

    early = BLSEmploymentSituationArchiveAdapter(
        cast(SafeHttpClient, EarlyClient()),
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
