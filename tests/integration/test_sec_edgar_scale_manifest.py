from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from finreplay.scale import (
    SECLogScaleManifest,
    build_sec_log_scale_manifest,
    load_sec_log_download_receipt,
    load_sec_log_inventory_lock,
    load_sec_log_partition_receipt,
    load_sec_log_scale_manifest,
    verify_sec_log_scale_manifest,
    write_sec_log_scale_manifest,
)

REPOSITORY = Path(__file__).resolve().parents[2]
SCALE_ROOT = REPOSITORY / "verification" / "scale" / "sec-edgar"


def test_committed_sec_log_checkpoint_rebuilds_without_estimates(tmp_path: Path) -> None:
    inventory = load_sec_log_inventory_lock(
        SCALE_ROOT / "inventory" / "edgar2012.inventory-lock.json"
    )
    download = load_sec_log_download_receipt(
        SCALE_ROOT / "downloads" / "log20120101.download-receipt.json"
    )
    receipt = load_sec_log_partition_receipt(SCALE_ROOT / "partitions" / "log20120101.receipt.json")
    generated_at = datetime(2026, 8, 14, 15, 30, tzinfo=UTC)

    manifest = build_sec_log_scale_manifest(
        inventory_locks=[inventory],
        partition_receipts=[receipt],
        target_physical_row_count=1_000_000_000,
        code_revision="5826831",
        generated_at=generated_at,
    )

    assert manifest.partition_count == 1
    assert manifest.total_distinct_physical_rows == 1_850_071
    assert manifest.total_invalid_rows == 3_463
    assert manifest.target_met is False
    assert manifest.partitions[0].inventory_lock_sha256 == inventory.lock_sha256
    assert manifest.partitions[0].download_receipt_sha256 == download.receipt_sha256
    path = tmp_path / "manifest.json"
    write_sec_log_scale_manifest(manifest, path)
    assert load_sec_log_scale_manifest(path) == manifest
    verify_sec_log_scale_manifest(
        manifest,
        inventory_locks=[inventory],
        partition_receipts=[receipt],
        download_receipts=[download],
        archive_directory=tmp_path,
        parquet_directory=tmp_path,
        deep=False,
    )


def test_manifest_rejects_duplicate_partition_and_tampered_hash() -> None:
    inventory = load_sec_log_inventory_lock(
        SCALE_ROOT / "inventory" / "edgar2012.inventory-lock.json"
    )
    receipt = load_sec_log_partition_receipt(SCALE_ROOT / "partitions" / "log20120101.receipt.json")
    generated_at = datetime(2026, 8, 14, 15, 30, tzinfo=UTC)

    with pytest.raises(ValidationError, match="partition dates"):
        build_sec_log_scale_manifest(
            inventory_locks=[inventory],
            partition_receipts=[receipt, receipt],
            target_physical_row_count=1,
            code_revision="5826831",
            generated_at=generated_at,
        )

    manifest = build_sec_log_scale_manifest(
        inventory_locks=[inventory],
        partition_receipts=[receipt],
        target_physical_row_count=1,
        code_revision="5826831",
        generated_at=generated_at,
    )
    assert manifest.target_met is True
    values = cast(dict[str, Any], json.loads(manifest.model_dump_json()))
    values["manifest_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="manifest_sha256"):
        SECLogScaleManifest.model_validate(values)
