from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from finreplay.scale import (
    SECLogAsOfQueryReceipt,
    SECLogQueryBenchmarkReceipt,
    build_sec_log_query_benchmark_receipt,
    load_sec_log_query_benchmark_receipt,
    load_sec_log_scale_manifest,
    run_sec_log_asof_query,
    write_sec_log_query_benchmark_receipt,
)

REPOSITORY = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPOSITORY / "verification" / "scale" / "sec-edgar" / "latest-scale-manifest.json"


def test_benchmark_binds_two_comparable_fresh_process_receipts(tmp_path: Path) -> None:
    first, second = _query_pair(tmp_path)
    benchmark = build_sec_log_query_benchmark_receipt(
        first=first,
        second=second,
        code_revision="c919136",
        generated_at=datetime(2026, 8, 14, 17, 0, tzinfo=UTC),
    )

    assert benchmark.runs[0].process_id != benchmark.runs[1].process_id
    assert benchmark.os_cache_controlled is False
    assert benchmark.hardware.logical_cpu_count > 0
    assert benchmark.hardware.physical_memory_bytes > 0
    path = tmp_path / "benchmark.json"
    write_sec_log_query_benchmark_receipt(benchmark, path)
    assert load_sec_log_query_benchmark_receipt(path) == benchmark


def test_benchmark_rejects_same_process_and_tampered_hash(tmp_path: Path) -> None:
    first, second = _query_pair(tmp_path)
    with pytest.raises(ValidationError, match="distinct processes"):
        build_sec_log_query_benchmark_receipt(
            first=first,
            second=first,
            code_revision="c919136",
            generated_at=datetime(2026, 8, 14, 17, 0, tzinfo=UTC),
        )

    benchmark = build_sec_log_query_benchmark_receipt(
        first=first,
        second=second,
        code_revision="c919136",
        generated_at=datetime(2026, 8, 14, 17, 0, tzinfo=UTC),
    )
    values = cast(dict[str, Any], json.loads(benchmark.model_dump_json()))
    values["benchmark_receipt_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="benchmark_receipt_sha256"):
        SECLogQueryBenchmarkReceipt.model_validate(values)


def _query_pair(tmp_path: Path) -> tuple[SECLogAsOfQueryReceipt, SECLogAsOfQueryReceipt]:
    manifest = load_sec_log_scale_manifest(MANIFEST_PATH)
    first = run_sec_log_asof_query(
        manifest,
        parquet_directory=tmp_path,
        event_cutoff_date=date(2012, 1, 1),
        event_cutoff_second=0,
        knowledge_cutoff=datetime(2020, 1, 1, tzinfo=UTC),
        executed_at=datetime(2026, 8, 14, 16, 0, tzinfo=UTC),
        cache_state="fresh_process_os_cache_not_controlled",
    )
    values = first.model_dump(mode="json", exclude={"receipt_sha256"})
    values["process_id"] = first.process_id + 1
    second = SECLogAsOfQueryReceipt.create(values)
    return first, second
