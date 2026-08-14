#!/usr/bin/env python3
"""Freshly verify the exact SEC log scale sum and its complete receipt chain."""

from __future__ import annotations

import argparse
from pathlib import Path

from finreplay.scale import (
    load_sec_log_download_receipt,
    load_sec_log_inventory_lock,
    load_sec_log_partition_receipt,
    load_sec_log_scale_manifest,
    verify_sec_log_scale_manifest,
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
    args = parser.parse_args()
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
    verify_sec_log_scale_manifest(
        manifest,
        inventory_locks=inventories,
        partition_receipts=partition_receipts,
        download_receipts=download_receipts,
        archive_directory=args.archive_directory,
        parquet_directory=args.parquet_directory,
        deep=args.deep,
    )
    print(
        f"verified=true deep={str(args.deep).lower()} "
        f"partitions={manifest.partition_count} "
        f"exact_rows={manifest.total_distinct_physical_rows} "
        f"target={manifest.target_physical_row_count} "
        f"target_met={str(manifest.target_met).lower()} "
        f"manifest_sha256={manifest.manifest_sha256}"
    )


if __name__ == "__main__":
    main()
