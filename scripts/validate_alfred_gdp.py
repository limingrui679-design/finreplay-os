#!/usr/bin/env python3
"""Live-validate the four ALFRED GDP vintages used by the revision scenario."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from finreplay.adapters import AdapterBatch, ALFREDGDPVintageAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification

DEFAULT_VINTAGES = (
    date(2022, 10, 27),
    date(2022, 11, 30),
    date(2023, 1, 26),
    date(2023, 2, 23),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--vintage-date",
        type=date.fromisoformat,
        action="append",
        default=None,
        help="ISO date; repeat to override the four scenario vintages.",
    )
    parser.add_argument(
        "--observation-start",
        type=date.fromisoformat,
        default=date(2022, 7, 1),
    )
    parser.add_argument(
        "--observation-end",
        type=date.fromisoformat,
        default=date(2022, 10, 1),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/silver/supporting/alfred.duckdb"),
    )
    parser.add_argument(
        "--raw-store",
        type=Path,
        default=Path("data/raw/supporting/alfred"),
    )
    parser.add_argument(
        "--receipt-directory",
        type=Path,
        default=Path("verification/supporting/alfred/live"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    vintages = tuple(args.vintage_date or DEFAULT_VINTAGES)
    if len(vintages) != len(set(vintages)):
        raise SystemExit("--vintage-date values must be unique")
    if tuple(sorted(vintages)) != vintages:
        raise SystemExit("--vintage-date values must be ascending")
    args.database.parent.mkdir(parents=True, exist_ok=True)
    batches = []
    for vintage in vintages:
        # The ALFRED download host has intermittently stalled reused HTTP/1.1 connections in
        # live validation. A fresh bounded client per explicitly named vintage avoids silently
        # retrying or changing the requested snapshot.
        with SafeHttpClient(
            user_agent="python-httpx/0.28.1 FinReplayOS/0.1",
            timeout_seconds=60.0,
            trust_environment=False,
        ) as http:
            batches.append(
                ALFREDGDPVintageAdapter(
                    http,
                    vintage_date=vintage,
                    observation_start=args.observation_start,
                    observation_end=args.observation_end,
                ).fetch()
            )
    batch = AdapterBatch(
        records=tuple(record for item in batches for record in item.records),
        receipts=tuple(receipt for item in batches for receipt in item.receipts),
        artifacts=tuple(artifact for item in batches for artifact in item.artifacts),
    )
    store = ContentAddressedStore(args.raw_store)
    stored = tuple(store.put(artifact) for artifact in batch.artifacts)
    with TimeVault(args.database) as vault:
        append = vault.append(batch.records)
        manifest = vault.manifest()
    receipt = write_live_verification(
        output_directory=args.receipt_directory,
        batch=batch,
        stored_artifacts=stored,
        append_receipt=append,
        vault_manifest=manifest,
    )
    print(
        f"{batch.receipts[0].adapter_id}: vintages={len(vintages)} "
        f"records={len(batch.records)} inserted={append.inserted_records} "
        f"receipt={receipt.name}"
    )


if __name__ == "__main__":
    main()
