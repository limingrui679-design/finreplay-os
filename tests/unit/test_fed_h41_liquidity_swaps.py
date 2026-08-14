from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from finreplay.adapters import FederalReserveH41LiquiditySwapsAdapter, SourceSchemaError
from finreplay.adapters.base import SafeHttpClient
from finreplay.contracts import EvidenceClass, LicenseClass, TemporalCoverage
from finreplay.engines import TimeVault

RELEASE_DATE = date(2020, 3, 26)
FOOTNOTE = (
    "Dollar value of foreign currency held under these agreements valued at the exchange rate "
    "to be used when the foreign currency is returned to the foreign central bank. This "
    "exchange rate equals the market exchange rate used when the foreign currency was acquired "
    "from the foreign central bank."
)


def html_bytes(
    *,
    release_label: str = "March 26, 2020",
    week_label: str = "Mar 25, 2020",
    row: tuple[str, ...] = (
        "Central bank liquidity swaps 8",
        "168,814",
        "+ 168,769",
        "+ 168,748",
        "206,051",
    ),
    time_marker: str = "For Release at 4:30 P.M. ED T March 26, 2020",
    footnote: str = FOOTNOTE,
    extra_row: str = "",
    injected_token: str = "first",
) -> bytes:
    cells = "".join(f"<td>{value}</td>" for value in row)
    return f"""
    <!doctype html><html><head><title>H.4.1 {release_label}</title>
    <script>ignored {injected_token}</script></head><body>
      <p>Release Date: Thursday, {release_label}</p>
      <p>{time_marker}</p>
      <p>Factors Affecting Reserve Balances of Depository Institutions</p>
      <table>
        <tr><th>Reserve Bank credit</th><th>Averages of daily figures</th>
          <th>Wednesday {week_label}</th></tr>
        <tr><th>Week ended {week_label}</th><th>Change from week ended</th></tr>
        <tr>{cells}</tr>{extra_row}
      </table>
      <p>{footnote}</p>
    </body></html>
    """.encode()


def txt_bytes(
    *,
    release_label: str = "March 26, 2020",
    week_label: str = "Mar 25, 2020",
    row: str = (
        "  Central bank liquidity swaps (8)  168,814  +  168,769  "
        "+  168,748  206,051"
    ),
    footnote: str = FOOTNOTE,
) -> bytes:
    return f"""
    FEDERAL RESERVE statistical release
    For Release at 4:30 P.M. EDT {release_label}
    Factors Affecting Reserve Balances of Depository Institutions
    Averages of daily figures Wednesday {week_label}
    reserve balances Week ended Change from week ended {week_label}
    {row}
    {footnote}
    """.encode("utf-8-sig")


def response(
    request: httpx.Request,
    content: bytes,
    content_type: str,
) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"Content-Type": content_type},
        request=request,
    )


def adapter(
    html: bytes,
    txt: bytes,
    *,
    html_type: str = "text/html; charset=utf-8",
    txt_type: str = "text/plain; charset=utf-8",
    release_date: date = RELEASE_DATE,
) -> FederalReserveH41LiquiditySwapsAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/h41.htm"):
            return response(request, html, html_type)
        return response(request, txt, txt_type)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    safe = SafeHttpClient(user_agent="FinReplay tests@example.invalid", client=client)
    return FederalReserveH41LiquiditySwapsAdapter(safe, release_date=release_date)


def test_paired_swap_release_is_versioned_exact_and_crosschecked() -> None:
    batch = adapter(html_bytes(), txt_bytes()).fetch()

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.record_id.endswith("20200325:wednesday_outstanding")
    assert record.entity_id == "federal_reserve_facility:central_bank_liquidity_swaps"
    assert record.source.temporal_coverage is TemporalCoverage.VERSIONED_SNAPSHOT
    assert record.source.license_class is LicenseClass.DOWNLOAD_ONLY
    assert record.source.vintage_as_of == datetime(2020, 3, 26, 20, 30, tzinfo=UTC)
    assert record.interval.available_at == datetime(2020, 3, 26, 20, 30, tzinfo=UTC)
    assert record.interval.valid_from == datetime(2020, 3, 25, tzinfo=UTC)
    assert record.interval.availability_confidence == 1.0
    assert record.evidence_class is EvidenceClass.REPORTED
    assert record.payload["value_millions"] == 206_051
    assert record.payload["weekly_average_millions"] == 168_814
    assert record.payload["weekly_average_change_from_prior_week_millions"] == 168_769
    assert record.payload["weekly_average_change_from_year_ago_millions"] == 168_748
    assert record.payload["html_ascii_crosscheck_verified"] is True
    assert record.payload["official_stated_release_at"] == "2020-03-26T20:30:00+00:00"
    assert record.payload["actual_server_publication_log_available"] is False
    assert record.source.sha256 == record.payload["release_semantic_sha256"]
    assert record.source.sha256 not in {receipt.response_sha256 for receipt in batch.receipts}
    assert [receipt.record_count for receipt in batch.receipts] == [0, 1]
    assert all(receipt.historical_replay_eligible for receipt in batch.receipts)
    assert len(batch.artifacts) == 2

    with TimeVault() as vault:
        first = vault.append(batch.records)
        second = vault.append(batch.records)
        assert first.inserted_records == 1
        assert second.idempotent_records == 1


def test_dynamic_html_wrapper_does_not_change_financial_record_semantics() -> None:
    first = adapter(html_bytes(injected_token="one"), txt_bytes()).fetch()
    second = adapter(html_bytes(injected_token="two"), txt_bytes()).fetch()
    assert first.receipts[0].response_sha256 != second.receipts[0].response_sha256
    assert first.records[0].source.sha256 == second.records[0].source.sha256
    assert first.records[0].payload == second.records[0].payload


def test_date_only_event_uses_following_new_york_midnight() -> None:
    release_date = date(2020, 4, 2)
    html = html_bytes(
        release_label="April 2, 2020",
        week_label="Apr 1, 2020",
        row=(
            "Central bank liquidity swaps 8",
            "327,787",
            "+ 158,973",
            "+ 326,422",
            "348,544",
        ),
        time_marker="",
    )
    txt = txt_bytes(
        release_label="April 2, 2020",
        week_label="Apr 1, 2020",
        row="  Central bank liquidity swaps (8) 327,787 + 158,973 + 326,422 348,544",
    )
    record = adapter(html, txt, release_date=release_date).fetch().records[0]
    assert record.interval.available_at == datetime(2020, 4, 3, 4, tzinfo=UTC)
    assert record.source.vintage_as_of == datetime(2020, 4, 3, 4, tzinfo=UTC)
    assert record.payload["official_stated_release_at"] is None
    assert record.payload["release_time_local"] is None
    assert (
        record.payload["availability_method"]
        == "release_date_following_new_york_midnight_html_ascii"
    )


@pytest.mark.parametrize(
    ("html", "txt", "match"),
    [
        (
            html_bytes(time_marker="For Release at 4:30 P.M. EST March 26, 2020"),
            txt_bytes(),
            "release date or time",
        ),
        (
            html_bytes(footnote="missing"),
            txt_bytes(),
            "HTML liquidity-swap measurement footnote",
        ),
        (
            html_bytes(),
            txt_bytes(footnote="missing"),
            "ASCII liquidity-swap measurement footnote",
        ),
        (
            html_bytes(
                extra_row=(
                    "<tr><td>Central bank liquidity swaps 8</td><td>168,814</td>"
                    "<td>+ 168,769</td><td>+ 168,748</td><td>206,051</td></tr>"
                )
            ),
            txt_bytes(),
            "one Table 1",
        ),
        (
            html_bytes(row=("Central bank liquidity swaps 8", "bad", "1", "1", "1")),
            txt_bytes(),
            "weekly average",
        ),
        (
            html_bytes(),
            txt_bytes(
                row="  Central bank liquidity swaps (8) 168,814 + 168,770 + 168,748 206,051"
            ),
            "do not agree",
        ),
        (
            html_bytes(week_label="Mar 24, 2020"),
            txt_bytes(week_label="Mar 24, 2020"),
            "ASCII week-ending header",
        ),
        (
            html_bytes(),
            txt_bytes(row="Central bank liquidity swaps missing"),
            "one Table 1",
        ),
    ],
)
def test_release_corruption_fails_closed(html: bytes, txt: bytes, match: str) -> None:
    with pytest.raises(SourceSchemaError, match=match):
        adapter(html, txt).fetch()


@pytest.mark.parametrize(
    ("html_type", "txt_type", "match"),
    [
        ("application/json", "text/plain", "HTML content type"),
        ("text/html", "application/json", "ASCII content type"),
    ],
)
def test_content_types_fail_closed(html_type: str, txt_type: str, match: str) -> None:
    with pytest.raises(SourceSchemaError, match=match):
        adapter(
            html_bytes(),
            txt_bytes(),
            html_type=html_type,
            txt_type=txt_type,
        ).fetch()


def test_invalid_encoding_unsupported_date_and_response_url_fail_closed() -> None:
    with pytest.raises(SourceSchemaError, match="HTML is not valid UTF-8"):
        adapter(b"\xff\xfe", txt_bytes()).fetch()
    with pytest.raises(SourceSchemaError, match="ASCII release is not valid UTF-8"):
        adapter(html_bytes(), b"\xff\xfe").fetch()
    with pytest.raises(ValueError, match=r"verified H\.4\.1 swap calendar"):
        adapter(html_bytes(), txt_bytes(), release_date=date(2020, 3, 12))

    class WrongURLClient:
        def get(self, endpoint: str, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            is_html = endpoint.endswith("h41.htm")
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "text/html" if is_html else "text/plain"},
                    "request_url": endpoint.replace("20200326", "20200319"),
                    "status_code": 200,
                },
            )()
            content = html_bytes() if is_html else txt_bytes()
            return snapshot, content, datetime(2026, 1, 1, tzinfo=UTC)

    wrong = FederalReserveH41LiquiditySwapsAdapter(
        WrongURLClient(),  # type: ignore[arg-type]
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="response URL"):
        wrong.fetch()


def test_future_release_cannot_be_backdated() -> None:
    class EarlyClient:
        def get(self, endpoint: str, **_kwargs: Any) -> tuple[Any, bytes, datetime]:
            is_html = endpoint.endswith("h41.htm")
            snapshot = type(
                "Snapshot",
                (),
                {
                    "headers": {"Content-Type": "text/html" if is_html else "text/plain"},
                    "request_url": endpoint,
                    "status_code": 200,
                },
            )()
            content = html_bytes() if is_html else txt_bytes()
            return snapshot, content, datetime(2020, 3, 26, 20, 29, tzinfo=UTC)

    early = FederalReserveH41LiquiditySwapsAdapter(
        EarlyClient(),  # type: ignore[arg-type]
        release_date=RELEASE_DATE,
    )
    with pytest.raises(SourceSchemaError, match="not yet knowable"):
        early.fetch()
