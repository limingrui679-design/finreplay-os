from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest
from pydantic import ValidationError

from finreplay.scale import (
    SECLogAsOfQueryReceipt,
    SECLogScaleManifest,
    SECLogScalePartitionSummary,
    load_sec_log_asof_query_receipt,
    load_sec_log_scale_manifest,
    run_sec_log_asof_query,
    write_sec_log_asof_query_receipt,
)

REPOSITORY = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPOSITORY / "verification" / "scale" / "sec-edgar" / "latest-scale-manifest.json"


def test_query_does_not_backdate_archive_availability(tmp_path: Path) -> None:
    manifest = load_sec_log_scale_manifest(MANIFEST_PATH)
    receipt = run_sec_log_asof_query(
        manifest,
        parquet_directory=tmp_path,
        event_cutoff_date=date(2012, 1, 1),
        event_cutoff_second=86_399,
        knowledge_cutoff=datetime(2020, 1, 1, tzinfo=UTC),
        executed_at=datetime(2026, 8, 14, 16, 0, tzinfo=UTC),
        cache_state="fresh_process_os_cache_not_controlled",
    )

    assert receipt.eligible_partition_count == 0
    assert receipt.input_scan_rows == 0
    assert receipt.rows_at_or_before_cutoff == 0
    output = tmp_path / "query-receipt.json"
    write_sec_log_asof_query_receipt(receipt, output)
    assert load_sec_log_asof_query_receipt(output) == receipt
    assert str(tmp_path) not in output.read_text(encoding="utf-8")


def test_query_hashes_inputs_and_measures_cutoff_aggregates(tmp_path: Path) -> None:
    parquet = tmp_path / "log20120101.parquet"
    connection = duckdb.connect()
    connection.execute(
        """
        CREATE TABLE events(
            event_date DATE,
            event_time_seconds UINTEGER,
            invalid_mask USMALLINT,
            is_crawler BOOLEAN,
            status_code USMALLINT,
            cik UBIGINT,
            document_size UBIGINT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO events VALUES
            ('2012-01-01', 1, 0, false, 200, 1, 10),
            ('2012-01-01', 100, 1, true, 404, 2, 20),
            (NULL, NULL, 4, false, 200, 3, 30)
        """
    )
    connection.execute("COPY events TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(parquet)])
    connection.close()
    manifest = _synthetic_manifest(parquet)

    receipt = run_sec_log_asof_query(
        manifest,
        parquet_directory=tmp_path,
        event_cutoff_date=date(2012, 1, 1),
        event_cutoff_second=50,
        knowledge_cutoff=datetime(2026, 8, 14, 15, 0, tzinfo=UTC),
        executed_at=datetime(2026, 8, 14, 16, 0, tzinfo=UTC),
        cache_state="fresh_process_os_cache_not_controlled",
        threads=1,
    )

    assert receipt.input_hashes_verified is True
    assert receipt.input_scan_rows == 3
    assert receipt.temporal_invalid_rows == 1
    assert receipt.rows_at_or_before_cutoff == 1
    assert receipt.invalid_rows_at_or_before_cutoff == 0
    assert receipt.crawler_rows_at_or_before_cutoff == 0
    assert receipt.http_success_rows_at_or_before_cutoff == 1
    assert receipt.unique_ciks_at_or_before_cutoff == 1
    assert receipt.document_bytes_at_or_before_cutoff == 10


def test_query_receipt_rejects_tampered_self_hash(tmp_path: Path) -> None:
    manifest = load_sec_log_scale_manifest(MANIFEST_PATH)
    receipt = run_sec_log_asof_query(
        manifest,
        parquet_directory=tmp_path,
        event_cutoff_date=date(2012, 1, 1),
        event_cutoff_second=0,
        knowledge_cutoff=datetime(2020, 1, 1, tzinfo=UTC),
        executed_at=datetime(2026, 8, 14, 16, 0, tzinfo=UTC),
        cache_state="same_process_os_cache_not_controlled",
    )
    values = cast(dict[str, Any], json.loads(receipt.model_dump_json()))
    values["receipt_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="receipt_sha256"):
        SECLogAsOfQueryReceipt.model_validate(values)


def _synthetic_manifest(parquet: Path) -> SECLogScaleManifest:
    base = load_sec_log_scale_manifest(MANIFEST_PATH)
    summary_values = base.partitions[0].model_dump(mode="json")
    summary_values.update(
        {
            "parquet_sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(),
            "parquet_bytes": parquet.stat().st_size,
            "physical_row_count": 3,
            "invalid_row_count": 1,
            "archive_retrieved_at": "2026-08-14T14:00:00Z",
        }
    )
    summary = SECLogScalePartitionSummary.model_validate(summary_values)
    values = base.model_dump(mode="json", exclude={"manifest_sha256"})
    values.update(
        {
            "generated_at": "2026-08-14T15:00:00Z",
            "target_physical_row_count": 3,
            "target_met": True,
            "partitions": [summary.model_dump(mode="json")],
            "partition_count": 1,
            "total_distinct_physical_rows": 3,
            "total_invalid_rows": 1,
            "total_source_zip_bytes": summary.zip_bytes,
            "total_parquet_bytes": summary.parquet_bytes,
        }
    )
    return SECLogScaleManifest.create(values)
