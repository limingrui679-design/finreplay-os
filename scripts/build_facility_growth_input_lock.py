#!/usr/bin/env python3
"""Extract and seal four pre-decision H.4.1 BTFP facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import FED_H41_BTFP_SOURCE_ID, FacilityGrowthInputLock

ROLE_POINTS = {
    "first_weekly_average": ("2023-03-16", "weekly_average"),
    "first_wednesday": ("2023-03-16", "wednesday_outstanding"),
    "second_weekly_average": ("2023-03-23", "weekly_average"),
    "second_wednesday": ("2023-03-23", "wednesday_outstanding"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="btfp-2023-early-growth-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", default="btfp-2023-early-growth-boundary-v1")
    parser.add_argument("--artifact-prefix", default="btfp-growth")
    parser.add_argument(
        "--title",
        default="Federal Reserve BTFP early weekly growth boundary, March 2023",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-03-25T12:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T04:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/fed-h41.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/btfp-growth-2023/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[FED_H41_BTFP_SOURCE_ID],
        )
    by_point = {
        (str(record.payload.get("release_date")), str(record.payload.get("metric"))): record
        for record in candidates
        if record.payload.get("program") == "Bank Term Funding Program"
    }
    missing = sorted(point for point in ROLE_POINTS.values() if point not in by_point)
    if missing:
        raise SystemExit(f"required H.4.1 BTFP facts are absent before decision: {missing}")
    roles = {role: by_point[point].record_id for role, point in ROLE_POINTS.items()}
    records = tuple(
        sorted(
            (by_point[point] for point in ROLE_POINTS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = FacilityGrowthInputLock.create(
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
            "source_response_sha256s": sorted({record.source.sha256 for record in records}),
            "records": [record.model_dump(mode="json") for record in records],
            "claim_boundary": (
                "This lock preserves four minimal reported BTFP facts from two archived Federal "
                "Reserve H.4.1 releases that were conservatively knowable before the replay "
                "decision time. It excludes the March 30 release and does not contain all H.4.1 "
                "tables, exact intraday publication times, borrower identities, collateral, a "
                "probability distribution, forecast, causal stress attribution, or policy and "
                "investment performance evidence."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("facility growth input-lock destination contains different bytes")
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
