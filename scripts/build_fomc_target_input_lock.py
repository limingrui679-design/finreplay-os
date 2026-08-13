#!/usr/bin/env python3
"""Extract and seal four pre-decision FOMC target-range endpoint facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import FED_FOMC_SOURCE_ID, FOMCTargetBoundaryInputLock

ROLE_POINTS = {
    "february_lower": ("2023-02-01", "target_range_lower"),
    "february_upper": ("2023-02-01", "target_range_upper"),
    "march_lower": ("2023-03-22", "target_range_lower"),
    "march_upper": ("2023-03-22", "target_range_upper"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="fomc-2023-target-range-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", default="fomc-2023-target-range-boundary-v1")
    parser.add_argument("--artifact-prefix", default="fomc-target")
    parser.add_argument(
        "--title",
        default="FOMC federal-funds target-range boundary, spring 2023",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-03-23T12:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T05:30:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/fed-fomc.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/fomc-target-2023/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[FED_FOMC_SOURCE_ID],
        )
    by_point = {
        (str(record.payload.get("release_date")), str(record.payload.get("metric"))): record
        for record in candidates
        if record.entity_id == "fomc_policy:federal_funds_target_range"
    }
    missing = sorted(point for point in ROLE_POINTS.values() if point not in by_point)
    if missing:
        raise SystemExit(f"required FOMC target facts are absent before decision: {missing}")
    roles = {role: by_point[point].record_id for role, point in ROLE_POINTS.items()}
    records = tuple(
        sorted(
            (by_point[point] for point in ROLE_POINTS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = FOMCTargetBoundaryInputLock.create(
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
                "This lock preserves four minimal federal-funds target endpoints from the February "
                "1 and March 22, 2023 archived FOMC statements that were explicitly available "
                "before the decision time. It excludes the May 3 statement, implementation-note "
                "details, statement language beyond the target facts, market expectations, vote or "
                "macro models, a probability distribution, forecast, calibrated interval, causal "
                "policy-effect claim, recommendation, or investment-performance evidence."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("FOMC target input-lock destination contains different bytes")
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
