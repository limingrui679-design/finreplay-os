#!/usr/bin/env python3
"""Live-validate SEC adapters for a configured reporting entity."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from finreplay.adapters import (
    AdapterBatch,
    SECCompanyFactsAdapter,
    SECHistoricalSubmissionsAdapter,
    SECSubmissionsAdapter,
)
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cik", type=int, default=719_739)
    parser.add_argument("--database", type=Path, default=Path("data/silver/timevault.duckdb"))
    parser.add_argument("--raw-store", type=Path, default=Path("data/raw/artifacts"))
    parser.add_argument(
        "--receipt-directory",
        type=Path,
        default=Path("verification/live/sec"),
    )
    return parser.parse_args()


def persist_batch(
    *,
    batch: AdapterBatch,
    database: Path,
    raw_store: Path,
    receipt_directory: Path,
) -> tuple[Path, int, int]:
    store = ContentAddressedStore(raw_store)
    stored = tuple(store.put(artifact) for artifact in batch.artifacts)
    database.parent.mkdir(parents=True, exist_ok=True)
    with TimeVault(database) as vault:
        append = vault.append(batch.records)
        manifest = vault.manifest()
    receipt = write_live_verification(
        output_directory=receipt_directory,
        batch=batch,
        stored_artifacts=stored,
        append_receipt=append,
        vault_manifest=manifest,
    )
    return receipt, append.inserted_records, len(batch.records)


def main() -> None:
    args = parse_args()
    user_agent = os.environ.get("FINREPLAY_SEC_USER_AGENT", "").strip()
    if "@" not in user_agent or len(user_agent) < 12:
        raise SystemExit(
            "FINREPLAY_SEC_USER_AGENT must identify the project and a real contact email; "
            "it is used in HTTP headers but never written to receipts."
        )
    with SafeHttpClient(user_agent=user_agent) as http:
        recent, historical_names = SECSubmissionsAdapter(http).fetch(args.cik)
        recent_receipt, recent_inserted, recent_count = persist_batch(
            batch=recent,
            database=args.database,
            raw_store=args.raw_store,
            receipt_directory=args.receipt_directory,
        )
        acceptance_times = {
            str(record.payload["accessionNumber"]): record.interval.available_at
            for record in recent.records
        }
        history_result: tuple[Path, int, int] | None = None
        if historical_names:
            historical = SECHistoricalSubmissionsAdapter(http).fetch(
                cik=args.cik,
                file_name=historical_names[0],
            )
            history_result = persist_batch(
                batch=historical,
                database=args.database,
                raw_store=args.raw_store,
                receipt_directory=args.receipt_directory,
            )
            acceptance_times.update(
                {
                    str(record.payload["accessionNumber"]): record.interval.available_at
                    for record in historical.records
                }
            )
        companyfacts = SECCompanyFactsAdapter(http).fetch(
            args.cik,
            acceptance_times=acceptance_times,
        )
        facts_receipt, facts_inserted, facts_count = persist_batch(
            batch=companyfacts,
            database=args.database,
            raw_store=args.raw_store,
            receipt_directory=args.receipt_directory,
        )
    print(
        f"sec.edgar.submissions: records={recent_count} inserted={recent_inserted} "
        f"receipt={recent_receipt.name}"
    )
    if history_result is not None:
        history_receipt, history_inserted, history_count = history_result
        print(
            f"sec.edgar.submissions_historical: records={history_count} "
            f"inserted={history_inserted} receipt={history_receipt.name}"
        )
    else:
        print("sec.edgar.submissions_historical: not declared by this entity index; skipped")
    print(
        f"sec.xbrl.companyfacts: records={facts_count} inserted={facts_inserted} "
        f"receipt={facts_receipt.name}"
    )


if __name__ == "__main__":
    main()
