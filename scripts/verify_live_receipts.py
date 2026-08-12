#!/usr/bin/env python3
"""Verify and summarize the newest live receipt for every official adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finreplay.verification import latest_live_receipts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("verification/live"))
    parser.add_argument("--raw-store", type=Path, default=Path("data/raw/artifacts"))
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    receipts = latest_live_receipts(args.directory, raw_store=args.raw_store)
    payload = {
        "schema_version": "1.0.0",
        "verified_adapter_count": len(receipts),
        "historical_replay_eligible_count": sum(
            receipt.historical_replay_eligible for receipt in receipts
        ),
        "latest_only_count": sum(
            receipt.temporal_coverage == "latest_only" for receipt in receipts
        ),
        "adapters": [
            {
                "adapter_id": receipt.adapter_id,
                "retrieved_at": receipt.retrieved_at,
                "record_count": receipt.record_count,
                "inserted_records": receipt.inserted_records,
                "idempotent_records": receipt.idempotent_records,
                "temporal_coverage": receipt.temporal_coverage,
                "historical_replay_eligible": receipt.historical_replay_eligible,
                "receipt": receipt.path.relative_to(args.directory.resolve()).as_posix(),
            }
            for receipt in receipts
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    print(serialized, end="")


if __name__ == "__main__":
    main()
