#!/usr/bin/env python3
"""Extract and seal four pre-decision BLS Consumer Price Index headline facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finreplay.engines import TimeVault
from finreplay.scenarios import BLS_CPI_SOURCE_ID, CPIBoundaryInputLock

ROLE_POINTS = {
    "december_monthly": ("2023-01-12", "all_items_monthly_change_seasonally_adjusted"),
    "december_yoy": (
        "2023-01-12",
        "all_items_12_month_change_not_seasonally_adjusted",
    ),
    "january_monthly": ("2023-02-14", "all_items_monthly_change_seasonally_adjusted"),
    "january_yoy": (
        "2023-02-14",
        "all_items_12_month_change_not_seasonally_adjusted",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", default="bls-2023-cpi-release-boundary")
    parser.add_argument("--scenario-version", default="1.0.0")
    parser.add_argument("--replay-id", default="bls-2023-cpi-release-boundary-v1")
    parser.add_argument("--artifact-prefix", default="bls-cpi")
    parser.add_argument(
        "--title",
        default="BLS CPI-U all-items release boundary, early 2023",
    )
    parser.add_argument(
        "--decision-time",
        type=_aware_datetime,
        default=_aware_datetime("2023-02-15T12:00:00Z"),
    )
    parser.add_argument(
        "--build-epoch",
        type=_aware_datetime,
        default=_aware_datetime("2026-08-13T05:20:00Z"),
    )
    parser.add_argument(
        "--timevault",
        type=Path,
        default=Path("data/silver/supporting/bls-cpi.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scenarios/bls-cpi-2023/input-lock.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with TimeVault(args.timevault) as vault:
        candidates = vault.records_as_of(
            args.decision_time,
            source_ids=[BLS_CPI_SOURCE_ID],
        )
    by_point = {
        (str(record.payload.get("release_date")), str(record.payload.get("metric"))): record
        for record in candidates
        if record.entity_id == "bls_cpi_u_all_items:united_states"
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
    lock = CPIBoundaryInputLock.create(
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
                "This lock preserves four minimal headline facts from the January 12 and February "
                "14, 2023 archived BLS Consumer Price Index releases that were explicitly "
                "available before the replay decision time. It excludes the March 14 release, "
                "later revised values, all detailed tables, micro price-quote or item-level "
                "records, a probability distribution, forecast, calibrated interval, causal "
                "attribution, or investment and policy performance. The February release "
                "documents annual weight updates and recalculation of the previous five years of "
                "seasonally adjusted indexes, limiting cross-release comparability."
            ),
        }
    )
    content = (lock.model_dump_json(indent=2) + "\n").encode()
    if args.output.exists() and args.output.read_bytes() != content:
        raise SystemExit("CPI release boundary input-lock destination contains different bytes")
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
