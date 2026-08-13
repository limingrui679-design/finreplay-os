#!/usr/bin/env python3
"""Extract the minimal historical-safe SEC fact lock for the SVB replay."""

from __future__ import annotations

import argparse
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios.svb import (
    REQUIRED_CONCEPTS,
    SEC_FRAME,
    SEC_SOURCE_ID,
    SVB_BALANCE_DATE,
    SVB_DECISION_TIME,
    SVBInputLock,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/timevault.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/svb-2023/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            SVB_DECISION_TIME,
            valid_at=SVB_BALANCE_DATE,
            source_ids=[SEC_SOURCE_ID],
        )
    records = tuple(
        record
        for record in candidates
        if record.payload.get("frame") == SEC_FRAME
        and record.payload.get("concept") in REQUIRED_CONCEPTS
    )
    lock = SVBInputLock.create(records)
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("SVB input-lock destination contains different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        f"records={len(lock.records)} accession={lock.selected_accession} "
        f"lock_sha256={lock.lock_sha256} output={args.output}"
    )


if __name__ == "__main__":
    main()
