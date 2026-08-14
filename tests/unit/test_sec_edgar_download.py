from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from finreplay.scale import (
    SECLogDownloadReceipt,
    SECLogPartition,
    SECLogRetryableDownloadError,
    download_sec_log_archive,
    fetch_sec_log_inventory,
    load_sec_log_download_receipt,
    write_sec_log_download_receipt,
)

USER_AGENT = "FinReplay OS test contact test@example.com"
LIST_URL = "https://www.sec.gov/files/edgar2012.html"
SOURCE_URL = "https://www.sec.gov/dera/data/Public-EDGAR-log-file-data/2012/Qtr1/log20120101.zip"
LISTED_URL = SOURCE_URL.replace("https://", "http://", 1)


class _FailAfterPrefix(httpx.SyncByteStream):
    def __init__(self, prefix: bytes) -> None:
        self.prefix = prefix

    def __iter__(self) -> Iterator[bytes]:
        yield self.prefix
        raise httpx.ReadError("simulated interrupted response")


def test_fetch_inventory_locks_exact_response_bytes() -> None:
    content = (
        b'<a href="/dera/data/Public-EDGAR-log-file-data/2012/Qtr1/'
        b'log20120101.zip">log20120101.zip</a>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == USER_AGENT
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(content))},
            content=content,
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    lock, observed = fetch_sec_log_inventory(
        list_page_url=LIST_URL,
        user_agent=USER_AGENT,
        client=client,
    )

    assert observed == content
    assert lock.list_page_sha256 == hashlib.sha256(content).hexdigest()
    assert [item.partition_date for item in lock.partitions] == [date(2012, 1, 1)]


def test_download_complete_and_verify_existing_without_recontact(tmp_path: Path) -> None:
    content = b"complete official archive bytes"
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert "Range" not in request.headers
        return httpx.Response(
            200,
            headers={
                "Content-Length": str(len(content)),
                "ETag": '"source-v1"',
                "Last-Modified": "Thu, 14 Aug 2026 00:00:00 GMT",
            },
            content=content,
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    destination = tmp_path / "log20120101.zip"
    receipt = download_sec_log_archive(
        _partition(),
        destination=destination,
        user_agent=USER_AGENT,
        client=client,
    )

    assert destination.read_bytes() == content
    assert receipt.mode == "downloaded"
    assert receipt.archive_sha256 == hashlib.sha256(content).hexdigest()
    assert receipt.network_response_sha256 == receipt.archive_sha256
    assert requests == 1

    existing = download_sec_log_archive(
        _partition(),
        destination=destination,
        user_agent=USER_AGENT,
        expected_bytes=len(content),
        expected_sha256=receipt.archive_sha256,
        client=client,
    )
    assert existing.mode == "verified_existing"
    assert existing.network_response_bytes == 0
    assert requests == 1

    receipt_path = tmp_path / "download-receipt.json"
    write_sec_log_download_receipt(existing, receipt_path)
    assert load_sec_log_download_receipt(receipt_path) == existing
    assert USER_AGENT not in receipt_path.read_text(encoding="utf-8")


def test_download_resumes_only_with_matching_range_validator(tmp_path: Path) -> None:
    content = b"0123456789abcdef"
    prefix = content[:5]
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                headers={
                    "Content-Length": str(len(content)),
                    "ETag": '"stable-v1"',
                },
                stream=_FailAfterPrefix(prefix),
                request=request,
            )
        assert request.headers["Range"] == f"bytes={len(prefix)}-"
        assert request.headers["If-Range"] == '"stable-v1"'
        remaining = content[len(prefix) :]
        return httpx.Response(
            206,
            headers={
                "Content-Length": str(len(remaining)),
                "Content-Range": (f"bytes {len(prefix)}-{len(content) - 1}/{len(content)}"),
                "ETag": '"stable-v1"',
            },
            content=remaining,
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    destination = tmp_path / "log20120101.zip"
    with pytest.raises(ValueError, match="resumable partial bytes were kept"):
        download_sec_log_archive(
            _partition(),
            destination=destination,
            user_agent=USER_AGENT,
            client=client,
        )
    assert (tmp_path / ".log20120101.zip.part").read_bytes() == prefix
    assert (tmp_path / ".log20120101.zip.part.json").is_file()

    receipt = download_sec_log_archive(
        _partition(),
        destination=destination,
        user_agent=USER_AGENT,
        client=client,
    )

    assert receipt.mode == "resumed"
    assert receipt.resumed_from_bytes == len(prefix)
    assert receipt.network_response_bytes == len(content) - len(prefix)
    assert destination.read_bytes() == content
    assert not (tmp_path / ".log20120101.zip.part").exists()
    assert not (tmp_path / ".log20120101.zip.part.json").exists()


def test_download_receipt_rejects_tampered_hash(tmp_path: Path) -> None:
    content = b"archive"
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"Content-Length": str(len(content))},
                content=content,
                request=request,
            )
        )
    )
    receipt = download_sec_log_archive(
        _partition(),
        destination=tmp_path / "log20120101.zip",
        user_agent=USER_AGENT,
        client=client,
    )
    values = cast(dict[str, Any], json.loads(receipt.model_dump_json()))
    values["receipt_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="receipt_sha256"):
        SECLogDownloadReceipt.model_validate(values)


def test_download_classifies_retryable_status_and_retry_after(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            headers={"Retry-After": "17"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SECLogRetryableDownloadError) as observed:
        download_sec_log_archive(
            _partition(),
            destination=tmp_path / "log20120101.zip",
            user_agent=USER_AGENT,
            client=client,
        )

    assert observed.value.status_code == 503
    assert observed.value.retry_after_seconds == 17.0


def test_download_does_not_retry_classify_permanent_http_status(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="HTTP 404") as observed:
        download_sec_log_archive(
            _partition(),
            destination=tmp_path / "log20120101.zip",
            user_agent=USER_AGENT,
            client=client,
        )

    assert not isinstance(observed.value, SECLogRetryableDownloadError)


def _partition() -> SECLogPartition:
    return SECLogPartition.model_validate(
        {
            "partition_date": "2012-01-01",
            "listed_url": LISTED_URL,
            "source_url": SOURCE_URL,
            "list_page_url": LIST_URL,
        }
    )
