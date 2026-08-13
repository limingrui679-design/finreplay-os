#!/usr/bin/env python3
"""Live-validate configured New York Fed Markets API product adapters."""

from __future__ import annotations

import argparse
from pathlib import Path

from finreplay.adapters import NYFED_DATASET_SPECS, NYFedMarketsAdapter
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
        default=Path("verification/live/nyfed"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    store = ContentAddressedStore(args.raw_store)
    summaries: list[str] = []
    with SafeHttpClient(
        user_agent="FinReplayOS/0.1 public-source research connector"
    ) as http:
        for spec in NYFED_DATASET_SPECS:
            batch = NYFedMarketsAdapter(http, spec).fetch()
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
                f"{spec.adapter_id}: records={len(batch.records)} "
                f"inserted={append.inserted_records} receipt={receipt.name}"
            )
    print("\n".join(summaries))


if __name__ == "__main__":
    main()
