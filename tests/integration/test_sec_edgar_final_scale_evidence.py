from __future__ import annotations

from pathlib import Path

from finreplay.scale import (
    load_sec_log_inventory_lock,
    load_sec_log_query_benchmark_receipt,
    load_sec_log_scale_manifest,
    load_sec_log_scale_verification_receipt,
    verify_sec_log_scale_verification_receipt,
)

REPOSITORY = Path(__file__).resolve().parents[2]
SCALE_ROOT = REPOSITORY / "verification" / "scale" / "sec-edgar"


def test_committed_billion_row_evidence_chain_is_self_consistent() -> None:
    manifest = load_sec_log_scale_manifest(SCALE_ROOT / "latest-scale-manifest.json")
    deep_receipt = load_sec_log_scale_verification_receipt(
        SCALE_ROOT / "latest-deep-verification-receipt.json"
    )
    benchmark = load_sec_log_query_benchmark_receipt(
        SCALE_ROOT / "latest-query-benchmark-receipt.json"
    )
    inventories = [
        load_sec_log_inventory_lock(SCALE_ROOT / "inventory" / name)
        for name in (
            "edgar2012.inventory-lock.json",
            "edgar2013.inventory-lock.json",
        )
    ]

    assert manifest.partition_count == 244
    assert manifest.total_distinct_physical_rows == 1_014_736_394
    assert manifest.total_parquet_bytes == 12_277_974_518
    assert manifest.target_met is True
    assert (
        manifest.manifest_sha256
        == "c5ba416aa05ef15b59d32f5e1c38d19779679fa9aa1fca9746ebb900f5622697"
    )

    verify_sec_log_scale_verification_receipt(
        deep_receipt,
        manifest=manifest,
        inventory_locks=inventories,
    )
    assert deep_receipt.deep is True
    assert deep_receipt.workers == 4
    assert deep_receipt.exact_physical_row_count == manifest.total_distinct_physical_rows
    assert (
        deep_receipt.verification_receipt_sha256
        == "a1c5ce99c643985c411180c9af35d3a26ce62cc243ed0c5bd6bf4035fd8d0aae"
    )

    assert benchmark.code_revision == deep_receipt.verifier_code_revision
    assert benchmark.os_cache_controlled is False
    assert benchmark.runs[0].process_id != benchmark.runs[1].process_id
    assert (
        benchmark.benchmark_receipt_sha256
        == "1e9e85a979427ba6ff24d7d206a8f5e3d39e6067017597c4b64141073af067f1"
    )
    expected_partition_hashes = tuple(
        partition.partition_receipt_sha256 for partition in manifest.partitions
    )
    latest_observation = max(
        partition.archive_retrieved_at for partition in manifest.partitions
    )
    for run in benchmark.runs:
        assert run.manifest_sha256 == manifest.manifest_sha256
        assert run.scale_target_met is True
        assert run.eligible_partition_count == manifest.partition_count
        assert run.eligible_partition_receipt_sha256s == expected_partition_hashes
        assert run.input_hashes_verified is True
        assert run.input_scan_rows == manifest.total_distinct_physical_rows
        assert run.rows_at_or_before_cutoff == run.input_scan_rows
        assert run.eligible_parquet_bytes == manifest.total_parquet_bytes
        assert run.event_cutoff_date == manifest.partitions[-1].partition_date
        assert run.event_cutoff_second == 86_399
        assert run.knowledge_cutoff >= latest_observation
        assert run.cache_state == "fresh_process_os_cache_not_controlled"
