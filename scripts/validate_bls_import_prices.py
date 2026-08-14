#!/usr/bin/env python3
"""Live-validate three paired archived BLS Import Price Index releases."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from finreplay.adapters import AdapterBatch, BLSImportPriceArchiveAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification

DEFAULT_RELEASES = (
    date(2020, 2, 14),
    date(2020, 3, 13),
    date(2020, 4, 14),
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
        default=Path("data/silver/supporting/bls-import-prices.duckdb"),
    )
    parser.add_argument(
        "--raw-store",
        type=Path,
        default=Path("data/raw/supporting/bls-import-prices"),
    )
    parser.add_argument(
        "--receipt-directory",
        type=Path,
        default=Path("verification/supporting/bls-import-prices/live"),
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
            user_agent="FinReplayOS/0.1 research-contact@example.invalid",
            timeout_seconds=90.0,
            trust_environment=False,
        ) as http:
            batches.append(
                BLSImportPriceArchiveAdapter(
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
        f"responses={len(batch.receipts)} records={len(batch.records)} "
        f"inserted={append.inserted_records} idempotent={append.idempotent_records} "
        f"receipt={receipt.name}"
    )


if __name__ == "__main__":
    main()
