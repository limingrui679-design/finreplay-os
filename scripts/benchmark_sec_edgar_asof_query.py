#!/usr/bin/env python3
"""Run two comparable SEC log queries in fresh processes without claiming cache control."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

from finreplay.scale import (
    build_sec_log_query_benchmark_receipt,
    load_sec_log_asof_query_receipt,
    write_sec_log_query_benchmark_receipt,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the same hash-verified SEC lake query in two fresh processes."
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
    parser.add_argument("--knowledge-cutoff", type=_aware_datetime, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--benchmark-receipt", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if args.threads <= 0:
        raise SystemExit("--threads must be positive")
    args.output_directory.mkdir(parents=True, exist_ok=True)
    runner = Path(__file__).with_name("run_sec_edgar_asof_query.py")
    outputs = (
        args.output_directory / "fresh-process-run-1.query-receipt.json",
        args.output_directory / "fresh-process-run-2.query-receipt.json",
    )
    for output in outputs:
        command = [
            sys.executable,
            str(runner),
            "--manifest",
            str(args.manifest),
            "--parquet-directory",
            str(args.parquet_directory),
            "--event-cutoff-date",
            args.event_cutoff_date.isoformat(),
            "--event-cutoff-time",
            args.event_cutoff_time.isoformat(),
            "--knowledge-cutoff",
            args.knowledge_cutoff.isoformat(),
            "--output",
            str(output),
            "--threads",
            str(args.threads),
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        if completed.stdout.strip():
            print(completed.stdout.strip())
    first, second = (load_sec_log_asof_query_receipt(path) for path in outputs)
    benchmark = build_sec_log_query_benchmark_receipt(
        first=first,
        second=second,
        code_revision=_git_revision(),
        generated_at=datetime.now(UTC),
    )
    write_sec_log_query_benchmark_receipt(benchmark, args.benchmark_receipt)
    print(
        f"benchmark_receipt_sha256={benchmark.benchmark_receipt_sha256} "
        f"input_rows={first.input_scan_rows} input_bytes={first.eligible_parquet_bytes} "
        f"run_1_seconds={first.query_elapsed_seconds:.6f} "
        f"run_2_seconds={second.query_elapsed_seconds:.6f} "
        f"run_1_peak_rss_bytes={first.process_peak_rss_bytes} "
        f"run_2_peak_rss_bytes={second.process_peak_rss_bytes} "
        "os_cache_controlled=false"
    )


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("invalid ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("knowledge cutoff must include a timezone")
    return parsed


def _git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    main()
