from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

import finreplay.scale.sec_edgar_manifest as manifest_module
from finreplay.scale import (
    SECLogScaleManifest,
    SECLogScaleVerificationReceipt,
    build_sec_log_scale_manifest,
    build_sec_log_scale_verification_receipt,
    load_sec_log_download_receipt,
    load_sec_log_inventory_lock,
    load_sec_log_partition_receipt,
    load_sec_log_scale_manifest,
    load_sec_log_scale_verification_receipt,
    verify_sec_log_scale_manifest,
    verify_sec_log_scale_verification_receipt,
    write_sec_log_scale_manifest,
    write_sec_log_scale_verification_receipt,
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


def test_deep_verification_receipt_is_durable_and_manifest_bound(tmp_path: Path) -> None:
    inventory = load_sec_log_inventory_lock(
        SCALE_ROOT / "inventory" / "edgar2012.inventory-lock.json"
    )
    receipt = load_sec_log_partition_receipt(SCALE_ROOT / "partitions" / "log20120101.receipt.json")
    manifest = build_sec_log_scale_manifest(
        inventory_locks=[inventory],
        partition_receipts=[receipt],
        target_physical_row_count=1_000_000,
        code_revision="427026d",
        generated_at=datetime(2026, 8, 15, 1, 0, tzinfo=UTC),
    )
    verification_receipt = build_sec_log_scale_verification_receipt(
        manifest=manifest,
        inventory_locks=[inventory],
        verification_started_at=datetime(2026, 8, 15, 2, 0, tzinfo=UTC),
        verification_completed_at=datetime(2026, 8, 15, 2, 1, tzinfo=UTC),
        verifier_code_revision="abcdef1",
        workers=4,
        duration_seconds=60.0,
    )
    path = tmp_path / "deep-verification.json"
    write_sec_log_scale_verification_receipt(verification_receipt, path)
    loaded = load_sec_log_scale_verification_receipt(path)

    assert loaded == verification_receipt
    assert loaded.deep is True
    assert loaded.target_met is True
    verify_sec_log_scale_verification_receipt(
        loaded,
        manifest=manifest,
        inventory_locks=[inventory],
    )
    values = cast(dict[str, Any], json.loads(loaded.model_dump_json()))
    values["verification_receipt_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="verification_receipt_sha256"):
        SECLogScaleVerificationReceipt.model_validate(values)

    with pytest.raises(ValidationError, match="cannot predate"):
        build_sec_log_scale_verification_receipt(
            manifest=manifest,
            inventory_locks=[inventory],
            verification_started_at=datetime(2026, 8, 15, 0, 59, tzinfo=UTC),
            verification_completed_at=datetime(2026, 8, 15, 1, 1, tzinfo=UTC),
            verifier_code_revision="abcdef1",
            workers=1,
            duration_seconds=120.0,
        )


def test_parallel_deep_verifier_checks_every_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = load_sec_log_inventory_lock(
        SCALE_ROOT / "inventory" / "edgar2012.inventory-lock.json"
    )
    download = load_sec_log_download_receipt(
        SCALE_ROOT / "downloads" / "log20120101.download-receipt.json"
    )
    receipt = load_sec_log_partition_receipt(SCALE_ROOT / "partitions" / "log20120101.receipt.json")
    manifest = build_sec_log_scale_manifest(
        inventory_locks=[inventory],
        partition_receipts=[receipt],
        target_physical_row_count=1_000_000_000,
        code_revision="427026d",
        generated_at=datetime(2026, 8, 15, 1, 0, tzinfo=UTC),
    )
    checked: list[str] = []

    def fake_verify(*args: object, **kwargs: object) -> None:
        checked.append(receipt.partition_date.isoformat())

    monkeypatch.setattr(manifest_module, "verify_sec_log_partition_receipt", fake_verify)
    verify_sec_log_scale_manifest(
        manifest,
        inventory_locks=[inventory],
        partition_receipts=[receipt],
        download_receipts=[download],
        archive_directory=tmp_path,
        parquet_directory=tmp_path,
        deep=True,
        workers=2,
    )

    assert checked == ["2012-01-01"]
    with pytest.raises(ValueError, match="workers"):
        verify_sec_log_scale_manifest(
            manifest,
            inventory_locks=[inventory],
            partition_receipts=[receipt],
            download_receipts=[download],
            archive_directory=tmp_path,
            parquet_directory=tmp_path,
            deep=False,
            workers=2,
        )
