#!/usr/bin/env python3
"""Live-validate three archived H.4.1 BTFP releases used by one scenario."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from finreplay.adapters import AdapterBatch, FederalReserveH41BTFPAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification

DEFAULT_RELEASES = (
    date(2023, 3, 16),
    date(2023, 3, 23),
    date(2023, 3, 30),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-date",
        type=date.fromisoformat,
        action="append",
        default=None,
        help="ISO date; repeat to override the three scenario releases.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/silver/supporting/fed-h41.duckdb"),
    )
    parser.add_argument(
        "--raw-store",
        type=Path,
        default=Path("data/raw/supporting/fed-h41"),
    )
    parser.add_argument(
        "--receipt-directory",
        type=Path,
        default=Path("verification/supporting/fed-h41/live"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    releases = tuple(args.release_date or DEFAULT_RELEASES)
    if len(releases) != len(set(releases)):
        raise SystemExit("--release-date values must be unique")
    if tuple(sorted(releases)) != releases:
        raise SystemExit("--release-date values must be ascending")
    batches = []
    for release_date in releases:
        with SafeHttpClient(
            user_agent="python-httpx/0.28.1 FinReplayOS/0.1",
            timeout_seconds=60.0,
            trust_environment=False,
        ) as http:
            batches.append(
                FederalReserveH41BTFPAdapter(
                    http,
                    release_date=release_date,
                ).fetch()
            )
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
        f"{batch.receipts[0].adapter_id}: releases={len(releases)} "
        f"records={len(batch.records)} inserted={append.inserted_records} "
        f"receipt={receipt.name}"
    )


if __name__ == "__main__":
    main()
