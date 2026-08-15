#!/usr/bin/env python3
"""Freshly verify the exact SEC log scale sum and its complete receipt chain."""

from __future__ import annotations

import argparse
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from finreplay.scale import (
    build_sec_log_scale_verification_receipt,
    load_sec_log_download_receipt,
    load_sec_log_inventory_lock,
    load_sec_log_partition_receipt,
    load_sec_log_scale_manifest,
    verify_sec_log_scale_manifest,
    verify_sec_log_scale_verification_receipt,
    write_sec_log_scale_verification_receipt,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild an SEC log scale manifest and optionally re-read every data byte."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("verification/scale/sec-edgar/latest-scale-manifest.json"),
    )
    parser.add_argument(
        "--inventory-lock",
        action="append",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--download-receipt-directory",
        type=Path,
        default=Path("verification/scale/sec-edgar/downloads"),
    )
    parser.add_argument(
        "--partition-receipt-directory",
        type=Path,
        default=Path("verification/scale/sec-edgar/partitions"),
    )
    parser.add_argument(
        "--archive-directory",
        type=Path,
        default=Path("data/raw/scale/sec-edgar-logs"),
    )
    parser.add_argument(
        "--parquet-directory",
        type=Path,
        default=Path("data/silver/scale/sec-edgar-logs"),
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Re-hash, re-extract, re-profile, and re-scan every source/output partition.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Independent deep-verification workers; bounded to 1-4.",
    )
    parser.add_argument(
        "--verification-receipt",
        type=Path,
        default=Path("verification/scale/sec-edgar/latest-deep-verification-receipt.json"),
        help="Atomic self-hashed receipt written after a successful deep run.",
    )
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 4:
        raise SystemExit("--workers must be between 1 and 4")
    if not args.deep and args.workers != 1:
        raise SystemExit("--workers greater than one requires --deep")
    manifest = load_sec_log_scale_manifest(args.manifest)
    inventories = [load_sec_log_inventory_lock(path) for path in args.inventory_lock]
    partition_receipts = []
    download_receipts = []
    for summary in manifest.partitions:
        compact = summary.partition_date.strftime("%Y%m%d")
        partition_receipts.append(
            load_sec_log_partition_receipt(
                args.partition_receipt_directory / f"log{compact}.receipt.json"
            )
        )
        download_receipts.append(
            load_sec_log_download_receipt(
                args.download_receipt_directory / f"log{compact}.download-receipt.json"
            )
        )
    verifier_code_revision = _git_revision()
    verification_started_at = datetime.now(UTC)
    monotonic_started_at = time.perf_counter()
    verify_sec_log_scale_manifest(
        manifest,
        inventory_locks=inventories,
        partition_receipts=partition_receipts,
        download_receipts=download_receipts,
        archive_directory=args.archive_directory,
        parquet_directory=args.parquet_directory,
        deep=args.deep,
        workers=args.workers,
    )
    verification_completed_at = datetime.now(UTC)
    duration_seconds = time.perf_counter() - monotonic_started_at
    receipt_hash = "none"
    if args.deep:
        receipt = build_sec_log_scale_verification_receipt(
            manifest=manifest,
            inventory_locks=inventories,
            verification_started_at=verification_started_at,
            verification_completed_at=verification_completed_at,
            verifier_code_revision=verifier_code_revision,
            workers=args.workers,
            duration_seconds=duration_seconds,
        )
        write_sec_log_scale_verification_receipt(receipt, args.verification_receipt)
        verify_sec_log_scale_verification_receipt(
            receipt,
            manifest=manifest,
            inventory_locks=inventories,
        )
        receipt_hash = receipt.verification_receipt_sha256
    print(
        f"verified=true deep={str(args.deep).lower()} "
        f"workers={args.workers} "
        f"partitions={manifest.partition_count} "
        f"exact_rows={manifest.total_distinct_physical_rows} "
        f"target={manifest.target_physical_row_count} "
        f"target_met={str(manifest.target_met).lower()} "
        f"manifest_sha256={manifest.manifest_sha256} "
        f"verification_receipt_sha256={receipt_hash}"
    )


def _git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    main()
