#!/usr/bin/env python3
"""Extract and seal four pre-decision BLS Employment Situation headline facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import BLS_EMPLOYMENT_SOURCE_ID, EmploymentBoundaryInputLock

ROLE_POINTS = {
    "december_payroll": ("2023-01-06", "nonfarm_payroll_change"),
    "december_unemployment": ("2023-01-06", "unemployment_rate"),
    "january_payroll": ("2023-02-03", "nonfarm_payroll_change"),
    "january_unemployment": ("2023-02-03", "unemployment_rate"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="bls-2023-payroll-release-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", default="bls-2023-payroll-release-boundary-v1")
    parser.add_argument("--artifact-prefix", default="bls-payroll")
    parser.add_argument(
        "--title",
        default="BLS Employment Situation payroll headline boundary, early 2023",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-02-04T12:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T05:00:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/bls-employment.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/bls-payroll-2023/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[BLS_EMPLOYMENT_SOURCE_ID],
        )
    by_point = {
        (str(record.payload.get("release_date")), str(record.payload.get("metric"))): record
        for record in candidates
        if record.entity_id == "bls_employment_situation:united_states"
    }
    missing = sorted(point for point in ROLE_POINTS.values() if point not in by_point)
    if missing:
        raise SystemExit(f"required BLS headline facts are absent before decision: {missing}")
    roles = {role: by_point[point].record_id for role, point in ROLE_POINTS.items()}
    records = tuple(
        sorted(
            (by_point[point] for point in ROLE_POINTS.values()),
            key=lambda record: record.record_id,
        )
    )
    lock = EmploymentBoundaryInputLock.create(
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
                "This lock preserves four minimal headline facts from the January 6 and February "
                "3, 2023 archived BLS Employment Situation releases that were explicitly available "
                "before the replay decision time. It excludes the March 10 release, later revised "
                "series values, all detailed tables, worker- or employer-level records, a "
                "probability distribution, forecast, calibrated interval, causal attribution, or "
                "investment and policy performance. The January release documents annual "
                "benchmarking and seasonal-factor updates, limiting cross-release comparability."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("employment boundary input-lock destination contains different bytes")
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
