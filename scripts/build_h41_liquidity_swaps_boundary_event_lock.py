#!/usr/bin/env python3
"""Seal the April 1 H.4.1 liquidity-swap balance as a post-decision event."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import H41_LIQUIDITY_SWAPS_SOURCE_ID, seal_official_event_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="fed-h41-2020-liquidity-swap-balance-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-26T20:30:00Z"),
    )
    parser.add_argument(
        "--knowledge-cutoff",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-03T04:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/fed-h41-liquidity-swaps.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/fed-h41-liquidity-swaps-2020/event-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.knowledge_cutoff,
            source_ids=[H41_LIQUIDITY_SWAPS_SOURCE_ID],
        )
    selected = tuple(
        record
        for record in candidates
        if record.entity_id == "federal_reserve_facility:central_bank_liquidity_swaps"
        and record.payload.get("release_date") == "2020-04-02"
        and record.payload.get("week_ending") == "2020-04-01"
        and record.payload.get("metric") == "wednesday_outstanding"
    )
    if len(selected) != 1:
        raise SystemExit(f"expected one April 1 H.4.1 swap event, found {len(selected)}")
    lock = seal_official_event_lock(
        {
            "schema_version": "1.0.0",
            "scenario_id": args.scenario_id,
            "scenario_version": args.scenario_version,
            "decision_time": args.decision_time.isoformat(),
            "event_role": "post_decision_official_event",
            "records": [selected[0].model_dump(mode="json")],
            "claim_boundary": (
                "This lock preserves the April 1, 2020 H.4.1 Wednesday central-bank-liquidity-"
                "swap balance of $348,544 million solely as a post-decision event. The archived "
                "pair states only the April 2 release date, so eligibility is conservatively "
                "delayed to the following New York midnight. The balance lies $142,493 million "
                "above the fixed lower endpoint and $63,513 million below the fixed upper "
                "endpoint; that inside-range result is retained without being relabelled as "
                "forecast success. The event is excluded from all ReplayPack inputs and does "
                "not establish calibrated coverage, current-market exposure, transaction or "
                "institution behavior, policy effectiveness, systemic-stress causality, "
                "investment performance, external review, deployment, or user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("H.4.1 swap event-lock destination has different bytes")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(f"records={len(lock.records)} lock_sha256={lock.lock_sha256} output={args.output}")


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
