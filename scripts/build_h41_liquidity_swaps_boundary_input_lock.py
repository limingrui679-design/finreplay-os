#!/usr/bin/env python3
"""Extract and seal the two H.4.1 liquidity-swap balances known at decision time."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import (
    H41_LIQUIDITY_SWAPS_INPUT_RESPONSE_SHA256S,
    H41_LIQUIDITY_SWAPS_SOURCE_ID,
    H41_LIQUIDITY_SWAPS_SUPPORTING_RECEIPT_SHA256,
    H41LiquiditySwapsBoundaryInputLock,
)

ROLE_WEEKS = {
    "march18_release": "2020-03-18",
    "march25_decision_release": "2020-03-25",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="fed-h41-2020-liquidity-swap-balance-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="fed-h41-2020-liquidity-swap-balance-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="h41-swaps")
    parser.add_argument(
        "--title",
        default="Federal Reserve H.4.1 liquidity-swap balance boundary before April 2020",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-26T20:30:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-14T10:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/fed-h41-liquidity-swaps.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/fed-h41-liquidity-swaps-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[H41_LIQUIDITY_SWAPS_SOURCE_ID],
        )
    by_week = {
        str(record.payload.get("week_ending")): record
        for record in candidates
        if record.entity_id == "federal_reserve_facility:central_bank_liquidity_swaps"
        and record.payload.get("metric") == "wednesday_outstanding"
    }
    missing = sorted(week for week in ROLE_WEEKS.values() if week not in by_week)
    if missing:
        raise SystemExit(f"required H.4.1 swap facts are absent: {missing}")
    roles = {role: by_week[week].record_id for role, week in ROLE_WEEKS.items()}
    records = tuple(
        sorted(
            (by_week[week] for week in ROLE_WEEKS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = H41LiquiditySwapsBoundaryInputLock.create(
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
            "source_response_sha256s": H41_LIQUIDITY_SWAPS_INPUT_RESPONSE_SHA256S,
            "supporting_receipt_sha256": (
                H41_LIQUIDITY_SWAPS_SUPPORTING_RECEIPT_SHA256
            ),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves the March 18 and March 25, 2020 Wednesday central-bank-"
                "liquidity-swap balances known at the March 26 official stated release time and "
                "binds four exact HTML/ASCII input-response hashes plus the idempotent six-"
                "response supporting receipt. It excludes the April 1 event, weekly averages "
                "as range inputs, current-market revaluation, institutions, transactions, "
                "counterparty loss, P&L, a probability distribution, forecast, calibrated "
                "interval, systemic-stress or policy causality, recommendation, execution, "
                "investment performance, external review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("H.4.1 swap input-lock destination has different bytes")
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
