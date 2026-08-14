#!/usr/bin/env python3
"""Extract and seal two pre-decision original EIA WNGSR stock records."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import EIA_WNGSR_SOURCE_ID, WorkingGasStockBoundaryInputLock

ROLE_WEEKS = {
    "march06_release": "2020-03-06",
    "march13_decision_release": "2020-03-13",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="eia-wngsr-2020-working-gas-stock-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="eia-wngsr-2020-working-gas-stock-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="eia-wngsr")
    parser.add_argument(
        "--title",
        default="EIA WNGSR boundary before the March 2020 working-gas stock decline",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-03-19T14:30:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-14T08:10:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/eia-wngsr.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/eia-wngsr-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[EIA_WNGSR_SOURCE_ID],
        )
    by_week = {
        str(record.payload.get("week_ending")): record
        for record in candidates
        if record.entity_id == "eia_series:wngsr_working_gas_lower_48"
        and record.payload.get("metric") == "working_gas_in_underground_storage_lower_48"
    }
    missing = sorted(week for week in ROLE_WEEKS.values() if week not in by_week)
    if missing:
        raise SystemExit(f"required EIA WNGSR facts are absent: {missing}")
    roles = {role: by_week[week].record_id for role, week in ROLE_WEEKS.items()}
    records = tuple(
        sorted(
            (by_week[week] for week in ROLE_WEEKS.values()),
            key=lambda record: record.record_id,
        )
    )
    source_hashes = {
        value
        for record in records
        for value in (
            record.source.sha256,
            record.payload.get("history_workbook_sha256"),
            record.payload.get("performance_evaluation_sha256"),
        )
        if isinstance(value, str)
    }
    lock = WorkingGasStockBoundaryInputLock.create(
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
            "source_response_sha256s": sorted(source_hashes),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves the original March 6 and March 13 Lower 48 working-gas "
                "stock estimates knowable at the March 19 decision boundary. It binds the "
                "official original-estimate revision history, current-history cross-check, "
                "and 2020-22 performance evaluation. It excludes the March 20 event, source "
                "sampling measures as range inputs, facility or operator measurements, direct "
                "injection or withdrawal flows, storage constraints, prices, a probability "
                "distribution, forecast, calibrated interval, pandemic or market causality, "
                "recommendation, execution, investment performance, external review, "
                "deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("working-gas input-lock destination has different bytes")
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
