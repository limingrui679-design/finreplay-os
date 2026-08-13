#!/usr/bin/env python3
"""Live-validate three archived Treasury Daily Treasury Statement reports."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from finreplay.adapters import AdapterBatch, TreasuryDTSPublishedReportAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification

DEFAULT_REPORT_DATES = (
    date(2023, 5, 31),
    date(2023, 6, 1),
    date(2023, 6, 2),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report-date",
        type=date.fromisoformat,
        action="append",
        default=None,
        help="YYYY-MM-DD; repeat to override the three scenario dates.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/silver/supporting/treasury-dts.duckdb"),
    )
    parser.add_argument(
        "--raw-store",
        type=Path,
        default=Path("data/raw/supporting/treasury-dts"),
    )
    parser.add_argument(
        "--receipt-directory",
        type=Path,
        default=Path("verification/supporting/treasury-dts/live"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report_dates = tuple(args.report_date or DEFAULT_REPORT_DATES)
    if report_dates != tuple(sorted(set(report_dates))):
        raise SystemExit("--report-date values must be unique and chronological")
    batches = []
    for report_date in report_dates:
        with SafeHttpClient(
            user_agent="python-httpx/0.28.1 FinReplayOS/0.1",
            timeout_seconds=60.0,
            trust_environment=False,
        ) as http:
            batches.append(
                TreasuryDTSPublishedReportAdapter(http, report_date=report_date).fetch()
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
        f"{batch.receipts[0].adapter_id}: reports={len(report_dates)} "
        f"records={len(batch.records)} inserted={append.inserted_records} "
        f"receipt={receipt.name}"
    )


if __name__ == "__main__":
    main()
