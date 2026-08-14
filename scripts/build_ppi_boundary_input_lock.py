#!/usr/bin/env python3
"""Extract and seal the two paired-format BLS PPI releases known at decision time."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import BLS_PPI_SOURCE_ID, PPIBoundaryInputLock

ROLE_MONTHS = {
    "february_release": "2020-02",
    "march_decision_release": "2020-03",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario-id",
        default="bls-ppi-2020-final-demand-change-boundary",
    )
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument(
        "--replay-id",
        default="bls-ppi-2020-final-demand-change-boundary-v1",
    )
    parser.add_argument("--artifact-prefix", default="bls-ppi")
    parser.add_argument(
        "--title",
        default="BLS PPI final-demand monthly-change boundary before April 2020",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2020-04-09T12:30:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-14T08:40:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/bls-ppi.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/bls-ppi-2020/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[BLS_PPI_SOURCE_ID],
        )
    by_month = {
        str(record.payload.get("reference_month")): record
        for record in candidates
        if record.entity_id == "bls_ppi:final_demand_united_states"
        and record.payload.get("metric")
        == "final_demand_monthly_change_seasonally_adjusted"
    }
    missing = sorted(month for month in ROLE_MONTHS.values() if month not in by_month)
    if missing:
        raise SystemExit(f"required BLS PPI release facts are absent: {missing}")
    roles = {role: by_month[month].record_id for role, month in ROLE_MONTHS.items()}
    records = tuple(
        sorted(
            (by_month[month] for month in ROLE_MONTHS.values()),
            key=lambda record: record.record_id,
        )
    )
    source_hashes = {
        value
        for record in records
        for value in (
            record.source.sha256,
            record.payload.get("release_html_sha256"),
        )
        if isinstance(value, str)
    }
    lock = PPIBoundaryInputLock.create(
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
                "This lock preserves the February and March 2020 BLS final-demand monthly "
                "changes knowable at the April 9 embargo end and binds each archived HTML/PDF "
                "pair. It excludes the April event, current revised values, product or "
                "establishment observations, transactions, quantities, revenues, profits, "
                "consumer prices, a probability distribution, forecast, calibrated interval, "
                "pandemic or price-setting causality, recommendation, execution, investment "
                "performance, external review, deployment, and user impact."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("BLS PPI input-lock destination has different bytes")
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
