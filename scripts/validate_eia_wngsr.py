#!/usr/bin/env python3
"""Live-validate the three March 2020 EIA WNGSR working-gas records."""

from __future__ import annotations

import argparse
from pathlib import Path

from finreplay.adapters import EIAWNGSRWorkingGasHistoryAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/silver/supporting/eia-wngsr.duckdb"),
    )
    parser.add_argument(
        "--raw-store",
        type=Path,
        default=Path("data/raw/supporting/eia-wngsr"),
    )
    parser.add_argument(
        "--receipt-directory",
        type=Path,
        default=Path("verification/supporting/eia-wngsr/live"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with SafeHttpClient(
        user_agent="python-httpx/0.28.1 FinReplayOS/0.1",
        timeout_seconds=90.0,
    ) as http:
        batch = EIAWNGSRWorkingGasHistoryAdapter(http).fetch()
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
        f"{batch.receipts[0].adapter_id}: responses={len(batch.receipts)} "
        f"records={len(batch.records)} inserted={append.inserted_records} "
        f"idempotent={append.idempotent_records} receipt={receipt.name}"
    )


if __name__ == "__main__":
    main()
