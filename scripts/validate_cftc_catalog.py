#!/usr/bin/env python3
"""Live-validate the four official CFTC COT product adapters."""

from __future__ import annotations

import argparse
from pathlib import Path

from finreplay.adapters import CFTC_COT_SPECS, CFTCCOTAdapter
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
        default=Path("verification/live/cftc"),
    )
    parser.add_argument("--sample-records", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.sample_records <= 1_000:
        raise SystemExit("--sample-records must be between 1 and 1,000")
    args.database.parent.mkdir(parents=True, exist_ok=True)
    store = ContentAddressedStore(args.raw_store)
    summaries: list[str] = []
    with SafeHttpClient(user_agent="FinReplayOS/0.1 public-source research connector") as http:
        for spec in CFTC_COT_SPECS:
            batch = CFTCCOTAdapter(http, spec).fetch_page(limit=args.sample_records)
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
