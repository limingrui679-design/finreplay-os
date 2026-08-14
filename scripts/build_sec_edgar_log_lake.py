#!/usr/bin/env python3
"""Resume the exact SEC EDGAR physical-row lake until a declared row target is met."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, date, datetime
from pathlib import Path

from finreplay.scale import (
    SECLogDownloadReceipt,
    SECLogInventoryLock,
    SECLogPartition,
    SECLogPartitionReceipt,
    SECLogScaleManifest,
    build_sec_log_scale_manifest,
    download_sec_log_archive,
    extract_sec_log_archive,
    load_sec_log_download_receipt,
    load_sec_log_inventory_lock,
    load_sec_log_partition_receipt,
    materialize_sec_log_csv,
    verify_sec_log_partition_receipt,
    write_sec_log_download_receipt,
    write_sec_log_partition_receipt,
    write_sec_log_scale_manifest,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=("Download, materialize, verify, and exactly sum official SEC EDGAR log rows.")
    )
    parser.add_argument(
        "--inventory-lock",
        action="append",
        type=Path,
        required=True,
        help="Repeat for each self-hashed official annual inventory lock.",
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
        "--manifest",
        type=Path,
        default=Path("verification/scale/sec-edgar/latest-scale-manifest.json"),
    )
    parser.add_argument(
        "--failure-log",
        type=Path,
        default=Path("verification/runs/sec-edgar-scale-failures.jsonl"),
    )
    parser.add_argument("--target-rows", type=int, default=1_000_000_000)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument(
        "--max-new-partitions",
        type=int,
        help="Bound newly built partitions for a smoke or incremental run.",
    )
    parser.add_argument("--download-attempts", type=int, default=5)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent official archive transfers; deliberately capped at four.",
    )
    parser.add_argument("--retry-delay-seconds", type=float, default=5.0)
    parser.add_argument("--inter-request-delay-seconds", type=float, default=0.25)
    parser.add_argument(
        "--user-agent-env",
        default="FINREPLAY_SEC_USER_AGENT",
        help="Environment variable containing the accountable SEC User-Agent.",
    )
    parser.add_argument(
        "--fast-existing",
        action="store_true",
        help="Skip deep byte re-verification only for already sealed partitions.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first unusable source partition instead of recording and continuing.",
    )
    args = parser.parse_args()
    _validate_args(args)
    user_agent = os.environ.get(args.user_agent_env, "").strip()
    if not user_agent:
        raise SystemExit(
            f"set {args.user_agent_env} to an accountable SEC User-Agent; it is never published"
        )
    inventories = tuple(load_sec_log_inventory_lock(path) for path in args.inventory_lock)
    partitions = _selected_partitions(
        inventories,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if not partitions:
        raise SystemExit("no SEC log partitions remain after date filtering")
    revision = _git_revision()
    directories = (
        args.archive_directory,
        args.parquet_directory,
        args.download_receipt_directory,
        args.partition_receipt_directory,
        args.manifest.parent,
        args.failure_log.parent,
        Path("data/tmp"),
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    receipt_by_date = _load_existing_partition_receipts(
        args.partition_receipt_directory,
        allowed_dates={item.partition_date for item in partitions},
    )
    total_rows = sum(item.data_row_count for item in receipt_by_date.values())
    print(
        f"sealed_partitions={len(receipt_by_date)} exact_rows={total_rows} "
        f"target={args.target_rows}",
        flush=True,
    )
    candidates: list[SECLogPartition] = []
    for partition in partitions:
        existing = receipt_by_date.get(partition.partition_date)
        if existing is not None:
            if not args.fast_existing:
                download = _load_download_for_partition(
                    partition,
                    args.download_receipt_directory,
                )
                verify_sec_log_partition_receipt(
                    existing,
                    download_receipt=download,
                    archive_path=args.archive_directory / existing.zip_filename,
                    parquet_path=args.parquet_directory / existing.parquet_filename,
                )
        else:
            candidates.append(partition)
    if args.max_new_partitions is not None:
        candidates = candidates[: args.max_new_partitions]
    next_candidate = 0
    pending: dict[Future[SECLogPartitionReceipt], SECLogPartition] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        while (
            len(pending) < args.workers
            and next_candidate < len(candidates)
            and total_rows < args.target_rows
        ):
            partition = candidates[next_candidate]
            next_candidate += 1
            future = executor.submit(
                _build_partition,
                partition,
                user_agent=user_agent,
                archive_directory=args.archive_directory,
                parquet_directory=args.parquet_directory,
                download_receipt_directory=args.download_receipt_directory,
                partition_receipt_directory=args.partition_receipt_directory,
                download_attempts=args.download_attempts,
                retry_delay_seconds=args.retry_delay_seconds,
            )
            pending[future] = partition
            time.sleep(args.inter_request_delay_seconds)
        while pending:
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                partition = pending.pop(future)
                try:
                    receipt = future.result()
                except (OSError, ValueError) as error:
                    _record_failure(
                        args.failure_log,
                        partition=partition,
                        revision=revision,
                        error=error,
                    )
                    print(
                        f"partition={partition.partition_date} status=failed "
                        f"error={type(error).__name__}: {error}",
                        flush=True,
                    )
                    if args.fail_fast:
                        raise
                else:
                    receipt_by_date[partition.partition_date] = receipt
                    total_rows += receipt.data_row_count
                    manifest = _write_checkpoint(
                        inventories=inventories,
                        receipts=tuple(receipt_by_date.values()),
                        target_rows=args.target_rows,
                        revision=revision,
                        path=args.manifest,
                    )
                    print(
                        f"partition={partition.partition_date} status=sealed "
                        f"rows={receipt.data_row_count} "
                        f"exact_total={manifest.total_distinct_physical_rows} "
                        f"target_met={str(manifest.target_met).lower()} "
                        f"manifest_sha256={manifest.manifest_sha256}",
                        flush=True,
                    )
            while (
                len(pending) < args.workers
                and next_candidate < len(candidates)
                and total_rows < args.target_rows
            ):
                partition = candidates[next_candidate]
                next_candidate += 1
                future = executor.submit(
                    _build_partition,
                    partition,
                    user_agent=user_agent,
                    archive_directory=args.archive_directory,
                    parquet_directory=args.parquet_directory,
                    download_receipt_directory=args.download_receipt_directory,
                    partition_receipt_directory=args.partition_receipt_directory,
                    download_attempts=args.download_attempts,
                    retry_delay_seconds=args.retry_delay_seconds,
                )
                pending[future] = partition
                time.sleep(args.inter_request_delay_seconds)
    manifest = _write_checkpoint(
        inventories=inventories,
        receipts=tuple(receipt_by_date.values()),
        target_rows=args.target_rows,
        revision=revision,
        path=args.manifest,
    )
    print(
        f"complete partitions={manifest.partition_count} "
        f"exact_rows={manifest.total_distinct_physical_rows} "
        f"target_met={str(manifest.target_met).lower()} "
        f"manifest_sha256={manifest.manifest_sha256}",
        flush=True,
    )


def _validate_args(args: argparse.Namespace) -> None:
    if args.target_rows <= 0:
        raise SystemExit("--target-rows must be positive")
    if args.max_new_partitions is not None and args.max_new_partitions <= 0:
        raise SystemExit("--max-new-partitions must be positive")
    if args.download_attempts <= 0:
        raise SystemExit("--download-attempts must be positive")
    if args.workers <= 0 or args.workers > 4:
        raise SystemExit("--workers must be between 1 and 4")
    if args.retry_delay_seconds < 0 or args.inter_request_delay_seconds < 0:
        raise SystemExit("retry and inter-request delays cannot be negative")
    if args.start_date and args.end_date and args.start_date > args.end_date:
        raise SystemExit("--start-date cannot follow --end-date")


def _selected_partitions(
    inventories: tuple[SECLogInventoryLock, ...],
    *,
    start_date: date | None,
    end_date: date | None,
) -> tuple[SECLogPartition, ...]:
    by_date: dict[date, SECLogPartition] = {}
    for inventory in inventories:
        for partition in inventory.partitions:
            if start_date is not None and partition.partition_date < start_date:
                continue
            if end_date is not None and partition.partition_date > end_date:
                continue
            prior = by_date.get(partition.partition_date)
            if prior is not None and prior != partition:
                raise ValueError("SEC log inventory locks disagree on one partition date")
            by_date[partition.partition_date] = partition
    return tuple(by_date[item] for item in sorted(by_date))


def _load_existing_partition_receipts(
    directory: Path,
    *,
    allowed_dates: set[date],
) -> dict[date, SECLogPartitionReceipt]:
    receipts: dict[date, SECLogPartitionReceipt] = {}
    for path in sorted(directory.glob("log*.receipt.json")):
        receipt = load_sec_log_partition_receipt(path)
        if receipt.partition_date not in allowed_dates:
            continue
        if receipt.partition_date in receipts:
            raise ValueError("duplicate SEC log partition receipt date")
        receipts[receipt.partition_date] = receipt
    return receipts


def _load_download_for_partition(
    partition: SECLogPartition, directory: Path
) -> SECLogDownloadReceipt:
    path = directory / f"log{partition.partition_date:%Y%m%d}.download-receipt.json"
    receipt = load_sec_log_download_receipt(path)
    if receipt.partition_date != partition.partition_date:
        raise ValueError("SEC log download receipt date mismatch")
    if receipt.listed_url != partition.listed_url or receipt.source_url != partition.source_url:
        raise ValueError("SEC log download receipt URL mismatch")
    return receipt


def _build_partition(
    partition: SECLogPartition,
    *,
    user_agent: str,
    archive_directory: Path,
    parquet_directory: Path,
    download_receipt_directory: Path,
    partition_receipt_directory: Path,
    download_attempts: int,
    retry_delay_seconds: float,
) -> SECLogPartitionReceipt:
    compact = partition.partition_date.strftime("%Y%m%d")
    archive = archive_directory / f"log{compact}.zip"
    download_path = download_receipt_directory / f"log{compact}.download-receipt.json"
    if download_path.is_file() and archive.is_file():
        download = _load_download_for_partition(partition, download_receipt_directory)
    else:
        download = _download_with_retries(
            partition,
            destination=archive,
            user_agent=user_agent,
            attempts=download_attempts,
            retry_delay_seconds=retry_delay_seconds,
        )
        write_sec_log_download_receipt(download, download_path)
    parquet = parquet_directory / f"log{compact}.parquet"
    with tempfile.TemporaryDirectory(prefix=f"sec-log-{compact}-", dir="data/tmp") as work:
        extracted = extract_sec_log_archive(
            archive,
            partition_date=partition.partition_date,
            destination=Path(work) / f"log{compact}.csv",
        )
        receipt = materialize_sec_log_csv(
            extracted,
            partition=partition,
            download_receipt=download,
            parquet_path=parquet,
        )
    verify_sec_log_partition_receipt(
        receipt,
        download_receipt=download,
        archive_path=archive,
        parquet_path=parquet,
    )
    receipt_path = partition_receipt_directory / f"log{compact}.receipt.json"
    write_sec_log_partition_receipt(receipt, receipt_path)
    return receipt


def _download_with_retries(
    partition: SECLogPartition,
    *,
    destination: Path,
    user_agent: str,
    attempts: int,
    retry_delay_seconds: float,
) -> SECLogDownloadReceipt:
    last_error: ValueError | None = None
    for attempt in range(attempts):
        try:
            return download_sec_log_archive(
                partition,
                destination=destination,
                user_agent=user_agent,
            )
        except ValueError as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(retry_delay_seconds * (attempt + 1))
    if last_error is None:
        raise RuntimeError("SEC log download retry loop did not execute")
    raise last_error


def _write_checkpoint(
    *,
    inventories: tuple[SECLogInventoryLock, ...],
    receipts: tuple[SECLogPartitionReceipt, ...],
    target_rows: int,
    revision: str,
    path: Path,
) -> SECLogScaleManifest:
    manifest = build_sec_log_scale_manifest(
        inventory_locks=inventories,
        partition_receipts=receipts,
        target_physical_row_count=target_rows,
        code_revision=revision,
        generated_at=datetime.now(UTC),
    )
    write_sec_log_scale_manifest(manifest, path)
    return manifest


def _record_failure(
    path: Path,
    *,
    partition: SECLogPartition,
    revision: str,
    error: Exception,
) -> None:
    payload = {
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "partition_date": partition.partition_date.isoformat(),
        "listed_url": str(partition.listed_url),
        "source_url": str(partition.source_url),
        "code_revision": revision,
        "error_type": type(error).__name__,
        "error": str(error),
    }
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    main()
