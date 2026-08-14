from __future__ import annotations

import json
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest
from pydantic import ValidationError

from finreplay.scale import (
    SEC_EDGAR_LOG_HEADER_2003_2017,
    SECLogDownloadReceipt,
    SECLogExtractedCSV,
    SECLogPartition,
    SECLogPartitionReceipt,
    download_sec_log_archive,
    extract_sec_log_archive,
    load_sec_log_partition_receipt,
    materialize_sec_log_csv,
    verify_sec_log_partition_receipt,
    write_sec_log_partition_receipt,
)

PARTITION_DATE = date(2012, 1, 1)
LIST_URL = "https://www.sec.gov/files/edgar2012.html"
SOURCE_URL = "https://www.sec.gov/dera/data/Public-EDGAR-log-file-data/2012/Qtr1/log20120101.zip"
LISTED_URL = SOURCE_URL.replace("https://", "http://", 1)
USER_AGENT = "FinReplay OS test contact test@example.com"


def test_materialize_retains_valid_and_invalid_physical_rows(tmp_path: Path) -> None:
    archive, extracted, parquet, download = _source_artifacts(tmp_path)
    receipt = materialize_sec_log_csv(
        extracted,
        partition=_partition(),
        download_receipt=download,
        parquet_path=parquet,
        materialized_at=download.observation_completed_at + timedelta(seconds=1),
    )

    assert receipt.data_row_count == 2
    assert receipt.parquet_row_count == 2
    assert receipt.first_row_ordinal == 1
    assert receipt.last_row_ordinal == 2
    assert receipt.ordinal_sequence_mismatch_count == 0
    assert receipt.invalid_counts.rows_with_any_invalid == 1
    assert all(
        value == 1
        for name, value in receipt.invalid_counts.model_dump().items()
        if name != "rows_with_any_invalid"
    )
    rows = (
        duckdb.connect()
        .execute(
            "SELECT row_ordinal, cik, accession, document, invalid_mask "
            "FROM read_parquet(?) ORDER BY row_ordinal",
            [str(parquet)],
        )
        .fetchall()
    )
    assert rows[0] == (1, 1234, "0000001234-12-000001", "doc.htm", 0)
    assert rows[1] == (2, None, "bad", "", 32_767)

    receipt_path = tmp_path / "receipt.json"
    write_sec_log_partition_receipt(receipt, receipt_path)
    assert load_sec_log_partition_receipt(receipt_path) == receipt
    assert str(tmp_path) not in receipt_path.read_text(encoding="utf-8")
    verify_sec_log_partition_receipt(
        receipt,
        download_receipt=download,
        archive_path=archive,
        parquet_path=parquet,
    )


def test_receipt_rejects_tampered_self_hash(tmp_path: Path) -> None:
    _, extracted, parquet, download = _source_artifacts(tmp_path)
    receipt = materialize_sec_log_csv(
        extracted,
        partition=_partition(),
        download_receipt=download,
        parquet_path=parquet,
        materialized_at=download.observation_completed_at + timedelta(seconds=1),
    )
    values = cast(dict[str, Any], json.loads(receipt.model_dump_json()))
    values["receipt_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="receipt_sha256"):
        SECLogPartitionReceipt.model_validate(values)


def test_materialize_rejects_csv_changed_after_extraction(tmp_path: Path) -> None:
    _, extracted, parquet, download = _source_artifacts(tmp_path)
    with extracted.csv_path.open("ab") as output:
        output.write(b"unexpected")

    with pytest.raises(ValueError, match="byte count changed"):
        materialize_sec_log_csv(
            extracted,
            partition=_partition(),
            download_receipt=download,
            parquet_path=parquet,
            materialized_at=download.observation_completed_at + timedelta(seconds=1),
        )


def test_verifier_rejects_changed_parquet_bytes(tmp_path: Path) -> None:
    archive, extracted, parquet, download = _source_artifacts(tmp_path)
    receipt = materialize_sec_log_csv(
        extracted,
        partition=_partition(),
        download_receipt=download,
        parquet_path=parquet,
        materialized_at=download.observation_completed_at + timedelta(seconds=1),
    )
    with parquet.open("ab") as output:
        output.write(b"tamper")

    with pytest.raises(ValueError, match="Parquet byte count mismatch"):
        verify_sec_log_partition_receipt(
            receipt,
            download_receipt=download,
            archive_path=archive,
            parquet_path=parquet,
        )


def _source_artifacts(
    tmp_path: Path,
) -> tuple[Path, SECLogExtractedCSV, Path, SECLogDownloadReceipt]:
    archive = tmp_path / "log20120101.zip"
    csv_path = tmp_path / "log20120101.csv"
    parquet = tmp_path / "log20120101.parquet"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("log20120101.csv", _csv_bytes())
        output.writestr("README.txt", b"official notes\n")
    download = download_sec_log_archive(
        _partition(),
        destination=archive,
        user_agent=USER_AGENT,
    )
    extracted = extract_sec_log_archive(
        archive,
        partition_date=PARTITION_DATE,
        destination=csv_path,
    )
    return archive, extracted, parquet, download


def _partition() -> SECLogPartition:
    return SECLogPartition.model_validate(
        {
            "partition_date": PARTITION_DATE,
            "listed_url": LISTED_URL,
            "source_url": SOURCE_URL,
            "list_page_url": LIST_URL,
        }
    )


def _csv_bytes() -> bytes:
    header = ",".join(SEC_EDGAR_LOG_HEADER_2003_2017)
    valid = (
        "101.102.103.abc,2012-01-01,00:00:01,500.0,1234,"
        "0000001234-12-000001,doc.htm,200,100,1,0,0,7,0,chr"
    )
    invalid = ",2012-01-02,25:00:00,bad,bad,bad,,bad,bad,2,2,2,99,2,bad"
    return f"{header}\n{valid}\n{invalid}\n".encode()
