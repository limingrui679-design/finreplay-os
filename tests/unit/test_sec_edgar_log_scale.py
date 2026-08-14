from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from finreplay.scale import (
    SEC_EDGAR_LOG_HEADER_2003_2017,
    SECLogInventoryLock,
    extract_sec_log_archive,
    load_sec_log_inventory_lock,
    parse_sec_log_inventory,
    write_sec_log_inventory_lock,
)

LIST_URL = "https://www.sec.gov/files/edgar2012.html"
RETRIEVED_AT = datetime(2026, 8, 14, 12, 45, tzinfo=UTC)


def test_inventory_uses_only_exact_observed_official_links() -> None:
    content = b"""
    <html><body>
      <a href="/dera/data/Public-EDGAR-log-file-data/2012/Qtr1/log20120102.zip">
        log20120102.zip
      </a>
      <a href="https://www.sec.gov/dera/data/Public-EDGAR-log-file-data/2012/Qtr1/log20120101.zip">log20120101.zip</a>
      <a href="notes.pdf">notes</a>
    </body></html>
    """
    lock = parse_sec_log_inventory(content, list_page_url=LIST_URL, retrieved_at=RETRIEVED_AT)

    assert lock.list_page_sha256 == hashlib.sha256(content).hexdigest()
    assert [item.partition_date.isoformat() for item in lock.partitions] == [
        "2012-01-01",
        "2012-01-02",
    ]
    assert all(
        str(item.source_url).startswith("https://www.sec.gov/dera/data/")
        for item in lock.partitions
    )
    assert SECLogInventoryLock.model_validate_json(lock.model_dump_json()) == lock


@pytest.mark.parametrize(
    "content",
    [
        b'<a href="/dera/data/Public-EDGAR-log-file-data/2012/Qtr1/'
        b'log20120102.zip">log20120101.zip</a>',
        b'<a href="/dera/data/Public-EDGAR-log-file-data/2012/Qtr2/'
        b'log20120101.zip">log20120101.zip</a>',
        b'<a href="https://example.com/log20120101.zip">log20120101.zip</a>',
        b'<a href="notes.pdf">notes</a>',
    ],
)
def test_inventory_rejects_disagreement_guessing_and_nonofficial_links(content: bytes) -> None:
    with pytest.raises((ValidationError, ValueError)):
        parse_sec_log_inventory(content, list_page_url=LIST_URL, retrieved_at=RETRIEVED_AT)


def test_inventory_rejects_tampered_self_hash() -> None:
    content = (
        b'<a href="/dera/data/Public-EDGAR-log-file-data/2012/Qtr1/'
        b'log20120101.zip">log20120101.zip</a>'
    )
    lock = parse_sec_log_inventory(content, list_page_url=LIST_URL, retrieved_at=RETRIEVED_AT)
    values = cast(dict[str, Any], json.loads(lock.model_dump_json()))
    values["lock_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="lock_sha256"):
        SECLogInventoryLock.model_validate(values)


def test_inventory_lock_round_trips_without_local_paths(tmp_path: Path) -> None:
    content = (
        b'<a href="/dera/data/Public-EDGAR-log-file-data/2012/Qtr1/'
        b'log20120101.zip">log20120101.zip</a>'
    )
    lock = parse_sec_log_inventory(content, list_page_url=LIST_URL, retrieved_at=RETRIEVED_AT)
    path = tmp_path / "inventory.json"

    write_sec_log_inventory_lock(lock, path)

    assert load_sec_log_inventory_lock(path) == lock
    assert str(tmp_path) not in path.read_text(encoding="utf-8")


def test_extract_archive_measures_csv_and_official_readme(tmp_path: Path) -> None:
    archive = tmp_path / "log20120101.zip"
    destination = tmp_path / "log20120101.csv"
    content = _csv_bytes()
    readme = b"official source notes\n"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("log20120101.csv", content)
        output.writestr("README.txt", readme)

    result = extract_sec_log_archive(
        archive,
        partition_date=date(2012, 1, 1),
        destination=destination,
    )

    assert destination.read_bytes() == content
    assert result.zip_sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert result.csv_sha256 == hashlib.sha256(content).hexdigest()
    assert result.csv_bytes == len(content)
    assert result.physical_line_count == 3
    assert result.header == SEC_EDGAR_LOG_HEADER_2003_2017
    assert result.archive_member_names == ("README.txt", "log20120101.csv")
    assert result.readme_bytes == len(readme)
    assert result.readme_sha256 == hashlib.sha256(readme).hexdigest()
    assert result.readme_crc32 is not None


def test_extract_archive_allows_csv_without_readme(tmp_path: Path) -> None:
    archive = tmp_path / "log20120101.zip"
    destination = tmp_path / "log20120101.csv"
    _write_zip(archive, "log20120101.csv", _csv_bytes())

    result = extract_sec_log_archive(
        archive,
        partition_date=date(2012, 1, 1),
        destination=destination,
    )

    assert result.archive_member_names == ("log20120101.csv",)
    assert result.readme_bytes is None
    assert result.readme_sha256 is None
    assert result.readme_crc32 is None


@pytest.mark.parametrize(
    ("member_name", "content", "max_bytes", "message"),
    [
        ("../log20120101.csv", None, 10_000, "members"),
        ("log20120102.csv", None, 10_000, "members"),
        ("log20120101.csv", b"wrong,header\n1,2\n", 10_000, "header"),
        ("log20120101.csv", None, 10, "uncompressed size"),
    ],
)
def test_extract_archive_fails_closed(
    tmp_path: Path,
    member_name: str,
    content: bytes | None,
    max_bytes: int,
    message: str,
) -> None:
    archive = tmp_path / "input.zip"
    _write_zip(archive, member_name, content or _csv_bytes())
    with pytest.raises(ValueError, match=message):
        extract_sec_log_archive(
            archive,
            partition_date=date(2012, 1, 1),
            destination=tmp_path / "output.csv",
            max_uncompressed_bytes=max_bytes,
        )


def test_extract_archive_rejects_unexpected_members(tmp_path: Path) -> None:
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("log20120101.csv", _csv_bytes())
        output.writestr("extra.txt", b"unexpected")
    with pytest.raises(ValueError, match="members"):
        extract_sec_log_archive(
            archive,
            partition_date=date(2012, 1, 1),
            destination=tmp_path / "output.csv",
        )


def _csv_bytes() -> bytes:
    header = ",".join(SEC_EDGAR_LOG_HEADER_2003_2017)
    return (
        header
        + "\n"
        + "101.102.103.abc,2012-01-01,00:00:01,0.0,1234,"
        + "0000001234-12-000001,doc.htm,200,100,1,0,0,7,0,chr\n"
        + "101.102.103.def,2012-01-01,23:59:59,0.0,1234,"
        + "0000001234-12-000001,doc.htm,304,0,0,1,0,0,1,bot\n"
    ).encode()


def _write_zip(path: Path, member_name: str, content: bytes) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr(member_name, content)
