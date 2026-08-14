#!/usr/bin/env python3
"""Run one hash-verified, knowledge-cutoff-aware SEC log lake aggregate."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time
from pathlib import Path

from finreplay.scale import (
    load_sec_log_scale_manifest,
    run_sec_log_asof_query,
    write_sec_log_asof_query_receipt,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query exact SEC log rows with separate event and observed-knowledge cutoffs."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("verification/scale/sec-edgar/latest-scale-manifest.json"),
    )
    parser.add_argument(
        "--parquet-directory",
        type=Path,
        default=Path("data/silver/scale/sec-edgar-logs"),
    )
    parser.add_argument("--event-cutoff-date", type=date.fromisoformat, required=True)
    parser.add_argument("--event-cutoff-time", type=time.fromisoformat, required=True)
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        required=True,
        help="Timezone-aware time by which this project had actually observed eligible archives.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument(
        "--same-process-cache",
        action="store_true",
        help="Label the run as same-process; OS cache remains uncontrolled in either mode.",
    )
    parser.add_argument(
        "--skip-input-hashes",
        action="store_true",
        help="Skip Parquet SHA-256 checks; the receipt records that this was not verified.",
    )
    args = parser.parse_args()
    cutoff_second = (
        args.event_cutoff_time.hour * 3_600
        + args.event_cutoff_time.minute * 60
        + args.event_cutoff_time.second
    )
    manifest = load_sec_log_scale_manifest(args.manifest)
    receipt = run_sec_log_asof_query(
        manifest,
        parquet_directory=args.parquet_directory,
        event_cutoff_date=args.event_cutoff_date,
        event_cutoff_second=cutoff_second,
        knowledge_cutoff=args.knowledge_cutoff,
        executed_at=datetime.now(UTC),
        cache_state=(
            "same_process_os_cache_not_controlled"
            if args.same_process_cache
            else "fresh_process_os_cache_not_controlled"
        ),
        verify_input_hashes=not args.skip_input_hashes,
        threads=args.threads,
    )
    write_sec_log_asof_query_receipt(receipt, args.output)
    print(
        f"eligible_partitions={receipt.eligible_partition_count} "
        f"input_rows={receipt.input_scan_rows} "
        f"cutoff_rows={receipt.rows_at_or_before_cutoff} "
        f"query_seconds={receipt.query_elapsed_seconds:.6f} "
        f"input_hash_seconds={receipt.input_hash_verification_seconds:.6f} "
        f"receipt_sha256={receipt.receipt_sha256}"
    )


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("invalid ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("knowledge cutoff must include a timezone")
    return parsed


if __name__ == "__main__":
    main()
