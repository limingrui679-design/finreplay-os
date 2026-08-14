#!/usr/bin/env python3
"""Live-validate three Federal Reserve G.19 archived release snapshots."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from finreplay.adapters import AdapterBatch, FederalReserveG19ArchiveAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification

DEFAULT_RELEASE_DATES = (
    date(2020, 3, 6),
    date(2020, 4, 7),
    date(2020, 5, 7),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-date",
        type=date.fromisoformat,
        action="append",
        default=None,
        help="YYYY-MM-DD; repeat to override the three scenario releases.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/silver/supporting/fed-g19.duckdb"),
    )
    parser.add_argument(
        "--raw-store",
        type=Path,
        default=Path("data/raw/supporting/fed-g19"),
    )
    parser.add_argument(
        "--receipt-directory",
        type=Path,
        default=Path("verification/supporting/fed-g19/live"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    release_dates = tuple(args.release_date or DEFAULT_RELEASE_DATES)
    if release_dates != tuple(sorted(set(release_dates))):
        raise SystemExit("--release-date values must be unique and chronological")
    batches = []
    for release_date in release_dates:
        with SafeHttpClient(
            user_agent="python-httpx/0.28.1 FinReplayOS/0.1",
            timeout_seconds=60.0,
        ) as http:
            batches.append(FederalReserveG19ArchiveAdapter(http, release_date=release_date).fetch())
    batch = AdapterBatch(
        records=tuple(record for item in batches for record in item.records),
        receipts=tuple(receipt for item in batches for receipt in item.receipts),
        artifacts=tuple(artifact for item in batches for artifact in item.artifacts),
    )
    store = ContentAddressedStore(args.raw_store)
    stored = tuple(store.put(artifact) for artifact in batch.artifacts)
    args.database.parent.mkdir(parents=True, exist_ok=True)
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
        f"{batch.receipts[0].adapter_id}: releases={len(release_dates)} "
        f"responses={len(batch.receipts)} records={len(batch.records)} "
        f"inserted={append.inserted_records} idempotent={append.idempotent_records} "
        f"receipt={receipt.name}"
    )


if __name__ == "__main__":
    main()
