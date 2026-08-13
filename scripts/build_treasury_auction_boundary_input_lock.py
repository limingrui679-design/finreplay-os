#!/usr/bin/env python3
"""Extract and seal two pre-decision Treasury 91-day bill auction facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import (
    TREASURY_AUCTION_SOURCE_ID,
    TreasuryAuctionBoundaryInputLock,
)

ROLE_AUCTIONS = {
    "march09_high_rate": "2020-03-09",
    "march16_high_rate": "2020-03-16",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="treasury-auction-2020-zero-rate-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="treasury-auction-2020-zero-rate-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="treasury-auction")
    parser.add_argument(
        "--title",
        default=(
            "Treasury 91-day bill auction-rate boundary before the March 2020 zero result"
        ),
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-18T12:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T07:50:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/treasury-auction-results.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/treasury-auction-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[TREASURY_AUCTION_SOURCE_ID],
        )
    by_auction = {
        str(record.payload.get("auction_date")): record
        for record in candidates
        if record.entity_id == "us_treasury_auction:91_day_bill"
        and record.payload.get("metric") == "high_discount_rate"
    }
    missing = sorted(
        auction_date
        for auction_date in ROLE_AUCTIONS.values()
        if auction_date not in by_auction
    )
    if missing:
        raise SystemExit(f"required Treasury auction facts are absent: {missing}")
    roles = {
        role: by_auction[auction_date].record_id
        for role, auction_date in ROLE_AUCTIONS.items()
    }
    records = tuple(
        sorted(
            (by_auction[auction_date] for auction_date in ROLE_AUCTIONS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = TreasuryAuctionBoundaryInputLock.create(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "replay_id": args.replay_id,
            "artifact_prefix": args.artifact_prefix,
            "title": args.title,
            "decision_time": args.decision_time,
            "build_epoch": args.build_epoch,
            "roles": roles,
            "source_response_sha256s": sorted(
                {record.source.sha256 for record in records}
            ),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves the reported high discount rates for the March 9 and 16, "
                "2020 Treasury 91-day bill auctions from paired official XML/PDF results "
                "knowable before the replay decision time. It excludes the March 23 zero-rate "
                "event, individual bids, a probability distribution, forecast, calibrated "
                "interval, auction-demand or policy causality, recommendation, execution, "
                "investment performance, external review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("Treasury auction input-lock destination contains different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        f"records={len(lock.records)} source_hashes={len(lock.source_response_sha256s)} "
        f"lock_sha256={lock.lock_sha256} output={args.output}"
    )


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


if __name__ == "__main__":
    main()
