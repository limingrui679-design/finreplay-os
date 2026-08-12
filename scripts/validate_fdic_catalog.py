#!/usr/bin/env python3
"""Live-validate every configured FDIC BankFind data-product adapter."""

from __future__ import annotations

import argparse
from pathlib import Path

from finreplay.adapters import FDIC_DATASET_SPECS, FDICDatasetAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/silver/timevault.duckdb"))
    parser.add_argument("--raw-store", type=Path, default=Path("data/raw/artifacts"))
    parser.add_argument(
        "--receipt-directory",
        type=Path,
        default=Path("verification/live/fdic"),
    )
    parser.add_argument("--sample-records", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.sample_records <= 100:
        raise SystemExit("--sample-records must be between 1 and 100")
    args.database.parent.mkdir(parents=True, exist_ok=True)
    store = ContentAddressedStore(args.raw_store)
    summaries: list[str] = []
    with SafeHttpClient(
        user_agent="FinReplayOS/0.1 research connector (https://github.com/)"
    ) as http:
        for spec in FDIC_DATASET_SPECS:
            batch, total = FDICDatasetAdapter(http, spec).fetch_page(
                limit=args.sample_records
            )
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
            summaries.append(
                f"{spec.adapter_id}: sample={len(batch.records)} total={total} "
                f"inserted={append.inserted_records} receipt={receipt.name}"
            )
    print("\n".join(summaries))


if __name__ == "__main__":
    main()

