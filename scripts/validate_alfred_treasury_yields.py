#!/usr/bin/env python3
"""Live-validate six native-vintage ALFRED Treasury-yield observations."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from finreplay.adapters import AdapterBatch, ALFREDTreasuryYieldVintageAdapter
from finreplay.adapters.base import SafeHttpClient
from finreplay.engines import TimeVault
from finreplay.storage import ContentAddressedStore, write_live_verification


@dataclass(frozen=True)
class YieldPoint:
    series_id: str
    vintage_date: date
    observation_date: date


DEFAULT_POINTS = (
    YieldPoint("DGS10", date(2023, 3, 9), date(2023, 3, 8)),
    YieldPoint("DGS2", date(2023, 3, 9), date(2023, 3, 8)),
    YieldPoint("DGS10", date(2023, 3, 14), date(2023, 3, 13)),
    YieldPoint("DGS2", date(2023, 3, 14), date(2023, 3, 13)),
    YieldPoint("DGS10", date(2023, 3, 16), date(2023, 3, 15)),
    YieldPoint("DGS2", date(2023, 3, 16), date(2023, 3, 15)),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--point",
        type=_yield_point,
        action="append",
        default=None,
        help="SERIES_ID,VINTAGE_DATE,OBSERVATION_DATE; repeat to override defaults.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/silver/supporting/alfred-treasury-yields.duckdb"),
    )
    parser.add_argument(
        "--raw-store",
        type=Path,
        default=Path("data/raw/supporting/alfred-treasury-yields"),
    )
    parser.add_argument(
        "--receipt-directory",
        type=Path,
        default=Path("verification/supporting/alfred-treasury-yields/live"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    points = tuple(args.point or DEFAULT_POINTS)
    if len(points) != len(set(points)):
        raise SystemExit("--point values must be unique")
    expected_order = tuple(
        sorted(points, key=lambda item: (item.observation_date, item.series_id))
    )
    if points != expected_order:
        raise SystemExit("--point values must be ordered by observation date and series ID")
    batches = []
    for point in points:
        with SafeHttpClient(
            user_agent="python-httpx/0.28.1 FinReplayOS/0.1",
            timeout_seconds=60.0,
            trust_environment=False,
        ) as http:
            batches.append(
                ALFREDTreasuryYieldVintageAdapter(
                    http,
                    series_id=point.series_id,
                    vintage_date=point.vintage_date,
                    observation_date=point.observation_date,
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
        f"{batch.receipts[0].adapter_id}: points={len(points)} "
        f"records={len(batch.records)} inserted={append.inserted_records} "
        f"receipt={receipt.name}"
    )


def _yield_point(value: str) -> YieldPoint:
    parts = value.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "point must use SERIES_ID,VINTAGE_DATE,OBSERVATION_DATE"
        )
    series_id, raw_vintage, raw_observation = parts
    try:
        return YieldPoint(
            series_id,
            date.fromisoformat(raw_vintage),
            date.fromisoformat(raw_observation),
        )
    except ValueError as error:
        raise argparse.ArgumentTypeError("point dates must use YYYY-MM-DD") from error


if __name__ == "__main__":
    main()
