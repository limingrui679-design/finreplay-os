#!/usr/bin/env python3
"""Live-validate three paired TreasuryDirect 91-day bill auction results."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from finreplay.adapters import AdapterBatch, TreasuryAuction91DayArchiveAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification

DEFAULT_AUCTION_DATES = (
    date(2020, 3, 9),
    date(2020, 3, 16),
    date(2020, 3, 23),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--auction-date",
        type=date.fromisoformat,
        action="append",
        default=None,
        help="YYYY-MM-DD; repeat to override the three scenario auctions.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/silver/supporting/treasury-auction-results.duckdb"),
    )
    parser.add_argument(
        "--raw-store",
        type=Path,
        default=Path("data/raw/supporting/treasury-auction-results"),
    )
    parser.add_argument(
        "--receipt-directory",
        type=Path,
        default=Path("verification/supporting/treasury-auction-results/live"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    auction_dates = tuple(args.auction_date or DEFAULT_AUCTION_DATES)
    if auction_dates != tuple(sorted(set(auction_dates))):
        raise SystemExit("--auction-date values must be unique and chronological")
    batches = []
    for auction_date in auction_dates:
        with SafeHttpClient(
            user_agent="python-httpx/0.28.1 FinReplayOS/0.1",
            timeout_seconds=60.0,
        ) as http:
            batches.append(
                TreasuryAuction91DayArchiveAdapter(
                    http,
                    auction_date=auction_date,
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
        f"{batch.receipts[0].adapter_id}: auctions={len(auction_dates)} "
        f"responses={len(batch.receipts)} records={len(batch.records)} "
        f"inserted={append.inserted_records} receipt={receipt.name}"
    )


if __name__ == "__main__":
    main()
